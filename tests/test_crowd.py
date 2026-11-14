"""Unit tests for server/crowd.py.   Run:  py -m unittest discover tests"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))

from crowd import Crowd, CrowdState, DECOY_PREFIX  # noqa: E402

ROOM = (6.0, 4.5)


class CrowdTest(unittest.TestCase):
    def setUp(self):
        self.c = Crowd(count=8, seed=1)

    def test_off_by_default(self):
        self.c.tick(0.0, ROOM)
        self.assertEqual(self.c.state, CrowdState.OFF)
        self.assertEqual(self.c.snapshot(), [])

    def test_button_turns_on_and_off(self):
        self.assertTrue(self.c.handle_packet({"t": "crowd", "on": 1}))
        self.c.tick(0.0, ROOM)
        self.assertEqual(len(self.c.snapshot()), 8)
        self.c.handle_packet({"t": "crowd", "on": 0})
        self.c.tick(0.25, ROOM)
        self.assertEqual(self.c.snapshot(), [])

    def test_toggle_packet_flips_current_state(self):
        self.c.set(True)   # e.g. turned on from the web page first
        self.c.handle_packet({"t": "crowd", "toggle": 1})
        self.assertEqual(self.c.state, CrowdState.OFF)
        self.c.handle_packet({"t": "crowd", "toggle": 1})
        self.assertEqual(self.c.state, CrowdState.ON)

    def test_ignores_other_packets(self):
        self.assertFalse(self.c.handle_packet({"t": "tel"}))

    def test_toggle(self):
        self.c.toggle()
        self.assertEqual(self.c.state, CrowdState.ON)
        self.c.toggle()
        self.assertEqual(self.c.state, CrowdState.OFF)

    def test_decoys_are_flagged_and_fake(self):
        self.c.set(True)
        self.c.tick(0.0, ROOM)
        for dec in self.c.snapshot():
            self.assertTrue(dec["sim"])
            self.assertTrue(dec["mac"].startswith(DECOY_PREFIX))

    def test_decoys_stay_in_room(self):
        self.c.set(True)
        t = 0.0
        for _ in range(200):          # 50 s of movement
            t += 0.25
            self.c.tick(t, ROOM)
            for dec in self.c.snapshot():
                self.assertGreaterEqual(dec["x"], 0.0)
                self.assertLessEqual(dec["x"], ROOM[0])
                self.assertGreaterEqual(dec["y"], 0.0)
                self.assertLessEqual(dec["y"], ROOM[1])

    def test_decoys_move(self):
        self.c.set(True)
        self.c.tick(0.0, ROOM)
        first = self.c.snapshot()[0]
        start = (first["x"], first["y"])
        for i in range(1, 11):
            self.c.tick(0.25 * i, ROOM)
        moved = self.c.snapshot()[0]
        self.assertNotEqual((moved["x"], moved["y"]), start)

    def test_reproducible_with_seed(self):
        a, b = Crowd(seed=42), Crowd(seed=42)
        for c in (a, b):
            c.set(True)
            c.tick(0.0, ROOM)
            c.tick(0.25, ROOM)
        self.assertEqual(a.snapshot(), b.snapshot())

    def test_big_dt_clamped(self):
        self.c.set(True)
        self.c.tick(0.0, ROOM)
        self.c.tick(10_000.0, ROOM)   # a huge jump must not fling decoys out of the room
        for dec in self.c.snapshot():
            self.assertLessEqual(dec["x"], ROOM[0])
            self.assertLessEqual(dec["y"], ROOM[1])


if __name__ == "__main__":
    unittest.main()
