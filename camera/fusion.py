"""Use laptop-camera detections to sharpen phone positions (server side, numpy only).

The camera can't tell people apart and can't see who has joined the demo network, so it is never a source of new
people. Each detection is only matched to an existing phone track that it clearly belongs to. Matched detections
add a likelihood to that phone's grid; every other detection is dropped and never reaches the snapshot or the UI.

Typical use from the tracker:

    cam = CameraFusion(calibration_dict)
    cam.ingest(packet, rt)                                  # every `cam` packet
    matches = cam.assign({mac: (x, y, unc), ...}, t)        # once per tick, phone tracks only
    log_post += cam.loglik(matches[mac], grid_x, grid_y)    # inside that phone's Track.step
"""

from dataclasses import dataclass

import numpy as np

try:
    from . import homography
except ImportError:  # run as a plain script from inside camera/
    import homography

# Tunables: module-level and read when used, so tools and the lab can override them.
PIXEL_SIGMA = 0.015    # foot-point noise as a fraction of image height
MIN_SIGMA_M = 0.15     # floor noise never assumed smaller than this
MAX_SIGMA_M = 1.5      # detections vaguer than this (far away, near the horizon) are ignored
MIN_SCORE = 0.3        # detector confidence below this is ignored
MAX_AGE_S = 0.6        # detections older than this are stale
GATE_SIGMAS = 3.0      # a detection must lie within this many sigmas of a phone track to match it
AMBIGUITY = 1.5        # skip a detection if a second track is within AMBIGUITY x the best track's distance
OUTLIER = 0.1          # chance a matched detection is wrong; keeps a bad match from collapsing the filter
WEIGHT = 1.0           # scale on the camera's log-likelihood


@dataclass
class Detection:
    x: float
    y: float
    cov: np.ndarray    # 2x2 floor covariance, m^2
    score: float
    rt: float


class CameraFusion:
    def __init__(self, calibration=None):
        self.H = None
        self.dets = []
        self.last_rt = None
        self.last_used = 0
        self.set_calibration(calibration)

    def set_calibration(self, calibration):
        """Load a homography record from `homography.to_dict`, or None to switch the camera off."""
        self.H = homography.from_dict(calibration) if calibration else None
        self.dets = []

    @property
    def calibrated(self):
        return self.H is not None

    def ingest(self, packet, rt):
        """Take one `cam` packet received at time `rt` (the recorded receive time, not the wall clock)."""
        self.last_rt = rt
        self.dets = []
        if self.H is None:
            return
        w, h = packet.get("w") or 4, packet.get("h") or 3
        su = PIXEL_SIGMA * h / w   # u is normalised by width, so the same pixel noise is smaller in u
        sv = PIXEL_SIGMA
        img_cov = np.diag([su * su, sv * sv])
        for d in packet.get("det") or []:
            try:
                u, v, s = float(d["u"]), float(d["v"]), float(d.get("s", 1.0))
            except (KeyError, TypeError, ValueError):
                continue
            if s < MIN_SCORE or not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
                continue
            xy = homography.project(self.H, u, v)
            if xy is None:
                continue
            J = homography.jacobian(self.H, u, v)
            cov = J @ img_cov @ J.T + np.eye(2) * MIN_SIGMA_M ** 2
            if np.sqrt(np.linalg.eigvalsh(cov)[-1]) > MAX_SIGMA_M:
                continue
            self.dets.append(Detection(xy[0], xy[1], cov, s, rt))

    def detections(self, t):
        """Fresh detections at time `t`."""
        if self.last_rt is None or abs(t - self.last_rt) > MAX_AGE_S:
            return []
        return self.dets

    def assign(self, tracks, t):
        """Match fresh detections to phone tracks, one to one.

        tracks: {key: (x, y, unc)} with unc the track's 1-sigma radius in metres.
        Returns {key: Detection} for the tracks that got a clear, unambiguous match.
        """
        dets = self.detections(t)
        keys = list(tracks)
        pairs = []
        for i, det in enumerate(dets):
            dists = []
            for k in keys:
                x, y, unc = tracks[k]
                S = det.cov + np.eye(2) * max(float(unc), 0.0) ** 2
                d = np.array([det.x - x, det.y - y])
                dists.append((float(np.sqrt(d @ np.linalg.solve(S, d))), k))
            inside = sorted(p for p in dists if p[0] <= GATE_SIGMAS)
            if not inside:
                continue
            if len(inside) > 1 and inside[1][0] < AMBIGUITY * inside[0][0]:
                continue   # two phones could own it; guessing could swap identities
            pairs.extend((dist, i, k) for dist, k in inside)
        out, used_dets = {}, set()
        for dist, i, k in sorted(pairs, key=lambda p: p[0]):
            if k in out or i in used_dets:
                continue
            out[k] = dets[i]
            used_dets.add(i)
        self.last_used = len(out)
        return out

    def loglik(self, det, grid_x, grid_y):
        """Log-likelihood of each grid cell given a matched detection; 0 at the detection, log(OUTLIER) far away.

        grid_x, grid_y: arrays of cell-centre coordinates in metres (any matching shape).
        """
        if det is None:
            return np.zeros(np.shape(grid_x))
        inv = np.linalg.inv(det.cov)
        dx = np.asarray(grid_x, float) - det.x
        dy = np.asarray(grid_y, float) - det.y
        m = inv[0, 0] * dx * dx + 2.0 * inv[0, 1] * dx * dy + inv[1, 1] * dy * dy
        return WEIGHT * np.log((1.0 - OUTLIER) * np.exp(-0.5 * m) + OUTLIER)

    def status(self, t):
        """Summary safe for the snapshot: counts only, never positions of unmatched people."""
        return {"calibrated": self.calibrated, "seen": len(self.detections(t)), "used": self.last_used}
