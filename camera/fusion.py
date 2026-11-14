"""Use laptop-camera detections to sharpen phone positions (server side, numpy only).

The camera can't tell people apart and can't see who has joined the demo network, so it is never a source of new
people. Each detection is only matched to an existing phone track that it clearly belongs to. Matched detections
add a likelihood to that phone's grid; every other detection is dropped and never reaches the snapshot or the UI.

Used by server/tracker.py:

    cam = CameraFusion(calibration_dict)
    cam.ingest(packet, rt)                                  # every `cam` packet
    matches = cam.assign({mac: (x, y, unc), ...}, t)        # once per tick, phone tracks only
    extra = cam.loglik(matches[mac], m.cx, m.cy)            # passed to that phone's Track.step as `extra`
"""

from dataclasses import dataclass

import numpy as np

try:
    from . import headrange, homography
except ImportError:  # run as a plain script from inside camera/
    import headrange
    import homography

# Tunables: module-level and read when used, so tools and the lab can override them.
PIXEL_SIGMA = 0.015    # foot-point noise as a fraction of image height
MIN_SIGMA_M = 0.15     # floor noise never assumed smaller than this
MAX_SIGMA_M = 1.5      # detections vaguer than this (far away, near the horizon) are ignored
MIN_SCORE = 0.3        # detector confidence below this is ignored
MAX_AGE_S = 0.6        # detections older than this are stale
GATE_SIGMAS = 3.0      # a detection must lie within this many sigmas of a phone track to match it
AMBIGUITY = 1.5        # skip a detection if a second track is within AMBIGUITY x the best track's distance
# OUTLIER and WEIGHT set how hard the camera pulls. In simulation (tools/sim_nodes.py --camera) stronger is better
# all the way to WEIGHT=4, OUTLIER=0.02, but that camera is ideal; these are a cautious middle for a real one.
# Override as camera.fusion.WEIGHT (the module the tracker imports), not a separately imported `fusion`.
OUTLIER = 0.05         # chance a matched detection is wrong; keeps a bad match from collapsing the filter
WEIGHT = 2.0           # scale on the camera's log-likelihood
# Head fallback, used only when a person's feet are hidden (desk, frame edge) but the top of their head is seen.
# The head mapping is calibrated at the calibrating person's head height; anyone taller or shorter is placed too
# far or too near along the camera's line of sight, by their height difference as a fraction of that height.
HEAD_PIXEL_SIGMA = 0.02   # head-top noise as a fraction of image height (hair, box tops jitter more than feet)
HEIGHT_REL_SD = 0.1       # spread of standing head heights, relative (about 17 cm at 1.7 m)
# Is it a person? When both the bottom and the top of something are seen (and the head mapping exists), the two
# mappings give its height relative to the calibrating person. Moving-object detection reports anything that moves,
# so this is what tells a person from a pet or a rolling chair.
MIN_HEIGHT_REL = 0.45     # shorter than this is not a person (pet, chair, bag); sitting is about 0.7
MAX_HEIGHT_REL = 1.35     # taller than this means the bottom isn't the floor (a desk edge hides the legs): use the head


@dataclass
class Detection:
    x: float
    y: float
    cov: np.ndarray    # 2x2 floor covariance, m^2
    score: float
    rt: float
    # "feet": foot point (floor mapping); "range": head size + left/right (headrange.py); "head": head top (head mapping)
    kind: str = "feet"


class CameraFusion:
    def __init__(self, calibration=None):
        self.H = None            # floor mapping: image foot point -> floor
        self.Hh = None           # head mapping: image head top -> floor spot below it (optional)
        self.range = None        # head-size ranging from camera/head_setup.py (optional; works without H)
        self.packet = None       # latest raw `cam` packet; projected when used, so a new calibration applies at once
        self.last_rt = None
        self.last_used = 0
        self._cache = None       # (packet, H, Hh, range, detections) for the last projection
        self.set_calibration(calibration)

    def set_calibration(self, calibration):
        """Load a homography record from `homography.to_dict`, or None to switch the camera off.

        A record that can't be fitted (too few or collinear points) also switches it off rather than raising. The
        head mapping is only used alongside a floor mapping: its uncertainty comes from where the head's sight line
        meets the floor.
        """
        try:
            self.H = homography.from_dict(calibration) if calibration else None
        except (ValueError, KeyError, TypeError, np.linalg.LinAlgError):
            self.H = None
        self.Hh = homography.head_from_dict(calibration) if self.H is not None else None
        rng = calibration.get("head_range") if isinstance(calibration, dict) else None
        valid = isinstance(rng, dict) and rng.get("k") and rng.get("f", 0) > 0 and len(rng.get("cam") or []) == 2
        self.range = rng if valid else None
        self._cache = None

    @property
    def calibrated(self):
        """Usable at all: a floor mapping (calibrate.py) or head-size ranging (head_setup.py), or both."""
        return self.H is not None or self.range is not None

    @property
    def head_calibrated(self):
        return self.Hh is not None

    @property
    def range_calibrated(self):
        return self.range is not None

    def ingest(self, packet, rt):
        """Take one `cam` packet received at time `rt` (the recorded receive time, not the wall clock)."""
        self.packet = packet if isinstance(packet, dict) else None
        self.last_rt = rt

    @staticmethod
    def _point(d, ku, kv):
        """(u, v) from detection fields ku, kv if both are present and inside the frame, else None."""
        try:
            u, v = float(d[ku]), float(d[kv])
        except (KeyError, TypeError, ValueError):
            return None
        return (u, v) if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0 else None

    def _from_feet(self, u, v, aspect):
        xy = homography.project(self.H, u, v)
        if xy is None:
            return None
        J = homography.jacobian(self.H, u, v)
        img_cov = np.diag([(PIXEL_SIGMA * aspect) ** 2, PIXEL_SIGMA ** 2])
        return xy, J @ img_cov @ J.T

    def _from_head(self, u, v, aspect):
        """Floor spot below a head top, with height uncertainty laid along the sight line.

        Along one sight line, the floor spot below the head moves linearly with the head's height: at the calibrated
        head height it is the head mapping's answer, at height 0 it is where the line meets the floor (the floor
        mapping's answer, possibly behind the camera). A head HEIGHT_REL_SD taller or shorter therefore moves the
        spot HEIGHT_REL_SD of the way between those two points.
        """
        xy = homography.project(self.Hh, u, v)
        ground = homography.project_line(self.H, u, v)
        if xy is None or ground is None:
            return None
        J = homography.jacobian(self.Hh, u, v)
        img_cov = np.diag([(HEAD_PIXEL_SIGMA * aspect) ** 2, HEAD_PIXEL_SIGMA ** 2])
        ray = np.array([xy[0] - ground[0], xy[1] - ground[1]])
        return xy, J @ img_cov @ J.T + HEIGHT_REL_SD ** 2 * np.outer(ray, ray)

    def height_ratio(self, feet, head):
        """Height of something whose bottom is at image point `feet` and top at `head`, relative to the calibrating
        person's head height (1.0 = as tall as them). None without a head mapping or if either point won't map.

        Along the top's sight line, the floor spot below a point moves linearly with that point's height: the floor
        crossing at height 0, the head mapping's answer at the calibrated height. The bottom's floor position says
        where along that line the object stands, which gives its height.
        """
        if self.Hh is None:
            return None
        bottom = homography.project(self.H, *feet)
        # Both crossings may lie behind the camera (the top of anything lower than the camera is seen looking down,
        # so its sight line meets the head plane behind it); the straight-line relation holds either way.
        top = homography.project_line(self.Hh, *head)
        ground = homography.project_line(self.H, *head)
        if bottom is None or top is None or ground is None:
            return None
        ray = np.array([top[0] - ground[0], top[1] - ground[1]])
        length2 = float(ray @ ray)
        if length2 < 1e-9:
            return None
        return float(np.array([bottom[0] - ground[0], bottom[1] - ground[1]]) @ ray / length2)

    def _project(self, packet, rt):
        w, h = packet.get("w") or 4, packet.get("h") or 3
        aspect = h / w   # u is normalised by width, so the same pixel noise is smaller in u
        out = []
        for d in packet.get("det") or []:
            try:
                s = float(d.get("s", 1.0))
            except (TypeError, ValueError, AttributeError):
                continue
            if s < MIN_SCORE:
                continue
            # Feet whenever they're seen: they are on the floor, so no height guess is involved.
            feet, head = self._point(d, "u", "v"), self._point(d, "hu", "hv")
            if feet and head:
                ratio = self.height_ratio(feet, head)
                if ratio is not None and ratio < MIN_HEIGHT_REL:
                    continue            # too short to be a person
                if ratio is not None and ratio > MAX_HEIGHT_REL:
                    feet = None         # its "bottom" is a desk edge or similar, not the floor: trust the head
            # Preference: feet (no guess at all), then head size (guesses head size, works sitting), then head height.
            got, kind = None, "feet"
            if feet and self.H is not None:
                got = self._from_feet(*feet, aspect)
            if got is None and self.range is not None:
                obs = headrange.observe(d, w, h)
                located = headrange.locate(self.range, *obs) if obs else None
                if located:
                    got, kind = (located[0], np.array(located[1])), "range"
            if got is None and head and self.Hh is not None:
                got, kind = self._from_head(*head, aspect), "head"
            if got is None:
                continue
            xy, cov = got
            cov = cov + np.eye(2) * MIN_SIGMA_M ** 2
            if not np.all(np.isfinite(cov)) or np.sqrt(np.linalg.eigvalsh(cov)[-1]) > MAX_SIGMA_M:
                continue
            out.append(Detection(xy[0], xy[1], cov, s, rt, kind))
        return out

    def detections(self, t):
        """Fresh detections at time `t`, in floor metres."""
        if not self.calibrated or self.packet is None or abs(t - self.last_rt) > MAX_AGE_S:
            return []
        c = self._cache
        if c is None or c[0] is not self.packet or c[1] is not self.H or c[2] is not self.Hh or c[3] is not self.range:
            c = self._cache = (self.packet, self.H, self.Hh, self.range, self._project(self.packet, self.last_rt))
        return c[4]

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
        """Log-likelihood of each grid cell given a matched detection; 0 at the detection, WEIGHT*log(OUTLIER) far.

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
        age = None if self.last_rt is None else round(t - self.last_rt, 1)
        dets = self.detections(t)
        return {"calibrated": self.calibrated, "head_calibrated": self.head_calibrated,
                "floor_calibrated": self.H is not None, "range_calibrated": self.range_calibrated, "age": age,
                # Placed from the head (size or height) because no usable feet were seen.
                "seen": len(dets), "seen_by_head": sum(1 for d in dets if d.kind in ("head", "range")),
                "used": self.last_used}
