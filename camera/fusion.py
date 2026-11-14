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
from typing import Optional

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
# Radio-only uncertainty runs overconfident (a phone sits 2 m off at unc 0.5 m), which put its own holder outside the
# gate. Floored for matching only; the joint assignment and AMBIGUITY keep the wider gate from swapping people.
MIN_TRACK_SD_M = 0.8
# A phone gets its match only if every assignment that pairs it differently scores at least MARGIN worse (score:
# GATE_SIGMAS^2 - d^2 per pair, d in sigmas). Smaller trusts closer calls and swaps people more often.
MARGIN = 1.5
# OUTLIER and WEIGHT set how hard the camera pulls. In simulation (tools/sim_nodes.py --camera) stronger is better
# all the way to WEIGHT=4, OUTLIER=0.02, but that camera is ideal; these sit just below for a real one. Safe only
# because matches gate on the radio-only track and are dropped when ambiguous (assign).
# Override as camera.fusion.WEIGHT (the module the tracker imports), not a separately imported `fusion`.
OUTLIER = 0.02         # chance a matched detection is wrong; keeps a bad match from collapsing the filter
WEIGHT = 3.0           # scale on the camera's log-likelihood
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
# Bearing: when the feet aren't seen, use only the direction to the person (any visible part gives it) and let the
# radio decide how far along that line they are. Needs the camera's floor position (pose, camera_xy or head_range).
# Replaces the head-height guess, which is wrong for anyone sitting or of another height.
BEARING = True
BEARING_SD_DEG = 3.0      # direction noise: box-centre jitter plus the box centre sitting off the body's middle
# Scanned objects at least this tall hide a whole person, so the bearing line stops at them; desks and chairs don't.
OCCLUDER_MIN_H_M = 1.8


@dataclass
class Detection:
    x: float
    y: float
    cov: np.ndarray    # 2x2 floor covariance, m^2
    score: float
    rt: float
    # "feet": foot point (floor mapping); "range": head size + left/right (headrange.py); "head": head top (head
    # mapping); "bearing": direction only, x/y is then the camera's floor position and cov is unused
    kind: str = "feet"
    bearing: Optional[float] = None   # map angle (radians) from the camera, "bearing" only


def _wrap(a):
    return (a + np.pi) % (2 * np.pi) - np.pi


def _inside(x, y, poly):
    inside = False
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def _camera_xy(calibration):
    """The camera's floor position from a calibration record, or None."""
    if not isinstance(calibration, dict):
        return None
    for got in ((calibration.get("pose") or {}).get("x"), (calibration.get("pose") or {}).get("y")), \
            tuple(calibration.get("camera_xy") or ()), tuple((calibration.get("head_range") or {}).get("cam") or ()):
        try:
            x, y = float(got[0]), float(got[1])
        except (IndexError, TypeError, ValueError):
            continue
        if np.isfinite(x) and np.isfinite(y):
            return x, y
    return None


class CameraFusion:
    def __init__(self, calibration=None):
        self.H = None            # floor mapping: image foot point -> floor
        self.Hh = None           # head mapping: image head top -> floor spot below it (optional)
        self.range = None        # head-size ranging from camera/head_setup.py (optional; works without H)
        self.cam_xy = None       # camera floor position, for bearings
        self._scene = None       # (polygon, occluder footprints) from set_scene
        self._segs = np.zeros((0, 4))
        self._vis = None         # (grid_x, cam_xy, segs id, visible mask) for loglik
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
        self.cam_xy = _camera_xy(calibration) if self.calibrated else None
        self._cache = None
        self._scene = None   # walls count only when the camera is inside the room, so rebuild against the new spot

    def set_scene(self, polygon, objects):
        """Room outline and scanned objects ({"footprint", "height"}), for cutting bearing lines at what hides people.
        Cheap when unchanged, so it can be called every tick."""
        poly = [tuple(map(float, p)) for p in polygon or [] if len(p) >= 2]
        tall = [[tuple(map(float, p)) for p in o.get("footprint") or []] for o in objects or []
                if isinstance(o, dict) and float(o.get("height") or 0) >= OCCLUDER_MIN_H_M]
        scene = (poly, [fp for fp in tall if len(fp) >= 3])
        if scene == self._scene:
            return
        self._scene = scene
        segs = []
        if self.cam_xy is not None:
            cx, cy = self.cam_xy
            # A camera placed on or outside a wall would see every cell through it: then walls don't occlude.
            rings = ([poly] if len(poly) >= 3 and _inside(cx, cy, poly) else [])
            rings += [fp for fp in scene[1] if not _inside(cx, cy, fp)]
            for ring in rings:
                segs += [(x0, y0, x1, y1) for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1])]
        self._segs = np.array(segs, float).reshape(-1, 4)
        self._vis = None

    def visible(self, gx, gy):
        """True where the straight line from the camera to the point crosses no wall or tall object."""
        px, py = np.asarray(gx, float), np.asarray(gy, float)
        if self.cam_xy is None or not len(self._segs):
            return np.ones(px.shape, bool)
        cx, cy = self.cam_xy
        ax, ay, bx, by = (self._segs[:, i] for i in range(4))
        px, py = px[..., None], py[..., None]

        def side(x0, y0, x1, y1, x, y):
            return (x1 - x0) * (y - y0) - (y1 - y0) * (x - x0)

        crosses = ((side(cx, cy, px, py, ax, ay) * side(cx, cy, px, py, bx, by) < 0)
                   & (side(ax, ay, bx, by, cx, cy) * side(ax, ay, bx, by, px, py) < 0))
        return ~crosses.any(axis=-1)

    def _bearing(self, d):
        """Map angle from the camera to a detection, from its box centre (else head top, else feet), or None.

        Every image point on a person lies in the upright plane through the camera and that person, and the floor
        mapping sends it to where its sight line crosses the floor: on the line from the camera's floor spot towards
        the person, or behind the camera for points above the horizon (then the direction flips).
        """
        if not BEARING or self.H is None or self.cam_xy is None:
            return None
        pts = []
        try:
            pts.append((float(d["bx"]) + float(d["bw"]) / 2, float(d["by"]) + float(d["bh"]) / 2))
        except (KeyError, TypeError, ValueError):
            pass
        pts += [p for p in (self._point(d, "hu", "hv"), self._point(d, "u", "v")) if p]
        cx, cy = self.cam_xy
        for u, v in pts:
            p = self.H @ np.array([u, v, 1.0])
            if abs(p[2]) <= 1e-12:
                continue
            dx, dy = p[0] / p[2] - cx, p[1] / p[2] - cy
            if p[2] < 0:
                dx, dy = -dx, -dy
            if dx * dx + dy * dy > 1e-12:
                return float(np.arctan2(dy, dx))
        return None

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
            # Preference: feet (no guess at all), then head size (guesses head size, works sitting), then direction
            # only (no guess), then head height (guesses height and standing).
            got, kind = None, "feet"
            if feet and self.H is not None:
                got = self._from_feet(*feet, aspect)
            if got is None and self.range is not None:
                obs = headrange.observe(d, w, h)
                located = headrange.locate(self.range, *obs) if obs else None
                if located:
                    got, kind = (located[0], np.array(located[1])), "range"
            if got is None:
                b = self._bearing(d)
                if b is not None:
                    out.append(Detection(self.cam_xy[0], self.cam_xy[1], None, s, rt, "bearing", b))
                    continue
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
        """Match fresh detections to phone tracks, one to one, all phones at once.

        tracks: {key: (x, y, unc)} with unc the track's 1-sigma radius in metres (radio only: gating on an estimate
        the camera already pulled locks a wrong match in). Returns {key: Detection} for the tracks with a clear match.

        Joint, not nearest-first, so a phone whose radio drifted onto another phone's holder leaves that holder to
        their own phone. A phone whose pairing some rival assignment explains nearly as well (MARGIN) gets nothing:
        with the holder and someone beside them, or two phones near one person, a guess pulls a phone onto a stranger,
        which is worse than no help.
        """
        dets = self.detections(t)
        dist = {}
        for k, (x, y, unc) in tracks.items():
            sd = max(float(unc), MIN_TRACK_SD_M)
            for i, det in enumerate(dets):
                if det.kind == "bearing":
                    m = self._bearing_sigmas(det, x, y, sd)
                else:
                    S = det.cov + np.eye(2) * sd ** 2
                    d = np.array([det.x - x, det.y - y])
                    m = float(np.sqrt(d @ np.linalg.solve(S, d)))
                if m <= GATE_SIGMAS:
                    dist[i, k] = GATE_SIGMAS ** 2 - m * m
        order = [k for k in tracks if any((i, k) in dist for i in range(len(dets)))]

        def best(forbid=None):
            """Best total score and its {key: det index}; forbid=(key, det index) bars that pairing."""
            top = [-1.0, {}]

            def search(n, used, score, chosen):
                if score + (len(order) - n) * GATE_SIGMAS ** 2 <= top[0]:
                    return
                if n == len(order):
                    top[0], top[1] = score, dict(chosen)
                    return
                k = order[n]
                for i in range(len(dets)):
                    if (i, k) in dist and i not in used and forbid != (k, i):
                        chosen[k] = i
                        search(n + 1, used | {i}, score + dist[i, k], chosen)
                        del chosen[k]
                search(n + 1, used, score, chosen)

            search(0, frozenset(), 0.0, {})
            return top

        score, match = best()
        out = {k: dets[i] for k, i in match.items() if best((k, i))[0] <= score - MARGIN}
        self.last_used = len(out)
        return out

    @staticmethod
    def _bearing_sigmas(det, x, y, sd):
        """How many sigmas the point (x, y), known to sd metres, lies off a bearing line; inf behind the camera."""
        dx, dy = x - det.x, y - det.y
        r = float(np.hypot(dx, dy))
        delta = abs(float(_wrap(np.arctan2(dy, dx) - det.bearing)))
        if r < 1e-6 or delta > np.pi / 2:
            return np.inf
        ang_sd = np.sqrt(np.radians(BEARING_SD_DEG) ** 2 + (sd ** 2 + MIN_SIGMA_M ** 2) / (r * r))
        return delta / ang_sd

    def _bearing_loglik(self, det, grid_x, grid_y):
        gx, gy = np.asarray(grid_x, float), np.asarray(grid_y, float)
        dx, dy = gx - det.x, gy - det.y
        r = np.maximum(np.hypot(dx, dy), 0.1)
        delta = _wrap(np.arctan2(dy, dx) - det.bearing)
        # Near the camera the line is never thinner than MIN_SIGMA_M across.
        m = delta ** 2 / (np.radians(BEARING_SD_DEG) ** 2 + (MIN_SIGMA_M / r) ** 2)
        like = np.where(np.abs(delta) < np.pi / 2, np.exp(-0.5 * m), 0.0)
        v = self._vis
        if v is None or v[0] is not grid_x or v[1] != self.cam_xy or v[2] is not self._segs:
            v = self._vis = (grid_x, self.cam_xy, self._segs, self.visible(gx, gy))
        like = np.where(v[3], like, 0.0)   # the person seen can't be behind a wall or a wardrobe
        return WEIGHT * np.log((1.0 - OUTLIER) * like + OUTLIER)

    def loglik(self, det, grid_x, grid_y):
        """Log-likelihood of each grid cell given a matched detection; 0 at the detection, WEIGHT*log(OUTLIER) far.

        grid_x, grid_y: arrays of cell-centre coordinates in metres (any matching shape). A bearing is 0 along its
        line, up to the first wall or tall object.
        """
        if det is None:
            return np.zeros(np.shape(grid_x))
        if det.kind == "bearing":
            return self._bearing_loglik(det, grid_x, grid_y)
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
                # Direction only: feet hidden, so the radio decides the distance.
                "seen_by_bearing": sum(1 for d in dets if d.kind == "bearing"), "used": self.last_used}
