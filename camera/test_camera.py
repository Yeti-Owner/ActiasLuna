"""Tests for the camera modules (numpy only; OpenCV not needed).

    python camera/test_camera.py
"""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fusion       # noqa: E402
import headrange    # noqa: E402
import homography   # noqa: E402
import luna_camera  # noqa: E402

# A plausible camera: looks down at a 5 x 4 m floor; image bottom is near the camera.
TRUE_H = np.array([[4.0, 0.3, 0.5], [0.2, -3.0, 4.2], [0.05, -0.6, 1.0]])
IMG = [(0.1, 0.9), (0.9, 0.9), (0.2, 0.5), (0.8, 0.5), (0.5, 0.7), (0.5, 0.95)]


def floor_of(pts, H=TRUE_H):
    return [homography.project(H, u, v) for u, v in pts]


class HomographyTest(unittest.TestCase):
    def test_round_trip(self):
        H = homography.fit(IMG, floor_of(IMG))
        for u, v in [(0.3, 0.6), (0.7, 0.85)]:
            np.testing.assert_allclose(homography.project(H, u, v), homography.project(TRUE_H, u, v), atol=1e-9)
        self.assertLess(homography.rms_error(H, IMG, floor_of(IMG)), 1e-9)

    def test_collinear_rejected(self):
        pts = [(0.1, 0.5), (0.3, 0.5), (0.6, 0.5), (0.9, 0.5)]
        with self.assertRaises(ValueError):
            homography.fit(pts, [(0, 0), (1, 0), (2, 0), (3, 0)])

    def test_too_few_points(self):
        with self.assertRaises(ValueError):
            homography.fit(IMG[:3], floor_of(IMG[:3]))

    def test_above_horizon_is_none(self):
        H = homography.fit(IMG, floor_of(IMG))
        # Horizon of TRUE_H is where 0.05u - 0.6v + 1 = 0, about v = 1.67 at u = 0... pick a point beyond it.
        self.assertIsNone(homography.project(H, 0.0, 2.0))

    def test_jacobian_matches_finite_difference(self):
        H = homography.fit(IMG, floor_of(IMG))
        u, v, e = 0.4, 0.7, 1e-6
        J = homography.jacobian(H, u, v)
        p0 = np.array(homography.project(H, u, v))
        du = (np.array(homography.project(H, u + e, v)) - p0) / e
        dv = (np.array(homography.project(H, u, v + e)) - p0) / e
        np.testing.assert_allclose(J, np.column_stack([du, dv]), rtol=1e-4, atol=1e-6)

    def test_dict_round_trip(self):
        H = homography.fit(IMG, floor_of(IMG))
        d = homography.to_dict(H, IMG, floor_of(IMG), (640, 480))
        H2 = homography.from_dict(d)
        np.testing.assert_allclose(homography.project(H2, 0.5, 0.8), homography.project(H, 0.5, 0.8), atol=1e-9)
        self.assertIsNotNone(d["loo_m"])


class FusionTest(unittest.TestCase):
    def setUp(self):
        self.cam = fusion.CameraFusion(homography.to_dict(homography.fit(IMG, floor_of(IMG)), IMG, floor_of(IMG)))

    def packet(self, *uv, s=0.9):
        return {"t": "cam", "w": 640, "h": 480, "det": [{"u": u, "v": v, "s": s} for u, v in uv]}

    def test_matches_nearby_phone_only(self):
        self.cam.ingest(self.packet((0.5, 0.8)), rt=10.0)
        x, y = homography.project(TRUE_H, 0.5, 0.8)
        got = self.cam.assign({"near": (x + 0.2, y, 0.5), "far": (x + 4.0, y + 4.0, 0.5)}, 10.1)
        self.assertEqual(set(got), {"near"})

    def test_unmatched_detection_dropped(self):
        self.cam.ingest(self.packet((0.5, 0.8)), rt=10.0)
        self.assertEqual(self.cam.assign({"far": (50.0, 50.0, 0.5)}, 10.1), {})
        self.assertEqual(self.cam.assign({}, 10.1), {})

    def test_ambiguous_detection_skipped(self):
        self.cam.ingest(self.packet((0.5, 0.8)), rt=10.0)
        x, y = homography.project(TRUE_H, 0.5, 0.8)
        got = self.cam.assign({"a": (x + 0.1, y, 0.5), "b": (x - 0.1, y, 0.5)}, 10.1)
        self.assertEqual(got, {})

    def test_one_to_one(self):
        self.cam.ingest(self.packet((0.3, 0.85), (0.7, 0.85)), rt=10.0)
        a = homography.project(TRUE_H, 0.3, 0.85)
        b = homography.project(TRUE_H, 0.7, 0.85)
        got = self.cam.assign({"a": (*a, 0.3), "b": (*b, 0.3)}, 10.1)
        self.assertEqual(set(got), {"a", "b"})
        self.assertNotEqual((got["a"].x, got["a"].y), (got["b"].x, got["b"].y))

    def test_stale_and_low_score_ignored(self):
        self.cam.ingest(self.packet((0.5, 0.8)), rt=10.0)
        self.assertEqual(self.cam.detections(10.0 + fusion.MAX_AGE_S + 0.1), [])
        self.cam.ingest(self.packet((0.5, 0.8), s=0.1), rt=11.0)
        self.assertEqual(self.cam.detections(11.0), [])

    def test_uncalibrated_does_nothing(self):
        cam = fusion.CameraFusion(None)
        cam.ingest(self.packet((0.5, 0.8)), rt=1.0)
        self.assertEqual(cam.assign({"a": (0.0, 0.0, 1.0)}, 1.0), {})

    def test_loglik_peaks_at_detection(self):
        self.cam.ingest(self.packet((0.5, 0.8)), rt=10.0)
        det = self.cam.detections(10.0)[0]
        gx, gy = np.meshgrid(np.linspace(det.x - 3, det.x + 3, 61), np.linspace(det.y - 3, det.y + 3, 61))
        ll = self.cam.loglik(det, gx, gy)
        i = np.unravel_index(np.argmax(ll), ll.shape)
        self.assertAlmostEqual(gx[i], det.x, delta=0.11)
        self.assertAlmostEqual(gy[i], det.y, delta=0.11)
        self.assertAlmostEqual(ll.min(), fusion.WEIGHT * np.log(fusion.OUTLIER), delta=0.1)
        np.testing.assert_array_equal(self.cam.loglik(None, gx, gy), 0)

    def test_status_has_no_positions(self):
        self.cam.ingest(self.packet((0.5, 0.8)), rt=10.0)
        self.assertEqual(set(self.cam.status(10.0)), {"calibrated", "head_calibrated", "floor_calibrated",
                                                      "range_calibrated", "age", "seen", "seen_by_head", "used"})

    def test_new_calibration_applies_to_held_packet(self):
        cam = fusion.CameraFusion(None)
        cam.ingest(self.packet((0.5, 0.8)), rt=10.0)
        self.assertEqual(cam.detections(10.0), [])
        cam.set_calibration(homography.to_dict(homography.fit(IMG, floor_of(IMG)), IMG, floor_of(IMG)))
        det = cam.detections(10.0)[0]
        np.testing.assert_allclose((det.x, det.y), homography.project(TRUE_H, 0.5, 0.8), atol=1e-6)

    def test_bad_calibration_switches_off(self):
        line = [{"u": 0.1 * i, "v": 0.5, "x": float(i), "y": 0.0} for i in range(1, 5)]
        cam = fusion.CameraFusion({"points": line})
        self.assertFalse(cam.calibrated)


def pinhole(x, y, z, cam=(0.2, 0.2, 0.8), yaw=0.8, pitch=0.1, focal=0.5, aspect=4 / 3):
    """Normalised image (u, v) of room point (x, y, z) for a laptop on a table: wide lens, tilted slightly down.

    Asserts the point is in frame, so a test can't silently use a point the camera couldn't see.
    """
    fwd = np.array([np.cos(yaw) * np.cos(pitch), np.sin(yaw) * np.cos(pitch), -np.sin(pitch)])
    right = np.array([np.sin(yaw), -np.cos(yaw), 0.0])
    up = np.cross(right, fwd)
    d = np.array([x, y, z]) - np.array(cam)
    depth = d @ fwd
    assert depth > 0.1, "behind the camera"
    u, v = 0.5 + focal * (d @ right) / depth, 0.5 - focal * aspect * (d @ up) / depth
    assert 0 < u < 1 and 0 < v < 1, f"({x}, {y}, {z}) is out of frame: ({u:.2f}, {v:.2f})"
    return float(u), float(v)


CAL_HEAD_M = 1.7
CAL_SPOTS = [(1.5, 1.5), (3.5, 1.5), (1.5, 3.5), (3.5, 3.5), (2.5, 2.5), (3.0, 2.0)]


def head_calibration():
    """Floor + head record made the way calibrate.py's space key makes it, by a 1.7 m person on CAL_SPOTS."""
    feet = [pinhole(x, y, 0.0) for x, y in CAL_SPOTS]
    heads = [pinhole(x, y, CAL_HEAD_M) for x, y in CAL_SPOTS]
    rec = homography.to_dict(homography.fit(feet, CAL_SPOTS), feet, CAL_SPOTS)
    rec["head"] = homography.to_dict(homography.fit(heads, CAL_SPOTS), heads, CAL_SPOTS)
    return rec


class HeadTest(unittest.TestCase):
    def setUp(self):
        self.cam = fusion.CameraFusion(head_calibration())

    def detect(self, d, rt=5.0):
        self.cam.ingest({"t": "cam", "w": 640, "h": 480, "det": [dict(d, s=0.9)]}, rt)
        dets = self.cam.detections(rt)
        return dets[0] if dets else None

    def test_head_mapping_loaded(self):
        self.assertTrue(self.cam.head_calibrated)

    def test_head_only_places_calibrated_height_correctly(self):
        hu, hv = pinhole(2.2, 2.6, CAL_HEAD_M)
        det = self.detect({"hu": hu, "hv": hv})
        self.assertEqual(det.kind, "head")
        self.assertAlmostEqual(det.x, 2.2, delta=1e-6)
        self.assertAlmostEqual(det.y, 2.6, delta=1e-6)

    def test_height_error_lies_along_sight_line_and_inside_uncertainty(self):
        x, y = 2.2, 2.6
        hu, hv = pinhole(x, y, CAL_HEAD_M * 1.1)   # someone 10% taller than the calibrator
        det = self.detect({"hu": hu, "hv": hv})
        err = np.array([det.x - x, det.y - y])
        self.assertGreater(np.hypot(*err), 0.05)   # being taller really does shift the estimate
        sight = np.array([x - 0.2, y - 0.2])       # from the camera, along the floor
        cos = abs(err @ sight) / (np.linalg.norm(err) * np.linalg.norm(sight))
        self.assertGreater(cos, 0.99)
        eigval, eigvec = np.linalg.eigh(det.cov)   # and the uncertainty is stretched the same way
        self.assertGreater(abs(eigvec[:, -1] @ sight) / np.linalg.norm(sight), 0.99)
        self.assertGreater(eigval[-1], 4 * eigval[0])
        self.assertLess(np.sqrt(err @ np.linalg.solve(det.cov, err)), 1.5)

    def test_feet_preferred_when_both_seen(self):
        u, v = pinhole(2.0, 2.0, 0.0)
        hu, hv = pinhole(2.0, 2.0, CAL_HEAD_M * 1.2)
        det = self.detect({"u": u, "v": v, "hu": hu, "hv": hv})
        self.assertEqual(det.kind, "feet")
        self.assertAlmostEqual(det.x, 2.0, delta=1e-6)

    def test_head_ignored_without_head_mapping(self):
        rec = head_calibration()
        del rec["head"]
        cam = fusion.CameraFusion(rec)
        hu, hv = pinhole(2.0, 2.0, CAL_HEAD_M)
        cam.ingest({"t": "cam", "w": 640, "h": 480, "det": [{"hu": hu, "hv": hv, "s": 0.9}]}, 1.0)
        self.assertFalse(cam.head_calibrated)
        self.assertEqual(cam.detections(1.0), [])

    def test_head_mapping_needs_floor_mapping(self):
        cam = fusion.CameraFusion({"points": [], "head": head_calibration()["head"]})
        self.assertFalse(cam.calibrated)
        self.assertFalse(cam.head_calibrated)

    def test_status_counts_head_detections(self):
        hu, hv = pinhole(2.2, 2.6, CAL_HEAD_M)
        self.detect({"hu": hu, "hv": hv})
        st = self.cam.status(5.0)
        self.assertEqual((st["seen"], st["seen_by_head"], st["head_calibrated"]), (1, 1, True))


class HeightCheckTest(unittest.TestCase):
    """Is it a person? Bottom + top give an object's height relative to the calibrating person."""

    def setUp(self):
        self.cam = fusion.CameraFusion(head_calibration())

    def thing(self, x, y, bottom_z, top_z):
        """Detection of something at floor (x, y) whose visible bottom is at bottom_z and top at top_z."""
        u, v = pinhole(x, y, bottom_z)
        hu, hv = pinhole(x, y, top_z)
        self.cam.ingest({"t": "cam", "w": 640, "h": 480, "det": [{"u": u, "v": v, "hu": hu, "hv": hv, "s": 0.9}]}, 1.0)
        dets = self.cam.detections(1.0)
        return dets[0] if dets else None

    def test_height_ratio_measures_height(self):
        for rel in (0.7, 1.0, 1.1):
            r = self.cam.height_ratio(pinhole(2.4, 2.2, 0.0), pinhole(2.4, 2.2, CAL_HEAD_M * rel))
            self.assertAlmostEqual(r, rel, delta=1e-6)

    def test_standing_and_sitting_people_use_feet(self):
        for top in (CAL_HEAD_M, 0.7 * CAL_HEAD_M):
            det = self.thing(2.4, 2.2, 0.0, top)
            self.assertEqual(det.kind, "feet")
            self.assertAlmostEqual(det.x, 2.4, delta=1e-6)

    def test_short_moving_thing_is_not_a_person(self):
        self.assertIsNone(self.thing(2.4, 2.2, 0.0, 0.4))   # a pet or a rolling chair

    def test_desk_edge_bottom_falls_back_to_head(self):
        det = self.thing(2.4, 2.2, 0.75, CAL_HEAD_M)        # legs hidden: the blob ends at the desk top
        self.assertEqual(det.kind, "head")
        self.assertAlmostEqual(det.x, 2.4, delta=1e-6)
        self.assertAlmostEqual(det.y, 2.2, delta=1e-6)


class MoverTracksTest(unittest.TestCase):
    W = 640

    def run_frames(self, tracks, boxes_per_frame, t0=0.0, dt=0.125):
        out = []
        for i, boxes in enumerate(boxes_per_frame):
            out = tracks.update(boxes, t0 + i * dt, self.W)
        return out

    def test_mover_confirmed_after_moving_for_a_few_frames(self):
        tr = luna_camera.MoverTracks()
        frames = [[(200 + 8 * i, 100, 60, 200)] for i in range(luna_camera.MOVE_MIN_FRAMES + 1)]
        out = self.run_frames(tr, frames)
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0][2])

    def test_flicker_and_still_blobs_never_count(self):
        tr = luna_camera.MoverTracks()
        self.assertEqual(self.run_frames(tr, [[(300, 100, 60, 200)]] * 10), [])          # never moves
        self.assertEqual(self.run_frames(luna_camera.MoverTracks(), [[(300, 100, 60, 200)], [], [(50, 50, 40, 40)]]),
                         [])                                                               # comes and goes

    def test_stopped_mover_is_held_then_released(self):
        tr = luna_camera.MoverTracks()
        self.run_frames(tr, [[(200 + 8 * i, 100, 60, 200)] for i in range(5)])
        held = tr.update([], 1.0, self.W)                    # faded into the background
        self.assertEqual(len(held), 1)
        self.assertTrue(held[0][2])
        self.assertEqual(held[0][1], luna_camera.MOVE_HELD_SCORE)
        self.assertEqual(tr.update([], 1.0 + luna_camera.MOVE_HOLD_S + 1, self.W), [])

    def test_mover_leaving_by_the_side_is_not_held(self):
        tr = luna_camera.MoverTracks()
        self.run_frames(tr, [[(560 + 15 * i, 100, 60, 200)] for i in range(5)])   # reaches the right edge
        self.assertEqual(tr.update([], 1.0, self.W), [])


FACE_M = 0.22   # crown-to-chin height of the test faces


def face_obs(x, y, face=FACE_M, top=CAL_HEAD_M):
    """(size, x_off) of a face box for someone at floor (x, y), as the pinhole test camera sees it."""
    u0, v0 = pinhole(x, y, top)
    u1, v1 = pinhole(x, y, top - face)
    return v1 - v0, ((u0 + u1) / 2 - 0.5) * 4 / 3


RANGE_SPOTS = [(1.5, 3.5), (2.5, 2.5), (3.5, 1.5)]   # left of view, middle, right


def range_cal():
    return headrange.solve((0.2, 0.2), [{"xy": xy, "obs": {"face": face_obs(*xy)}} for xy in RANGE_SPOTS])


class HeadRangeTest(unittest.TestCase):
    def test_setup_recovers_camera_facing_and_view(self):
        cal = range_cal()
        self.assertAlmostEqual(cal["yaw"], 0.8, delta=np.radians(2))   # the pinhole camera's real facing
        self.assertAlmostEqual(cal["f"], 0.5 * 4 / 3, delta=0.05)      # its focal length in frame heights
        self.assertLess(cal["rms_deg"], 1.0)

    def test_places_people_from_head_size(self):
        cal = range_cal()
        for x, y in ((2.2, 2.6), (3.0, 2.0), (1.8, 3.0)):
            (gx, gy), _ = headrange.locate(cal, "face", *face_obs(x, y))
            self.assertLess(np.hypot(gx - x, gy - y), 0.15)

    def test_bigger_head_errs_along_sight_line_within_uncertainty(self):
        cal = range_cal()
        x, y = 2.6, 2.4
        (gx, gy), cov = headrange.locate(cal, "face", *face_obs(x, y, face=FACE_M * 1.1))
        err = np.array([gx - x, gy - y])
        sight = np.array([x - 0.2, y - 0.2])
        self.assertGreater(np.hypot(*err), 0.1)                       # a bigger head looks nearer
        self.assertLess(err @ sight, 0)                               # ...placed toward the camera
        self.assertGreater(abs(err @ sight) / (np.linalg.norm(err) * np.linalg.norm(sight)), 0.97)
        self.assertLess(np.sqrt(err @ np.linalg.solve(np.array(cov), err)), 1.5)

    def test_needs_spread_out_spots(self):
        with self.assertRaises(ValueError):
            headrange.solve((0.2, 0.2), [{"xy": (2.5, 2.5), "obs": {"face": face_obs(2.5, 2.5)}}])

    def test_fusion_uses_range_without_floor_calibration(self):
        cam = fusion.CameraFusion({"version": 1, "head_range": range_cal()})
        self.assertTrue(cam.calibrated)
        self.assertFalse(cam.head_calibrated)
        size, x_off = face_obs(2.2, 2.6)
        cx = 0.5 + x_off * 3 / 4                                      # back to a box centre in frame widths
        det = {"bx": cx - 0.02, "bw": 0.04, "by": 0.3, "bh": size, "s": 0.6, "k": "face"}
        cam.ingest({"t": "cam", "w": 640, "h": 480, "det": [det]}, 1.0)
        d = cam.detections(1.0)[0]
        self.assertEqual(d.kind, "range")
        self.assertLess(np.hypot(d.x - 2.2, d.y - 2.6), 0.15)
        self.assertEqual(cam.status(1.0)["seen_by_head"], 1)

    def test_feet_still_preferred_when_floor_calibrated(self):
        rec = head_calibration()
        rec["head_range"] = range_cal()
        cam = fusion.CameraFusion(rec)
        u, v = pinhole(2.2, 2.6, 0.0)
        size, x_off = face_obs(2.2, 2.6, face=FACE_M * 1.3)          # a misleading head size...
        det = {"u": u, "v": v, "bx": u - 0.02, "bw": 0.04, "by": 0.3, "bh": size, "s": 0.9, "k": "body"}
        cam.ingest({"t": "cam", "w": 640, "h": 480, "det": [det]}, 1.0)
        d = cam.detections(1.0)[0]
        self.assertEqual(d.kind, "feet")                              # ...is ignored when the feet are seen
        self.assertAlmostEqual(d.x, 2.2, delta=1e-6)


class PacketTest(unittest.TestCase):
    def test_body_has_feet_and_head(self):
        d = luna_camera.person_record((100, 50, 60, 200), 640, 480, 0.8)
        self.assertEqual((d["u"], d["v"]), (round(130 / 640, 4), round(250 / 480, 4)))
        self.assertEqual((d["hu"], d["hv"]), (round(130 / 640, 4), round(50 / 480, 4)))

    def test_feet_out_of_view_keeps_head(self):
        d = luna_camera.person_record((100, 200, 60, 280), 640, 480, 0.8)
        self.assertNotIn("u", d)
        self.assertEqual(d["hv"], round(200 / 480, 4))

    def test_upper_body_never_has_feet(self):
        d = luna_camera.person_record((100, 50, 60, 80), 640, 480, 0.5, "upper")
        self.assertNotIn("u", d)
        self.assertIn("hu", d)

    def test_face_head_top_is_above_the_face(self):
        d = luna_camera.person_record((100, 100, 40, 40), 640, 480, 0.6, "face")
        self.assertEqual(d["hv"], round((100 - luna_camera.FACE_CROWN * 40) / 480, 4))

    def test_nothing_usable_dropped(self):
        self.assertIsNone(luna_camera.person_record((100, 0, 60, 480), 640, 480, 0.8))

    def test_packet_shape(self):
        p = luna_camera.build_packet("cam:laptop", 3, 1234.5, 640, 480, [])
        self.assertEqual((p["t"], p["role"], p["seq"], p["up"], p["det"]), ("cam", "cam", 3, 1234, []))


if __name__ == "__main__":
    unittest.main()
