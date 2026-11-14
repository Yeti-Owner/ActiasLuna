"""Tests for the camera modules (numpy only; OpenCV not needed).

    python camera/test_camera.py
"""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fusion       # noqa: E402
import homography   # noqa: E402
import moth_camera  # noqa: E402

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
        self.assertAlmostEqual(ll.min(), np.log(fusion.OUTLIER), delta=0.05)
        np.testing.assert_array_equal(self.cam.loglik(None, gx, gy), 0)

    def test_status_has_no_positions(self):
        self.cam.ingest(self.packet((0.5, 0.8)), rt=10.0)
        self.assertEqual(set(self.cam.status(10.0)), {"calibrated", "seen", "used"})


class PacketTest(unittest.TestCase):
    def test_foot_point(self):
        d = moth_camera.foot_point((100, 50, 60, 200), 640, 480, 0.8)
        self.assertEqual((d["u"], d["v"]), (round(130 / 640, 4), round(250 / 480, 4)))

    def test_feet_out_of_view_dropped(self):
        self.assertIsNone(moth_camera.foot_point((100, 200, 60, 280), 640, 480, 0.8))

    def test_packet_shape(self):
        p = moth_camera.build_packet("cam:laptop", 3, 1234.5, 640, 480, [])
        self.assertEqual((p["t"], p["role"], p["seq"], p["up"], p["det"]), ("cam", "cam", 3, 1234, []))


if __name__ == "__main__":
    unittest.main()
