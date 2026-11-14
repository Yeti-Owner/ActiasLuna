"""Simulated decoy crowd: a display-only effect for the Moth dashboard.

The point it makes: in a busy room, one person is harder to pick out of a crowd. To show that honestly, this draws
extra SIMULATED phones that wander near the real person. It is cosmetic only:

- It NEVER transmits anything over Wi-Fi and never creates real frames or packets.
- It NEVER touches the raw Wi-Fi estimate. Accuracy numbers are measured without it (see ARCHITECTURE.md:
  "Raw vs display" and the crowd.py row in the "Display only" section).
- Every decoy is flagged `sim: True` and carries a reserved, clearly-fake MAC, so the UI can label it as simulated.

Toggled by the Uno Q button via {"t": "crowd", "on": 0|1} (firmware/crowd_button + tools/unoq_crowd_relay.py).

Standard library only. Stepped from the tracker with the shared timestamp, never the wall clock, so replays match.
"""

import enum
import math
import random

# Reserved, obviously-fake MAC prefix (locally-administered, documentation-style) so decoys can never be confused
# with a real device and the UI can spot them: "02:00:DE:C0:..".  DE-C0 ~ "decoy".
DECOY_PREFIX = "02:00:DE:C0"
DEFAULT_COUNT = 8        # decoys when the crowd is on
SPEED = 0.7              # m/s, a slow indoor walk
TURN = 0.6              # rad/s, how sharply a decoy changes direction
EDGE_MARGIN = 0.3        # m, keep decoys this far inside the room


class CrowdState(enum.Enum):
    OFF = "off"
    ON = "on"


class Decoy:
    def __init__(self, idx, w, d, rng):
        self.mac = f"{DECOY_PREFIX}:{idx:02X}:{rng.randint(0, 255):02X}"
        self.x = rng.uniform(EDGE_MARGIN, max(w - EDGE_MARGIN, EDGE_MARGIN))
        self.y = rng.uniform(EDGE_MARGIN, max(d - EDGE_MARGIN, EDGE_MARGIN))
        self.heading = rng.uniform(-math.pi, math.pi)
        self._rng = rng

    def step(self, dt, w, d):
        self.heading += self._rng.uniform(-TURN, TURN) * dt
        self.x += math.cos(self.heading) * SPEED * dt
        self.y += math.sin(self.heading) * SPEED * dt
        # Bounce off the walls so decoys stay in the room.
        lo, hiw, hid = EDGE_MARGIN, max(w - EDGE_MARGIN, EDGE_MARGIN), max(d - EDGE_MARGIN, EDGE_MARGIN)
        if self.x < lo or self.x > hiw:
            self.x = min(max(self.x, lo), hiw)
            self.heading = math.pi - self.heading
        if self.y < lo or self.y > hid:
            self.y = min(max(self.y, lo), hid)
            self.heading = -self.heading


class Crowd:
    """Owns the decoy phones. Call set(on), then tick(dt, ...) each frame, then snapshot()."""

    def __init__(self, count=DEFAULT_COUNT, seed=1):
        self.state = CrowdState.OFF
        self.count = count
        self._rng = random.Random(seed)
        self._decoys = []
        self._last_t = None

    def set(self, on):
        """The one transition: OFF <-> ON. Accepts a bool, 0/1, or a CrowdState."""
        new = CrowdState.ON if (on is True or on == 1 or on is CrowdState.ON or on == "on") else CrowdState.OFF
        self.state = new

    def toggle(self):
        self.set(self.state is CrowdState.OFF)

    def tick(self, t, room_size, center=None):
        """Advance to time t. room_size: (w, d) metres. center: optional (x, y) to gather decoys around."""
        w, d = room_size[0], room_size[1]
        if self.state is CrowdState.OFF:
            self._decoys = []
            self._last_t = t
            return
        if not self._decoys:
            self._decoys = [Decoy(i, w, d, self._rng) for i in range(self.count)]
            if center:
                for dec in self._decoys:   # start the crowd near the real person
                    dec.x = min(max(center[0] + self._rng.uniform(-1.2, 1.2), EDGE_MARGIN), max(w - EDGE_MARGIN, EDGE_MARGIN))
                    dec.y = min(max(center[1] + self._rng.uniform(-1.2, 1.2), EDGE_MARGIN), max(d - EDGE_MARGIN, EDGE_MARGIN))
        dt = 0.0 if self._last_t is None else max(0.0, min(t - self._last_t, 1.0))
        self._last_t = t
        for dec in self._decoys:
            dec.step(dt, w, d)

    def snapshot(self):
        """Decoys for the snapshot. Empty when off. Every entry is flagged sim:True for the UI to label."""
        return [{"mac": dec.mac, "x": round(dec.x, 2), "y": round(dec.y, 2), "sim": True}
                for dec in self._decoys]

    def handle_packet(self, p):
        """If p is a crowd button message, apply it and return True; else False."""
        if p.get("t") == "crowd":
            self.set(p.get("on"))
            return True
        return False
