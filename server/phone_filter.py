"""Per-phone time filter on radiomap's grid.

A hidden-Markov forward filter whose motion model comes from the phone's own sensors (still / steps + compass /
unknown), plus a fixed-lag backward pass that cleans the recent trail. Design reasons:

- A standing phone sits in ONE multipath fade, so re-reading it adds no information. A dwell is therefore one
  measurement (its running mean) applied to the belief held at the stop, not multiplied in again every tick.
- While walking, readings decorrelate every few cm and steps + compass say how the position changed between them.
  That chain is what lets tracking beat a single snapshot.

tracker.Tracker owns sample storage and decides mv / steps / facing; this module owns the belief, the phone offset and
smoothing.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Callable, NamedTuple, Optional

import numpy as np

import radiomap as rmap

# Information rate while moving: fraction of a full likelihood per second of new readings (successive windows share
# the map's local error, so they are not independent).
K_RATE = 1.2
MOVING_SPEED = 1.0
UNKNOWN_SPEED = 0.6
# Standing still: the belief held at the stop spreads by SD_STILL * sqrt(seconds) (drift, small shuffles) and is
# multiplied once by the likelihood of the dwell's readings over at most STILL_WIN_S.
SD_STILL = 0.08
STILL_WIN_S = 6.0
STILL_MAX_S = 60.0
# A long dwell is worth a bit more than one reading (hands sway, the room changes): its weight grows by one per
# STILL_GAIN_S, so a wrong fix held at the stop is eventually overruled by what the boards hear.
STILL_GAIN_S = 15.0
# "Still" is only the phone's claim (slow walking can stay under its threshold). If board readings drift apart by
# STILL_BREAK_DB since the dwell began, the dwell restarts from the current belief spread by BREAK_SD_M.
STILL_BREAK_DB = 8.0
STILL_BREAK_S = 1.5
BREAK_SD_M = 0.4
# Second dwell check: if the readings are e^LOST_NATS times less likely over the whole held belief than at the room's
# best cell, the belief is in the wrong place (carried smoothly, or a bad fix before stopping). LOST_MIX of it is
# spread over the room so the readings can choose again.
LOST_NATS = 6.0
LOST_MIX = 0.3
# Steps + compass: each step moves the belief `stride` along the facing, adding PDR_SD_PER_M spread per metre.
# "Moving" with no step for NOSTEP_S (slow walk below the step threshold, or only the radio noticed) creeps at NOSTEP_V
# along the facing: slower than a walk, so a false alarm costs little.
PDR_SD_PER_M = 0.35
PDR_SD_MIN = 0.06
PDR_MAX_STEPS = 6
NOSTEP_S = 1.0
NOSTEP_V = 0.35
MIX = 0.01
# After GAP_S without a tick (screen off, out of range) the phone may have been carried anywhere nearby.
GAP_S = 3.0
GAP_SD_MAX_M = 4.0
# While not still, readings are folded in every MEAS_S over everything since the last fold (boards report every
# 0.25 s, not in step with the tick). A slow phone accumulates up to WINDOW_MAX_S until two boards have MIN_FRAMES.
MEAS_S = 0.5
WINDOW_MAX_S = 6.0
# Phone offset (tx power, case, grip): starts unknown (rmap.DELTA_SD), then follows the posterior-mean offset with time
# constant OFF_TAU_S while its prior tightens to OFF_SD_MIN. The solved offset is already pulled about halfway to the
# prior, so one still landing OFF_JUMP sd away means the phone changed (pocketed, regripped): the prior re-opens fully
# and, being wide, re-learns fast. Offset and "far from every board" look alike, so learning from a vague fix would
# feed on itself: the offset only moves while the belief's spread is under OFF_LEARN_UNC_M.
OFF_TAU_S = 20.0
OFF_SD_MIN = 5.0
OFF_JUMP = 1.5
OFF_LEARN_UNC_M = 1.0
SMOOTH_LAG_S = 3.0
# A tapped position is good to about a step either way (finger on a small map, where exactly the phone is held).
PIN_SD_M = 0.25

Window = Callable[[float, float], "rmap.Live"]


class Move(NamedTuple):
    """How one belief became the next: shift by (dx, dy) when `shifted`, then diffuse by sd."""
    shifted: bool
    dx: float
    dy: float
    sd: float


def _forward(grid, b, move: Move):
    if move.shifted:
        return grid.diffuse(grid.shift(b, move.dx, move.dy), move.sd)
    return grid.diffuse(b, move.sd)


def _backward(grid, b, move: Move):
    if move.shifted:
        return grid.shift_adj(grid.diffuse_adj(b, move.sd), move.dx, move.dy)
    return grid.diffuse_adj(b, move.sd)


class Track:
    def __init__(self):
        self.belief: Optional[np.ndarray] = None
        self.t: Optional[float] = None
        self.meas_t: Optional[float] = None
        self.off = 0.0
        self.off_sd = rmap.DELTA_SD
        self.still_since: Optional[float] = None
        self.anchor: Optional[np.ndarray] = None
        self.last_step_t: Optional[float] = None
        # Per tick: (t, filtered belief, move, factor) with belief_k ~ move_k(belief_{k-1}) * factor_k. Feeds smoothed().
        self.hist: deque = deque()

    def reset(self):
        self.__init__()

    def pin(self, m: "rmap.RadioMap", x: float, y: float, t: float, sd: float = PIN_SD_M):
        """The holder said where they stand: restart the belief as a PIN_SD_M blob there. The learned phone offset
        is kept; the dwell and the smoothing history restart, so the trail never smooths across the jump."""
        grid = m.grid
        if grid is None:
            return
        b = np.exp(-0.5 * ((grid.cx - x) ** 2 + (grid.cy - y) ** 2) / sd ** 2)
        if b.sum() <= 0:
            return
        self.belief = b / b.sum()
        self.t = self.meas_t = t
        self.still_since = self.anchor = None
        self._restart_history(t)

    def _restart_history(self, t: float):
        self.hist.clear()
        self.hist.append((t, self.belief, None, None))

    def _dwell_broken(self, window: Window, t: float) -> bool:
        if t - self.still_since < 2 * STILL_BREAK_S:
            return False
        start = window(self.still_since, self.still_since + STILL_BREAK_S)
        now = window(t - STILL_BREAK_S, t)
        enough = rmap.MIN_FRAMES
        changes = [now[n][0] - start[n][0] for n in start
                   if n in now and start[n][1] >= enough and now[n][1] >= enough]
        if len(changes) < 2:
            return False
        middle = sorted(changes)[len(changes) // 2]
        return max(abs(c - middle) for c in changes) > STILL_BREAK_DB

    def _walking_move(self, t: float, dt: float, mv: int, steps: int, facing, stride: float, pdr: bool) -> Move:
        if steps > 0:
            self.last_step_t = t
        creeping = mv == 1 and steps <= 0 and (self.last_step_t is None or t - self.last_step_t > NOSTEP_S)
        if steps > 0:
            dist = min(PDR_MAX_STEPS, steps) * stride
        elif creeping:
            dist = NOSTEP_V * dt
        else:
            dist = 0.0
        if mv == 1 and pdr and facing is not None:
            return Move(True, dist * math.cos(facing), dist * math.sin(facing), max(PDR_SD_MIN, PDR_SD_PER_M * dist))
        return Move(False, 0.0, 0.0, (MOVING_SPEED if mv == 1 else UNKNOWN_SPEED) * dt)

    def step(self, m: "rmap.RadioMap", t: float, window: Window, mv: int = -1, steps: int = 0,
             facing: Optional[float] = None, stride: float = 0.7, pdr: bool = True, shadow: bool = False,
             strength: float = 1.0, extra=None, holder_facing: Optional[float] = None) -> Optional[dict]:
        """Advance one tick and return radiomap's summary of the filtered belief (None before the first fix).

        window(t0, t1): this phone's readings in (t0, t1]. mv: 0 still, 1 moving, -1 unknown. steps: new steps since
        the last tick. facing: map angle walked towards, or None; pdr=False ignores steps. extra: another per-cell
        log-likelihood on the same grid (sensing.DFMap). strength scales this update's weight.
        """
        grid = m.grid
        if grid is None:
            return None
        if self.belief is not None and len(self.belief) != len(m.cells):
            self.reset()
        first = self.t is None
        gap = 0.0 if first else t - self.t
        dt = 0.25 if first else min(max(gap, 0.05), 2.0)
        self.t = t
        if gap > GAP_S and self.belief is not None:
            self.belief = grid.diffuse(self.belief, min(GAP_SD_MAX_M, UNKNOWN_SPEED * gap))
            self.still_since = self.anchor = self.meas_t = None
            self._restart_history(t)

        still = mv == 0
        if still and self.still_since is not None and self._dwell_broken(window, t):
            self.still_since, self.anchor = t - dt, None
            if self.belief is not None:
                self.belief = grid.diffuse(self.belief, BREAK_SD_M)
        if still:
            if self.still_since is None:
                self.still_since, self.anchor = t - dt, None
            move = Move(False, 0.0, 0.0, SD_STILL * math.sqrt(dt))
            live = window(max(self.still_since, t - STILL_WIN_S), t)
        else:
            self.still_since = self.anchor = None
            move = self._walking_move(t, dt, mv, steps, facing, stride, pdr)
            recent = self.meas_t is not None and t - self.meas_t <= WINDOW_MAX_S
            t0 = self.meas_t if recent else t - WINDOW_MAX_S
            due = self.belief is None or t - t0 >= MEAS_S - 1e-6
            live = window(t0, t) if due else {}

        fit = m.loglik_off(live, shadow, holder_facing, self.off, self.off_sd)
        if self.belief is None:
            if fit is None:
                return None
            ll = fit[0] if extra is None else fit[0] + np.asarray(extra, float)
            self.belief = rmap.posterior(None, ll, 1.0, m.prior)
            self.meas_t = t
            self._restart_history(t)
            return m.summary(self.belief)

        prior = _forward(grid, self.belief, move)
        if fit is None:
            post = prior
        else:
            ll = fit[0]
            if extra is not None and len(extra) == len(ll):
                ll = ll + np.asarray(extra, float)
            if still:
                post = self._dwell_update(m, grid, prior, ll, t, dt, strength)
            else:
                span = min(2.0, max(dt, t - self.meas_t if self.meas_t is not None else dt))
                post = rmap.posterior(prior, ll, min(1.0, K_RATE * span) * strength, m.prior)
            self.meas_t = t
            post = (1.0 - MIX) * post + MIX / len(post)
            if len(m.usable(live)) >= 3 and m.summary(post)["unc"] < OFF_LEARN_UNC_M:
                self._learn_offset(float(post @ fit[1]), dt)

        self.hist.append((t, post, move, post / np.maximum(prior, 1e-12)))
        while self.hist and t - self.hist[0][0] > SMOOTH_LAG_S + 1e-6:
            self.hist.popleft()
        self.belief = post
        return m.summary(post)

    def _dwell_update(self, m, grid, prior, ll, t: float, dt: float, strength: float):
        if self.anchor is None:
            self.anchor = prior
        held_s = min(STILL_MAX_S, t - self.still_since)
        held = grid.diffuse(self.anchor, SD_STILL * math.sqrt(held_s))
        if held_s >= STILL_BREAK_S and float(held @ np.exp(ll - ll.max())) < math.exp(-LOST_NATS):
            self.anchor = (1.0 - LOST_MIX) * held + LOST_MIX / len(held)
            self.still_since = t - dt
            held = self.anchor
        return rmap.posterior(held, ll, strength * (1.0 + held_s / STILL_GAIN_S), m.prior)

    def _learn_offset(self, solved: float, dt: float):
        if abs(solved - self.off) > OFF_JUMP * self.off_sd:
            self.off_sd = rmap.DELTA_SD
        gain = min(0.25, dt / OFF_TAU_S * (self.off_sd / OFF_SD_MIN) ** 2)
        self.off += gain * (solved - self.off)
        self.off_sd = max(OFF_SD_MIN, self.off_sd * math.exp(-dt / OFF_TAU_S))

    def smoothed(self, m: "rmap.RadioMap") -> list:
        """Backward pass over the last SMOOTH_LAG_S: [(t, x, y, unc)] oldest first, each also using later readings.

        The oldest entry is final; newer ones keep improving on later calls. Display / trail only.
        """
        grid = m.grid
        hist = list(self.hist)
        if grid is None or not hist or len(hist[-1][1]) != len(m.cells):
            return []
        beta = np.ones(len(m.cells))
        out = []
        for t, alpha, move, factor in reversed(hist):
            gamma = alpha * beta
            total = gamma.sum()
            if total > 0:
                est = m.summary(gamma / total)
                out.append((t, est["x"], est["y"], est["unc"]))
            if move is None:
                break
            beta = _backward(grid, factor * beta, move)
            peak = beta.max()
            if peak <= 0:
                break
            beta = beta / peak
        out.reverse()
        return out
