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
import hashlib
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
# Fake board readings, fitted to recorded phones (about -41 to -53 dBm across this room, ~4 dB scatter per reading):
# RSSI = RSSI_1M - RSSI_SLOPE * log10(distance) + a fixed per-board offset + per-reading noise. Shaped like
# tracker.Tracker._phone_row's per_node / spark (RATE_S, SPARK_S); sampled every RSSI_STEP_S to keep snapshots small.
RSSI_1M = -40.0
RSSI_SLOPE = 20.0
RSSI_BOARD_SD = 3.0
RSSI_NOISE_SD = 3.5
RSSI_STEP_S = 0.5
FRAMES = (12, 30)        # frames per reading
RATE_S = 5.0
SPARK_S = 30.0
# Jammer: while the crowd is on, real people's displayed dots and trails are pushed around so they are as hard to
# follow as the decoys. Every JAM_HOP_S the offset picks a new target within JAM_R_M, holds, then darts to it over
# the last JAM_DART of the hop. A pure function of (mac, t), so a trail drawn twice moves the same way.
JAM_R_M = 1.0
JAM_HOP_S = 1.6
JAM_DART = 0.3
JAM_JITTER_M = 0.12


def _hop_target(mac, k):
    h = hashlib.blake2b(f"{mac}:{k}".encode(), digest_size=8).digest()
    a = int.from_bytes(h[:4], "big") / 2 ** 32 * 2 * math.pi
    r = JAM_R_M * math.sqrt(int.from_bytes(h[4:], "big") / 2 ** 32)
    return r * math.cos(a), r * math.sin(a)


def jam_offset(mac, t):
    """(dx, dy) metres to add to a real person's displayed position at time t."""
    k = math.floor(t / JAM_HOP_S)
    f = t / JAM_HOP_S - k
    ax, ay = _hop_target(mac, k)
    bx, by = _hop_target(mac, k + 1)
    e = min(1.0, max(0.0, (f - (1.0 - JAM_DART)) / JAM_DART))
    e = e * e * (3 - 2 * e)
    return (ax + (bx - ax) * e + JAM_JITTER_M * math.sin(7.3 * t + len(mac)),
            ay + (by - ay) * e + JAM_JITTER_M * math.sin(5.9 * t + 1.7))


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
        self.ip = f"192.168.4.{rng.randint(20, 240)}"
        self.board_db = {}       # board id -> fixed offset dB (antenna, body, case)
        self.readings = []       # [t, board id, frames, rssi], last SPARK_S
        self._rng = rng

    def hear(self, t, boards):
        """One reading per board from where the decoy stands. boards: {id: (x, y)} in the crowd's frame."""
        rng = self._rng
        for bid, (bx, by) in boards.items():
            off = self.board_db.setdefault(bid, rng.gauss(0.0, RSSI_BOARD_SD))
            dist = max(0.3, math.hypot(self.x - bx, self.y - by))
            rssi = RSSI_1M - RSSI_SLOPE * math.log10(dist) + off + rng.gauss(0.0, RSSI_NOISE_SD)
            self.readings.append([t, bid, rng.randint(*FRAMES), round(rssi)])
        while self.readings and t - self.readings[0][0] > SPARK_S:
            self.readings.pop(0)

    def radio(self, t):
        """per_node and spark in the same shape as a tracked phone's row."""
        per, spark = {}, []
        for rt, bid, n, r in self.readings:
            spark.append([round(rt - t, 2), bid, r])
            if t - rt <= RATE_S:
                acc = per.setdefault(bid, [0, 0.0, rt])
                acc[0] += n
                acc[1] += n * r
                acc[2] = max(acc[2], rt)
        per_node = {b: {"r": round(a[1] / a[0], 1), "rate": round(a[0] / RATE_S, 1), "age": round(t - a[2], 1)}
                    for b, a in per.items()}
        return per_node, spark

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
        self.on_since = None      # tick time the current decoys appeared; the jammer leaves older trail alone
        self._last_heard = None

    def set(self, on):
        """The one transition: OFF <-> ON. Accepts a bool, 0/1, or a CrowdState."""
        new = CrowdState.ON if (on is True or on == 1 or on is CrowdState.ON or on == "on") else CrowdState.OFF
        self.state = new

    def toggle(self):
        self.set(self.state is CrowdState.OFF)

    def tick(self, t, room, center=None, boards=None):
        """Advance to time t. `room`: a Room, or a (w, d) tuple for a plain rectangle. center: optional (x, y)
        to gather the decoys around (e.g. the real person). boards: {id: (x, y)} to fake readings from."""
        rm = as_room(room)
        w, d = rm.w, rm.d
        if self.state is CrowdState.OFF:
            self._decoys = []
            self._last_t = t
            self._center = None
            self.on_since = self._last_heard = None
            return
        if not self._decoys:
            self.on_since = t
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
        if boards and (self._last_heard is None or t - self._last_heard >= RSSI_STEP_S):
            self._last_heard = t
            for dec in self._decoys:
                dec.hear(t, boards)

    def members(self):
        """{mac: ip} of the decoys present now."""
        return {dec.mac: dec.ip for dec in self._decoys}

    def snapshot(self, t=None):
        """Decoys for the snapshot (empty when off), each flagged sim:True. Metres in the crowd's 0..w / 0..d frame."""
        t = self._last_t if t is None else t
        out = []
        for dec in self._decoys:
            per_node, spark = dec.radio(t) if t is not None else ({}, [])
            rssi = [v["r"] for v in per_node.values()]
            out.append({"mac": dec.mac, "x": round(dec.x, 2), "y": round(dec.y, 2), "unc": round(dec.unc, 3),
                        "trail": [[round(p[0], 2), round(p[1], 3), round(p[2], 3)] for p in dec.trail],
                        "ip": dec.ip, "r": round(max(rssi)) if rssi else None, "per_node": per_node, "spark": spark,
                        "sim": True})
        return out

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
