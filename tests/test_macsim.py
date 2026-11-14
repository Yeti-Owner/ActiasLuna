"""Unit tests for server/macsim.py.   Run:  py -m unittest discover tests"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))

from macsim import MacSim, DECOY_PREFIX, TARGET_PREFIX, LABEL  # noqa: E402

ROOM = (6.0, 4.5)


class MacSimTest(unittest.TestCase):
    def test_snapshot_shape_and_flags(self):
        sim = MacSim(decoys=6, rotate_s=4, seed=1)
        sim.tick(0.0, ROOM)
        snap = sim.snapshot()
        self.assertTrue(snap["sim"])
        self.assertEqual(snap["label"], LABEL)
        self.assertEqual(len(snap["decoys"]), 6)
        self.assertEqual(snap["candidates"], 7)                     # 6 decoys + target
        self.assertTrue(snap["target"]["mac"].startswith(TARGET_PREFIX))
        for dec in snap["decoys"]:
            self.assertTrue(dec["mac"].startswith(DECOY_PREFIX))

    def test_target_mac_rotates(self):
        sim = MacSim(decoys=2, rotate_s=1.0, seed=1)
        macs = set()
        t = 0.0
        for _ in range(40):          # 10 s at 4 Hz, rotate every 1 s
            sim.tick(t, ROOM)
            macs.add(sim.target_mac)
            t += 0.25
        self.assertGreaterEqual(len(macs), 8)                      # trail reset many times
        self.assertGreaterEqual(sim.rotations, 8)

    def test_no_rotation_when_off(self):
        sim = MacSim(decoys=2, rotate_s=0, seed=1)
        macs = set()
        t = 0.0
        for _ in range(40):
            sim.tick(t, ROOM)
            macs.add(sim.target_mac)
            t += 0.25
        self.assertEqual(len(macs), 1)                             # steady address
        self.assertEqual(sim.rotations, 0)
        self.assertFalse(sim.snapshot()["rotating"])

    def test_everyone_stays_in_room(self):
        sim = MacSim(decoys=8, rotate_s=2, seed=3)
        t = 0.0
        for _ in range(200):
            t += 0.25
            sim.tick(t, ROOM)
            snap = sim.snapshot()
            for who in [snap["target"]] + snap["decoys"]:
                self.assertGreaterEqual(who["x"], 0.0)
                self.assertLessEqual(who["x"], ROOM[0])
                self.assertGreaterEqual(who["y"], 0.0)
                self.assertLessEqual(who["y"], ROOM[1])

    def test_reproducible_with_seed(self):
        a, b = MacSim(seed=7), MacSim(seed=7)
        for sim in (a, b):
            sim.tick(0.0, ROOM)
            sim.tick(0.25, ROOM)
        self.assertEqual(a.snapshot(), b.snapshot())

    def test_snapshot_none_before_tick(self):
        self.assertIsNone(MacSim().snapshot())


if __name__ == "__main__":
    unittest.main()
