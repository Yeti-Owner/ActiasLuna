"""Display-only output: the smoothed dot.

Nothing here feeds estimates or accuracy. The raw estimate stays in est["raw"]; accuracy figures (calibration LOO,
checks, lab) use Phone.raw, so smoothing cannot flatter them. The decoy crowd (crowd.py) is display-only too.
"""
from __future__ import annotations

import math

SMOOTH_MIN_CUTOFF_HZ = 0.6
SMOOTH_BETA = 4.0
SMOOTH_D_CUTOFF_HZ = 1.0
SMOOTH_UNC_ALPHA = 0.15
SMOOTH_RESET_GAP_S = 10.0
SMOOTH_LEASH_M = 0.05


def _blend(cutoff_hz: float, dt: float) -> float:
    tau = 1.0 / (2.0 * math.pi * cutoff_hz)
    return 1.0 / (1.0 + tau / dt)


class PointSmoother:
    """2-D One-Euro filter followed by a leash.

    One-Euro: strong smoothing at rest (removes jitter), weak smoothing at speed (low lag). Speed is the joint x/y
    speed so both axes share a cutoff and paths don't skew. The leash then keeps the shown point fixed while the
    filtered point stays within SMOOTH_LEASH_M of it, and drags it along once it leaves (hides slow standing drift).
    """

    def __init__(self):
        self.last_t = None
        self.fx = self.fy = 0.0
        self.vx = self.vy = 0.0
        self.unc = 0.0
        self.shown_x = self.shown_y = 0.0

    def _reset(self, x, y, unc, t):
        self.last_t = t
        self.fx, self.fy, self.unc = x, y, unc
        self.vx = self.vy = 0.0
        self.shown_x, self.shown_y = x, y

    def __call__(self, x: float, y: float, unc: float, t: float):
        if self.last_t is None or t <= self.last_t or t - self.last_t > SMOOTH_RESET_GAP_S:
            self._reset(x, y, unc, t)
            return x, y, unc
        dt = t - self.last_t
        self.last_t = t
        k_speed = _blend(SMOOTH_D_CUTOFF_HZ, dt)
        self.vx += k_speed * ((x - self.fx) / dt - self.vx)
        self.vy += k_speed * ((y - self.fy) / dt - self.vy)
        k = _blend(SMOOTH_MIN_CUTOFF_HZ + SMOOTH_BETA * math.hypot(self.vx, self.vy), dt)
        self.fx += k * (x - self.fx)
        self.fy += k * (y - self.fy)
        self.unc += SMOOTH_UNC_ALPHA * (unc - self.unc)

        gap = math.hypot(self.fx - self.shown_x, self.fy - self.shown_y)
        slack = SMOOTH_LEASH_M
        if gap > slack:
            pull = (gap - slack) / gap
            self.shown_x += pull * (self.fx - self.shown_x)
            self.shown_y += pull * (self.fy - self.shown_y)
        return self.shown_x, self.shown_y, self.unc

