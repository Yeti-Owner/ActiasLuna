"""Tests for calibrating with the laptop camera (walkcal.camera_truth, Tracker._record_camera_walk).

Run:  py -m unittest discover tests
"""

import math
import pathlib
import random
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import walkcal  # noqa: E402
from tracker import Tracker, capture_phase  # noqa: E402

A, B = (1.0, 1.0), (3.0, 1.0)


def stand_walk_stand(noise=0.03, seed=1, gap=None):
    """Camera points [t, x, y] every 0.25 s: 3 s on A, walk to B at 1 m/s (2 s), 3 s on B. gap=(t0, t1) drops points."""
    rng = random.Random(seed)
    pts, t = [], 0.0
    while t <= 8.0 + 1e-9:
        if t < 3.0:
            x, y = A
        elif t < 5.0:
            x, y = A[0] + (t - 3.0), A[1]
        else:
            x, y = B
        if not (gap and gap[0] <= t < gap[1]):
            pts.append([round(t, 3), x + rng.gauss(0, noise), y + rng.gauss(0, noise)])
        t += 0.25
    return pts


def samples(t_end=8.0, boards=("b1", "b2", "b3")):
    """Phone readings [t, board, frames, rssi, sd] every 0.25 s from each board."""
    out, t = [], 0.0
    while t <= t_end:
        for i, b in enumerate(boards):
            out.append([round(t, 3), b, 8, -50.0 - 5 * i, 2.0])
        t += 0.25
    return out


def capture(**kw):
    cap = {"phone": "aabbccddeeff", "orientation": "hand", "t0": 1000.0, "marks": [], "motion": [],
           "samples": samples(), "cam": stand_walk_stand()}
    cap.update(kw)
    return cap


class CameraTruthTest(unittest.TestCase):
    def test_standing_snapped_to_median_and_walking_in_between(self):
        tr = walkcal.truth(capture())
        still = [r for r in tr if not r[3]]
        moving = [r for r in tr if r[3]]
        self.assertTrue(still and moving)
        start = [r for r in still if r[0] < 2.5]
        end = [r for r in still if r[0] > 5.5]
        # One position per standing stretch, close to where the walker really stood.
        self.assertEqual(len({(r[1], r[2]) for r in start}), 1)
        self.assertLess(math.hypot(start[0][1] - A[0], start[0][2] - A[1]), 0.05)
        self.assertLess(math.hypot(end[0][1] - B[0], end[0][2] - B[1]), 0.05)
        # Walking rows lie between A and B and head along +x.
        for r in moving:
            self.assertTrue(A[0] - 0.2 <= r[1] <= B[0] + 0.2)
        headings = [r[4] for r in moving if r[4] is not None]
        self.assertTrue(headings)
        self.assertTrue(all(abs(h) < 0.4 for h in headings))

    def test_gap_out_of_view_leaves_a_hole(self):
        tr = walkcal.truth(capture(cam=stand_walk_stand(gap=(3.5, 4.75))))
        self.assertFalse(any(3.6 < r[0] < 4.6 for r in tr))
        self.assertTrue(any(r[0] < 3.5 for r in tr) and any(r[0] > 4.75 for r in tr))

    def test_motion_reports_decide_walking_when_present(self):
        # The phone says it never moved: everything counts as standing even though the camera track moved.
        motion = [[t * 0.5, 0, None, 0] for t in range(17)]
        tr = walkcal.truth(capture(motion=motion))
        self.assertTrue(tr)
        self.assertFalse(any(r[3] for r in tr))

    def test_too_few_points_give_nothing(self):
        self.assertEqual(walkcal.truth(capture(cam=[[0.0, 1.0, 1.0]])), [])
        self.assertEqual(walkcal.walk_spots(capture(cam=[]), "w"), [])

    def test_tapped_walks_unchanged(self):
        cap = capture(cam=None, marks=[[0.0, 1.0, 1.0], [4.0, 3.0, 1.0]])
        del cap["cam"]
        self.assertFalse(walkcal.is_camera_walk(cap))
        tr = walkcal.truth(cap)
        self.assertEqual(tr[0][1:3], (1.0, 1.0))
        self.assertAlmostEqual(tr[-1][1], 3.0)


class CameraSpotsTest(unittest.TestCase):
    def test_spots_from_camera_walk(self):
        spots = walkcal.walk_spots(capture(), "camwalk1000")
        self.assertTrue(spots)
        still = [s for s in spots if s["still"]]
        self.assertEqual(len(still), 2)
        for s, (x, y) in zip(sorted(still, key=lambda s: s["t"]), (A, B)):
            self.assertLess(math.hypot(s["x"] - x, s["y"] - y), 0.05)
        for s in spots:
            self.assertEqual(s["group"], "camwalk1000")
            self.assertEqual(s["kind"], "walk")
            self.assertGreaterEqual(len(s["per_node"]), 2)

    def test_raw_walk_keeps_camera_points_so_spots_rebuild(self):
        cap = capture()
        raw = walkcal.raw_walk(cap, "camwalk1000")
        self.assertEqual(raw["cam"], cap["cam"])
        rebuilt = walkcal.walk_spots(raw, "camwalk1000")
        self.assertEqual([(s["x"], s["y"]) for s in rebuilt],
                         [(s["x"], s["y"]) for s in walkcal.walk_spots(cap, "camwalk1000")])

    def test_df_walk_needs_no_marks(self):
        self.assertEqual(walkcal.df_walk(capture()), [])   # no link readings: nothing, and no crash


# Camera calibration where image (u, v) maps to floor (4u, 4v): an affine map is a homography.
CAL = {"points": [{"u": u, "v": v, "x": 4 * u, "y": 4 * v} for u, v in ((0.1, 0.2), (0.9, 0.2), (0.1, 0.9),
                                                                         (0.9, 0.9), (0.5, 0.5))]}


def cam_packet(*dets):
    return {"v": 1, "t": "cam", "id": "cam:test", "role": "cam", "seq": 1, "up": 0, "w": 640, "h": 480,
            "det": list(dets)}


class RecordCameraWalkTest(unittest.TestCase):
    def setUp(self):
        self.ctx = {"map": None, "settings": {}, "camera": CAL}
        self.tr = Tracker(lambda: self.ctx, [])
        self.tr.start_walk("aabbccddeeff", "hand", 100.0, camera=True)

    def feed(self, t, *dets):
        self.tr.ingest(cam_packet(*dets), t)
        self.tr.tick(t)

    def test_records_lone_person_with_feet(self):
        for i in range(4):
            self.feed(100.0 + 0.25 * i, {"u": 0.5, "v": 0.6, "s": 0.9})
        cam = self.tr.capture["cam"]
        self.assertEqual(len(cam), 4)
        self.assertAlmostEqual(cam[0][1], 2.0, places=2)
        self.assertAlmostEqual(cam[0][2], 2.4, places=2)
        self.assertTrue(self.tr.camera_walk_status(100.75)["seen"])

    def test_two_people_or_head_only_add_nothing(self):
        self.feed(100.0, {"u": 0.3, "v": 0.6, "s": 0.9}, {"u": 0.7, "v": 0.6, "s": 0.9})
        self.feed(100.25, {"hu": 0.5, "hv": 0.3, "s": 0.9})
        self.assertEqual(self.tr.capture["cam"], [])
        self.assertEqual(self.tr.capture["cam_crowded"], 1)

    def test_same_packet_counted_once(self):
        self.tr.ingest(cam_packet({"u": 0.5, "v": 0.6, "s": 0.9}), 100.0)
        self.tr.tick(100.0)
        self.tr.tick(100.25)   # no new packet: the detection is still fresh but must not be added twice
        self.assertEqual(len(self.tr.capture["cam"]), 1)

    def test_tapped_walk_records_no_camera(self):
        tr = Tracker(lambda: self.ctx, [])
        tr.start_walk("aabbccddeeff", "hand", 100.0)
        tr.ingest(cam_packet({"u": 0.5, "v": 0.6, "s": 0.9}), 100.0)
        tr.tick(100.0)
        self.assertNotIn("cam", tr.capture)
        self.assertIsNone(tr.camera_walk_status(100.0))

    def test_stop_finishes_with_camera_rows(self):
        done = []
        self.tr.on_capture_done = done.append
        self.feed(100.0, {"u": 0.5, "v": 0.6, "s": 0.9})
        self.tr.stop_walk()
        self.assertEqual(capture_phase(self.tr.capture, 100.25), "done")
        self.tr.tick(100.25)
        self.assertEqual(len(done), 1)
        self.assertTrue(walkcal.is_camera_walk(done[0]))


if __name__ == "__main__":
    unittest.main()
