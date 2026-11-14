"""Display-only output: the smoothed dot and the simulated demo crowd.

Nothing here feeds estimates or accuracy. The raw estimate stays in est["raw"]; accuracy figures (calibration LOO,
checks, lab) use Phone.raw, so smoothing cannot flatter them. The crowd (the crowd button) is stepped from
Tracker.tick with packet time, so replays reproduce it exactly.
"""
from __future__ import annotations

import math
import random
from typing import List, Optional

import radiomap as rmap

SMOOTH_MIN_CUTOFF_HZ = 0.3
SMOOTH_BETA = 2.0
SMOOTH_D_CUTOFF_HZ = 1.0
SMOOTH_UNC_ALPHA = 0.15
SMOOTH_RESET_GAP_S = 10.0
SMOOTH_LEASH_M = 0.15


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

    def __call__(self, x: float, y: float, unc: float, t: float, leash_scale: float = 1.0):
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
        slack = SMOOTH_LEASH_M * leash_scale
        if gap > slack:
            pull = (gap - slack) / gap
            self.shown_x += pull * (self.fx - self.shown_x)
            self.shown_y += pull * (self.fy - self.shown_y)
        return self.shown_x, self.shown_y, self.unc


CROWD_N = 30
CROWD_SPEED = (0.35, 1.1)
CROWD_PERSONAL_M = 0.45
CROWD_HOTSPOTS = 3
CROWD_HOTSPOT_MOVE_S = (15.0, 30.0)
CROWD_HOTSPOT_SPREAD_M = 0.7
CROWD_WANDER_P = 0.3
CROWD_DWELL_S = (1.0, 6.0)
CROWD_TRAIL_STEP_S = 0.5
CROWD_TRAIL_N = 8
CROWD_ARRIVE_M = 0.15


def _free(x, y, room, objects) -> bool:
    if not rmap.point_in_poly(x, y, room):
        return False
    return not any(rmap.point_in_poly(x, y, obj) for obj in objects)


class Crowd:
    def __init__(self, seed: int = 7):
        self.rng = random.Random(seed)
        self.on = False
        self.t: Optional[float] = None
        self.people: List[dict] = []
        self.hotspots: List[list] = []

    def set(self, on: bool):
        if on and not self.on:
            self.people, self.hotspots, self.t = [], [], None
        self.on = on

    def _random_point(self, room, objects):
        xs = [p[0] for p in room]
        ys = [p[1] for p in room]
        for _ in range(200):
            x = self.rng.uniform(min(xs), max(xs))
            y = self.rng.uniform(min(ys), max(ys))
            if _free(x, y, room, objects):
                return x, y
        return sum(xs) / len(xs), sum(ys) / len(ys)

    def _near_hotspot(self, room, objects):
        hx, hy, _ = self.rng.choice(self.hotspots)
        for _ in range(20):
            x = hx + self.rng.gauss(0, CROWD_HOTSPOT_SPREAD_M)
            y = hy + self.rng.gauss(0, CROWD_HOTSPOT_SPREAD_M)
            if _free(x, y, room, objects):
                return x, y
        return hx, hy

    def _retarget(self, person, room, objects):
        if not self.hotspots or self.rng.random() < CROWD_WANDER_P:
            person["tx"], person["ty"] = self._random_point(room, objects)
        else:
            person["tx"], person["ty"] = self._near_hotspot(room, objects)
        person["wait"] = None

    def _spawn(self, t, room, objects):
        x, y = self._random_point(room, objects)
        # Random locally administered unicast MAC, like a phone's private Wi-Fi address.
        first = (self.rng.getrandbits(8) & 0xFC) | 0x02
        person = {"x": x, "y": y, "v": self.rng.uniform(*CROWD_SPEED), "trail": [], "trail_t": 0.0,
                  "mac": f"{first:02x}{self.rng.getrandbits(40):010x}"}
        self._retarget(person, room, objects)
        return person

    def _velocity(self, person, t, room, objects):
        dx, dy = person["tx"] - person["x"], person["ty"] - person["y"]
        dist = math.hypot(dx, dy)
        if dist >= CROWD_ARRIVE_M:
            return dx / dist * person["v"], dy / dist * person["v"]
        if person["wait"] is None:
            person["wait"] = t + self.rng.uniform(*CROWD_DWELL_S)
        elif t >= person["wait"]:
            self._retarget(person, room, objects)
        return 0.0, 0.0

    def step(self, t: float, room, objects):
        if not self.on or not room or len(room) < 3:
            return
        dt = 0.0 if self.t is None else max(0.0, min(0.5, t - self.t))
        self.t = t
        for spot in self.hotspots:
            if t >= spot[2]:
                spot[0], spot[1] = self._random_point(room, objects)
                spot[2] = t + self.rng.uniform(*CROWD_HOTSPOT_MOVE_S)
        while len(self.hotspots) < CROWD_HOTSPOTS:
            x, y = self._random_point(room, objects)
            self.hotspots.append([x, y, t + self.rng.uniform(*CROWD_HOTSPOT_MOVE_S)])
        while len(self.people) < CROWD_N:
            self.people.append(self._spawn(t, room, objects))

        for person in self.people:
            vx, vy = self._velocity(person, t, room, objects)
            for other in self.people:
                if other is person:
                    continue
                ex, ey = person["x"] - other["x"], person["y"] - other["y"]
                gap = math.hypot(ex, ey)
                if 1e-6 < gap < CROWD_PERSONAL_M:
                    push = 0.8 * (CROWD_PERSONAL_M - gap) / CROWD_PERSONAL_M
                    vx += ex / gap * push
                    vy += ey / gap * push
            vx += self.rng.gauss(0, 0.08)
            vy += self.rng.gauss(0, 0.08)
            nx, ny = person["x"] + vx * dt, person["y"] + vy * dt
            if _free(nx, ny, room, objects):
                person["x"], person["y"] = nx, ny
            elif dt > 0:
                self._retarget(person, room, objects)
            if t - person["trail_t"] >= CROWD_TRAIL_STEP_S:
                point = [round(t, 2), round(person["x"], 3), round(person["y"], 3)]
                person["trail"] = (person["trail"] + [point])[-CROWD_TRAIL_N:]
                person["trail_t"] = t

    def snapshot(self) -> Optional[dict]:
        if not self.on:
            return None
        people = [{"mac": p["mac"], "x": round(p["x"], 3), "y": round(p["y"], 3), "trail": p["trail"]}
                  for p in self.people]
        return {"on": True, "people": people}
