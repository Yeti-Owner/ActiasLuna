"""Simulated decoy crowd: a display-only effect for the Moth dashboard.

The point it makes: in a busy room, one person is harder to pick out of a crowd. This adds simulated phones that
wander near the real person. It is cosmetic only:

- It NEVER transmits anything over Wi-Fi and never creates real frames or packets.
- It NEVER touches the raw Wi-Fi estimate, so accuracy numbers are measured without it (like display.py).
- Every decoy is flagged `sim: True` in the snapshot. For the demo the dashboard deliberately draws them as ordinary
  phones (app.js mergeDecoys): private-style MACs, trails and a varying uncertainty, so they pass for real ones.

Toggled over UDP by {"t": "crowd", "on": 0|1} or {"t": "crowd", "toggle": 1}; the Uno Q white button sends the toggle
through tools/unoq_button_adb.py.

Standard library only. Stepped from the tracker with the shared timestamp, never the wall clock, so replays match.
"""

import enum
import math
import random

from room import as_room

DEFAULT_COUNT = 8        # decoys when the crowd is on
SPEED = 0.7              # m/s, a slow indoor walk
TURN = 0.6              # rad/s, how sharply a decoy changes direction
EDGE_MARGIN = 0.3        # m, keep decoys this far inside the room
CLUSTER_R = 1.4          # m, decoys gather within this radius of the real person so the person blends in
CLUSTER_STEER = 0.55     # how strongly a stray decoy turns back toward the person (0..1)
# Trails and uncertainty shaped like tracker.py's (TRAIL_STEP_S) so decoys read as tracked phones.
TRAIL_STEP_S = 0.5
TRAIL_KEEP_S = 60.0
UNC_M = (0.3, 0.55)      # each decoy's typical uncertainty radius (a tracked phone is ~0.3-0.6 m)
UNC_WOBBLE_M = 0.08
UNC_PERIOD_S = (6.0, 14.0)


def private_mac(rng):
    """Random MAC with the locally-administered bit set, like a phone's private Wi-Fi address (12 hex, no colons)."""
    first = (rng.getrandbits(8) & 0xFC) | 0x02
    return f"{first:02x}{rng.getrandbits(40):010x}"


class CrowdState(enum.Enum):
    OFF = "off"
    ON = "on"


class Decoy:
    def __init__(self, idx, w, d, rng):
        self.mac = private_mac(rng)
        self.x = rng.uniform(EDGE_MARGIN, max(w - EDGE_MARGIN, EDGE_MARGIN))
        self.y = rng.uniform(EDGE_MARGIN, max(d - EDGE_MARGIN, EDGE_MARGIN))
        self.heading = rng.uniform(-math.pi, math.pi)
        self.unc_base = rng.uniform(*UNC_M)
        self.unc_w = 2 * math.pi / rng.uniform(*UNC_PERIOD_S)
        self.unc_phase = rng.uniform(0, 2 * math.pi)
        self.unc = self.unc_base
        self.trail = []          # [t, x, y], every TRAIL_STEP_S
        self._rng = rng

    def step(self, dt, w, d, room=None, center=None):
        prev_x, prev_y = self.x, self.y
        # When there's a real person to hide, a stray decoy turns back toward them so the crowd stays around the
        # person (move with the crowd -> you stay lost in it). Otherwise it just wanders.
        if center is not None and math.hypot(self.x - center[0], self.y - center[1]) > CLUSTER_R:
            toward = math.atan2(center[1] - self.y, center[0] - self.x)
            self.heading += CLUSTER_STEER * math.atan2(math.sin(toward - self.heading), math.cos(toward - self.heading))
        else:
            self.heading += self._rng.uniform(-TURN, TURN) * dt
        self.x += math.cos(self.heading) * SPEED * dt
        self.y += math.sin(self.heading) * SPEED * dt
        # Bounce off the bounding walls so decoys stay in the room.
        lo, hiw, hid = EDGE_MARGIN, max(w - EDGE_MARGIN, EDGE_MARGIN), max(d - EDGE_MARGIN, EDGE_MARGIN)
        if self.x < lo or self.x > hiw:
            self.x = min(max(self.x, lo), hiw)
            self.heading = math.pi - self.heading
        if self.y < lo or self.y > hid:
            self.y = min(max(self.y, lo), hid)
            self.heading = -self.heading
        # With a real room, also stay inside its shape and off the furniture: undo the step and turn away.
        if room is not None and not room.ok(self.x, self.y):
            self.x, self.y = prev_x, prev_y
            self.heading += math.pi / 2 + self._rng.uniform(-0.3, 0.3)


class Crowd:
    """Owns the decoy phones. Call set(on), then tick(dt, ...) each frame, then snapshot()."""

    def __init__(self, count=DEFAULT_COUNT, seed=1):
        self.state = CrowdState.OFF
        self.count = count
        self._rng = random.Random(seed)
        self._decoys = []
        self._last_t = None
        self._center = None       # last known position of the real person the crowd is hiding

    def set(self, on):
        """The one transition: OFF <-> ON. Accepts a bool, 0/1, or a CrowdState."""
        new = CrowdState.ON if (on is True or on == 1 or on is CrowdState.ON or on == "on") else CrowdState.OFF
        self.state = new

    def toggle(self):
        self.set(self.state is CrowdState.OFF)

    def tick(self, t, room, center=None):
        """Advance to time t. `room`: a Room, or a (w, d) tuple for a plain rectangle. center: optional (x, y)
        to gather the decoys around (e.g. the real person)."""
        rm = as_room(room)
        w, d = rm.w, rm.d
        if self.state is CrowdState.OFF:
            self._decoys = []
            self._last_t = t
            self._center = None
            return
        if not self._decoys:
            self._decoys = [Decoy(i, w, d, self._rng) for i in range(self.count)]
            for dec in self._decoys:   # place each at a spot a person could actually stand
                spot = None
                if center:
                    for _ in range(20):   # try to gather near the real person first
                        cx = min(max(center[0] + self._rng.uniform(-1.2, 1.2), EDGE_MARGIN), max(w - EDGE_MARGIN, EDGE_MARGIN))
                        cy = min(max(center[1] + self._rng.uniform(-1.2, 1.2), EDGE_MARGIN), max(d - EDGE_MARGIN, EDGE_MARGIN))
                        if rm.ok(cx, cy):
                            spot = (cx, cy)
                            break
                dec.x, dec.y = spot or rm.random_point(self._rng)
        dt = 0.0 if self._last_t is None else max(0.0, min(t - self._last_t, 1.0))
        self._last_t = t
        # If the real person moved, carry the whole crowd along with them, so they keep surrounding the person
        # instead of being left behind. Then each decoy wanders/steers within the cluster.
        if center is not None and self._center is not None:
            shift_x, shift_y = center[0] - self._center[0], center[1] - self._center[1]
            if abs(shift_x) < 2.0 and abs(shift_y) < 2.0:   # ignore teleport-sized jumps (bad estimates)
                for dec in self._decoys:
                    dec.x += shift_x
                    dec.y += shift_y
        self._center = center
        for dec in self._decoys:
            dec.step(dt, w, d, room=rm, center=center)
            dec.unc = dec.unc_base + UNC_WOBBLE_M * math.sin(dec.unc_w * t + dec.unc_phase)
            if not dec.trail or t - dec.trail[-1][0] >= TRAIL_STEP_S:
                dec.trail.append([t, dec.x, dec.y])
            while dec.trail and t - dec.trail[0][0] > TRAIL_KEEP_S:
                dec.trail.pop(0)

    def snapshot(self):
        """Decoys for the snapshot (empty when off), each flagged sim:True. Metres in the crowd's 0..w / 0..d frame."""
        return [{"mac": dec.mac, "x": round(dec.x, 2), "y": round(dec.y, 2), "unc": round(dec.unc, 3),
                 "trail": [[round(p[0], 2), round(p[1], 3), round(p[2], 3)] for p in dec.trail], "sim": True}
                for dec in self._decoys]

    def handle_packet(self, p):
        """If p is a crowd button message, apply it and return True; else False."""
        if p.get("t") == "crowd":
            # {"toggle": 1} flips whatever the state is now, so a button stays in step with the web toggle.
            if p.get("toggle"):
                self.toggle()
            else:
                self.set(p.get("on"))
            return True
        return False
