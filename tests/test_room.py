"""Unit tests for server/room.py and room-aware decoy placement.   Run:  py -m unittest discover tests"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from crowd import Crowd  # noqa: E402
from macsim import MacSim  # noqa: E402
from room import Room, as_room  # noqa: E402

SAMPLE = ROOT / "data" / "scan" / "sample" / "room.json"


class RoomTest(unittest.TestCase):
    def setUp(self):
        self.room = Room.load(SAMPLE)

    def test_loads_size_and_furniture(self):
        self.assertAlmostEqual(self.room.w, 6.0)
        self.assertAlmostEqual(self.room.d, 4.5)
        self.assertEqual(len(self.room.objects), 3)

    def test_ok_rejects_walls_and_furniture(self):
        self.assertTrue(self.room.ok(3.0, 1.0))        # open floor
        self.assertFalse(self.room.ok(0.05, 2.0))      # into the wall margin
        self.assertFalse(self.room.ok(3.0, 2.2))       # on top of the table
        self.assertFalse(self.room.ok(1.0, 3.9))       # on the sofa

    def test_random_point_is_standable(self):
        import random
        rng = random.Random(0)
        for _ in range(200):
            x, y = self.room.random_point(rng)
            self.assertTrue(self.room.ok(x, y))

    def test_as_room_from_tuple(self):
        r = as_room((5, 4))
        self.assertAlmostEqual(r.w, 5)
        self.assertAlmostEqual(r.d, 4)
        self.assertTrue(r.ok(2.5, 2.0))

    def test_from_dict_derives_size_from_walls(self):
        r = Room.from_dict({"walls": [{"a": [0, 0], "b": [4, 0]}, {"a": [4, 0], "b": [4, 3]},
                                      {"a": [4, 3], "b": [0, 3]}, {"a": [0, 3], "b": [0, 0]}]})
        self.assertAlmostEqual(r.w, 4)
        self.assertAlmostEqual(r.d, 3)

    def test_bad_room_raises(self):
        with self.assertRaises(ValueError):
            Room.from_dict({"nonsense": True})


class RoomAwareDecoysTest(unittest.TestCase):
    def setUp(self):
        self.room = Room.load(SAMPLE)

    # Decoys are kept off the padded furniture; snapshot rounds to 1 cm, so test against the real
    # footprint (pad=0) and the walls with a 1 cm tolerance.
    def assertStandable(self, x, y):
        tol = 0.02
        self.assertFalse(self.room.on_furniture(x, y, pad=0.0), f"({x}, {y}) is on furniture")
        self.assertTrue(0.3 - tol <= x <= self.room.w - 0.3 + tol and 0.3 - tol <= y <= self.room.d - 0.3 + tol,
                        f"({x}, {y}) is in a wall")

    def test_crowd_decoys_stay_standable(self):
        c = Crowd(count=10, seed=2)
        c.set(True)
        t = 0.0
        for _ in range(150):
            t += 0.25
            c.tick(t, self.room, center=(3.0, 1.0))
            for dec in c.snapshot():
                self.assertStandable(dec["x"], dec["y"])

    def test_macsim_everyone_standable(self):
        sim = MacSim(decoys=8, rotate_s=2, seed=4)
        t = 0.0
        for _ in range(150):
            t += 0.25
            sim.tick(t, self.room)
            snap = sim.snapshot()
            for who in [snap["target"]] + snap["decoys"]:
                self.assertStandable(who["x"], who["y"])

    def test_still_works_with_plain_tuple(self):
        c = Crowd(count=5, seed=1)
        c.set(True)
        c.tick(0.0, (6.0, 4.5))
        self.assertEqual(len(c.snapshot()), 5)


if __name__ == "__main__":
    unittest.main()
