"""Load a scanned room so the decoys/MAC phones stand in realistic spots inside it, not in a bare rectangle.

Reads the room file the 3D view uses (data/scan/room.json or similar). Tolerant about key names, and falls back to
a plain rectangle if it can't find walls/furniture, so it never crashes the demo. Metres throughout, x and y on
the floor (same frame as the ESP32 markers and the 3D view).

A point is "ok" to stand on when it is inside the room bounds (minus a margin from the walls) and not on top of a
piece of furniture. That keeps decoys where a person could actually be.
"""

import json
import math

WALL_MARGIN = 0.3      # m, keep people this far off the walls
FURNITURE_PAD = 0.1    # m, grow furniture footprints slightly so people stand beside, not on, them


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class Room:
    def __init__(self, w, d, h=2.6, objects=None, name="room"):
        self.w, self.d, self.h = w, d, h
        self.objects = objects or []   # each: dict with x, y, w, d, yaw (metres, degrees)
        self.name = name

    @property
    def size(self):
        return (self.w, self.d, self.h)

    # ---- loading -------------------------------------------------------------------------------------------------
    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f), name=str(path))

    @classmethod
    def from_dict(cls, data, name="room"):
        """Best-effort parse. Accepts a few common shapes for size/walls/furniture."""
        w = d = h = None

        size = data.get("size") or data.get("dimensions") or data.get("bounds")
        if isinstance(size, (list, tuple)) and len(size) >= 2:
            w, d = _num(size[0]), _num(size[1])
            h = _num(size[2]) if len(size) > 2 else None
        elif isinstance(size, dict):
            w, d, h = _num(size.get("w") or size.get("x")), _num(size.get("d") or size.get("y")), _num(size.get("h"))

        walls = data.get("walls") or []
        if (w is None or d is None) and walls:   # derive bounds from wall endpoints
            xs, ys = [], []
            for s in walls:
                for key in ("a", "b"):
                    pt = s.get(key) if isinstance(s, dict) else None
                    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                        xs.append(_num(pt[0]))
                        ys.append(_num(pt[1]))
            xs = [v for v in xs if v is not None]
            ys = [v for v in ys if v is not None]
            if xs and ys:
                w = w or (max(xs) - min(xs))
                d = d or (max(ys) - min(ys))

        objects = []
        for o in (data.get("objects") or data.get("furniture") or []):
            ox, oy = _num(o.get("x", o.get("cx"))), _num(o.get("y", o.get("cy")))
            ow, od = _num(o.get("w", o.get("width"))), _num(o.get("d", o.get("depth")))
            if None not in (ox, oy, ow, od):
                objects.append({"x": ox, "y": oy, "w": ow, "d": od, "yaw": _num(o.get("yaw")) or 0.0,
                                "kind": o.get("kind", "object")})

        if w is None or d is None or w <= 0 or d <= 0:
            raise ValueError("room file has no usable size or walls; pass a size or use a plain rectangle")
        return cls(w, d, h or 2.6, objects, name=data.get("name", name))

    @classmethod
    def rectangle(cls, w, d, h=2.6):
        """A plain empty room, the fallback when there's no scan."""
        return cls(w, d, h)

    # ---- geometry ------------------------------------------------------------------------------------------------
    def on_furniture(self, x, y, pad=FURNITURE_PAD):
        """True if (x, y) is over a furniture footprint, grown by `pad` metres. pad=0 is the real footprint."""
        for o in self.objects:
            a = math.radians(o["yaw"])
            dx, dy = x - o["x"], y - o["y"]          # point into the object's own frame
            lx = dx * math.cos(a) + dy * math.sin(a)
            ly = -dx * math.sin(a) + dy * math.cos(a)
            if abs(lx) <= o["w"] / 2 + pad and abs(ly) <= o["d"] / 2 + pad:
                return True
        return False

    def ok(self, x, y):
        """True if a person could stand at (x, y): inside the walls and off the (padded) furniture."""
        if not (WALL_MARGIN <= x <= self.w - WALL_MARGIN and WALL_MARGIN <= y <= self.d - WALL_MARGIN):
            return False
        return not self.on_furniture(x, y)

    def random_point(self, rng, tries=40):
        """A random standable point; falls back to the centre if the room is very cluttered."""
        for _ in range(tries):
            x = rng.uniform(WALL_MARGIN, max(self.w - WALL_MARGIN, WALL_MARGIN))
            y = rng.uniform(WALL_MARGIN, max(self.d - WALL_MARGIN, WALL_MARGIN))
            if self.ok(x, y):
                return x, y
        return self.w / 2, self.d / 2


def as_room(room_or_size):
    """Accept either a Room, or a (w, d) / (w, d, h) tuple, and return a Room. Keeps old callers working."""
    if isinstance(room_or_size, Room):
        return room_or_size
    w = room_or_size[0]
    d = room_or_size[1]
    h = room_or_size[2] if len(room_or_size) > 2 else 2.6
    return Room.rectangle(w, d, h)
