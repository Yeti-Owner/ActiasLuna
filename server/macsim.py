"""Honest, software-only demo of how fake and randomized MAC addresses defeat Wi-Fi tracking.

Moth follows a phone by its MAC address. Two well-known privacy techniques break that link, and this module
SIMULATES both for the demo. It is a teaching / display effect only:

- It NEVER transmits anything over the air and forges no real Wi-Fi frames.
- It NEVER touches the real Wi-Fi estimate (accuracy numbers stay honest).
- Every address it makes is an obviously-fake, locally-administered MAC flagged `sim: True`, so the UI labels it.

Technique 1 - decoys: many fake phones appear at once, so the real person is just one address among many
(this reuses the decoy motion from crowd.py).
Technique 2 - randomization: the target changes its own MAC every few seconds, so its trail keeps breaking and
can't be followed over time. This is exactly what modern iOS/Android do ("Private Wi-Fi Address").

Standard library only. Stepped with the shared timestamp, never the wall clock, so re-run recordings match.
"""

import random

from crowd import Decoy  # reuse the wandering-dot motion
from room import as_room

# Locally-administered, clearly-fake address blocks (the 0x02 bit marks "not a real vendor MAC").
DECOY_PREFIX = "02:00:DE:C0"   # DE-C0 ~ "decoy"
TARGET_PREFIX = "02:00:5A"     # 5A ~ "spoof/rotating target"
DEFAULT_ROTATE_S = 4.0         # how often the target changes its MAC (0 = randomization off)
LABEL = "Simulated (not real tracking)"


def random_mac(prefix, rng):
    """A full six-octet MAC that starts with `prefix` and fills the rest at random."""
    have = prefix.count(":") + 1
    tail = [f"{rng.randint(0, 255):02X}" for _ in range(6 - have)]
    return ":".join([prefix] + tail)


class MacSim:
    """Owns the simulated target and its decoys.

    tick(t, room) each frame, then snapshot(). `rotate_s = 0` turns randomization off so you can show the
    difference: a steady MAC is easy to follow, a rotating one is not.
    """

    def __init__(self, decoys=8, rotate_s=DEFAULT_ROTATE_S, seed=1):
        self.n_decoys = decoys
        self.rotate_s = rotate_s
        self._rng = random.Random(seed)
        self._decoys = []
        self._target = None
        self._target_mac = None
        self._next_rotate = None
        self._last_t = None
        self.rotations = 0

    def _ensure(self, rm):
        if self._target is None:
            w, d = rm.w, rm.d
            self._target = Decoy(0, w, d, self._rng)
            self._target.x, self._target.y = rm.random_point(self._rng)
            self._target_mac = random_mac(TARGET_PREFIX, self._rng)
            self._next_rotate = self.rotate_s
            self._decoys = [Decoy(i + 1, w, d, self._rng) for i in range(self.n_decoys)]
            for dec in self._decoys:
                dec.mac = random_mac(DECOY_PREFIX, self._rng)
                dec.x, dec.y = rm.random_point(self._rng)

    def tick(self, t, room):
        rm = as_room(room)
        w, d = rm.w, rm.d
        self._ensure(rm)
        dt = 0.0 if self._last_t is None else max(0.0, min(t - self._last_t, 1.0))
        self._last_t = t
        self._target.step(dt, w, d, room=rm)
        for dec in self._decoys:
            dec.step(dt, w, d, room=rm)
        # Rotate the target's MAC on schedule: its trail resets, as if a new device appeared.
        if self.rotate_s > 0 and t >= self._next_rotate:
            self._target_mac = random_mac(TARGET_PREFIX, self._rng)
            self._next_rotate = t + self.rotate_s
            self.rotations += 1

    @property
    def target_mac(self):
        return self._target_mac

    def candidates(self):
        """How many distinct addresses a tracker would have to choose among right now."""
        return len(self._decoys) + (1 if self._target else 0)

    def snapshot(self):
        if self._target is None:
            return None
        return {
            "sim": True,
            "label": LABEL,
            "rotating": self.rotate_s > 0,
            "rotate_s": self.rotate_s,
            "rotations": self.rotations,
            "candidates": self.candidates(),
            "target": {"mac": self._target_mac, "x": round(self._target.x, 2), "y": round(self._target.y, 2)},
            "decoys": [{"mac": dec.mac, "x": round(dec.x, 2), "y": round(dec.y, 2)} for dec in self._decoys],
        }

    def explain(self):
        """One-line takeaway for the demo."""
        if self.rotate_s > 0:
            return (f"Target hides among {len(self._decoys)} decoys AND changes its address every "
                    f"{self.rotate_s:g}s, so its trail only ever links ~{self.rotate_s:g}s before resetting.")
        return (f"Target has a steady address among {len(self._decoys)} decoys: still followable if you can "
                f"pick it out. Turn on randomization to break the trail.")
