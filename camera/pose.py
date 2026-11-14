"""Camera calibration from where the laptop sits and which way it looks (numpy only, safe to import from the server).

The /camera page gives the camera's floor position, a point it looks towards and a height preset. With a typical
webcam field of view that is a full pinhole camera, except for the lid's tilt (pitch). calibration() turns a pose into
the same record calibrate.py makes (floor points and head-height points, see homography.to_dict), so fusion.py needs
no changes.

Pitch is refined from people seen whole (refine): a person's height measured from their feet and head points depends
on the assumed pitch, and only the right pitch makes everyone the same height at every distance. That uses the
spread of heights, not their size, so loose detector boxes (taller than the person) don't bias it.

Coordinates: the floor plan is a top view drawn with y down, so (x, y, up) is left-handed and the image's right is
the facing direction turned clockwise on the plan: (-sin yaw, cos yaw).
"""
import math

import numpy as np

try:
    from . import homography
except ImportError:  # run as a plain script from inside camera/
    import homography

# Tunables: module-level and read when used.
HFOV_DEG = 68.0          # horizontal field of view of a typical 720p laptop webcam
DEFAULT_PITCH_DEG = 5.0  # positive looks up: a lid leaned back a little past upright
HEAD_H_M = 1.7           # head-height mapping is made for this head height (fusion.HEIGHT_REL_SD covers the rest)
DEFAULT_ASPECT = 16 / 9  # frame width / height until the camera has sent a packet
GRID_M = 0.5             # spacing of the synthetic calibration points
# refine(): needs this many whole-body sightings, spread over this much of the frame height (near and far), and
# must beat the current pitch's height spread by REFINE_GAIN before it moves.
REFINE_MIN = 30
REFINE_MIN_SPREAD_V = 0.12
REFINE_GAIN = 0.9
PITCH_RANGE_DEG = (-25.0, 35.0)
PITCH_STEP_DEG = 0.25


def make_pose(x, y, look_x, look_y, height, pitch_deg=None, hfov_deg=None):
    """JSON-ready pose from the camera's floor position, a floor point it looks towards and its height (metres)."""
    if math.hypot(look_x - x, look_y - y) < 0.05:
        raise ValueError("the look-at point must be away from the camera")
    if not 0.1 <= height <= 3.0:
        raise ValueError("camera height must be 0.1-3 m")
    return {"x": float(x), "y": float(y), "yaw": math.atan2(look_y - y, look_x - x), "height": float(height),
            "pitch_deg": DEFAULT_PITCH_DEG if pitch_deg is None else float(pitch_deg),
            "hfov_deg": HFOV_DEG if hfov_deg is None else float(hfov_deg)}


def _focal(pose, aspect):
    """Focal length in frame widths (u) and frame heights (v)."""
    fx = 0.5 / math.tan(math.radians(pose["hfov_deg"]) / 2)
    return fx, fx * aspect


def project(pose, aspect, x, y, z=0.0, clip=True):
    """Normalised image (u, v) of a room point, or None if it is behind the camera (or out of frame, with clip)."""
    yaw, p = pose["yaw"], math.radians(pose["pitch_deg"])
    fwd = (math.cos(yaw) * math.cos(p), math.sin(yaw) * math.cos(p), math.sin(p))
    right = (-math.sin(yaw), math.cos(yaw), 0.0)
    up = (-math.cos(yaw) * math.sin(p), -math.sin(yaw) * math.sin(p), math.cos(p))
    d = (x - pose["x"], y - pose["y"], z - pose["height"])
    depth = sum(a * b for a, b in zip(d, fwd))
    if depth <= 0.2:
        return None
    fx, fy = _focal(pose, aspect)
    u = 0.5 + fx * sum(a * b for a, b in zip(d, right)) / depth
    v = 0.5 - fy * sum(a * b for a, b in zip(d, up)) / depth
    return (u, v) if not clip or (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0) else None


def calibration(pose, polygon, aspect=None):
    """Calibration record for fusion.CameraFusion: floor points and head points on a grid over the room's bounding box
    (plus a margin), from the pose. Points outside the frame are kept: each mapping is one plane's geometry, which
    holds for the whole image, and a laptop on a desk often sees no floor near it. Raises ValueError if the room is
    behind the camera."""
    aspect = aspect or DEFAULT_ASPECT
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    feet_img, feet_floor, head_img, head_floor = [], [], [], []
    for gx in np.arange(min(xs) - 1.0, max(xs) + 1.0 + 1e-9, GRID_M):
        for gy in np.arange(min(ys) - 1.0, max(ys) + 1.0 + 1e-9, GRID_M):
            feet = project(pose, aspect, gx, gy, clip=False)
            if feet:
                feet_img.append(feet)
                feet_floor.append((float(gx), float(gy)))
            top = project(pose, aspect, gx, gy, HEAD_H_M, clip=False)
            if top:
                head_img.append(top)
                head_floor.append((float(gx), float(gy)))
    if len(feet_img) < homography.MIN_POINTS:
        raise ValueError("the room is behind the camera: click where it looks")
    record = homography.to_dict(homography.fit(feet_img, feet_floor), feet_img, feet_floor)
    if len(head_img) >= homography.MIN_POINTS:
        record["head"] = homography.to_dict(homography.fit(head_img, head_floor), head_img, head_floor)
    record.update(source="pose", pose=pose, aspect=round(aspect, 4))
    return record


def _height_ratios(pitch, a_f, b_f, a_h, b_h):
    """Measured height / camera height for each sighting at pitch (radians); NaN where the feet ray misses the floor.

    a, b: the point's offset from the image centre in focal lengths (right, up). A ray's horizontal length and climb
    don't depend on the yaw, so neither does this.
    """
    s, c = math.sin(pitch), math.cos(pitch)
    dz_f = s + b_f * c
    dz_h = s + b_h * c
    l_f = np.hypot(c - b_f * s, a_f)
    l_h = np.hypot(c - b_h * s, a_h)
    with np.errstate(divide="ignore", invalid="ignore"):
        g = 1.0 + (l_f / -dz_f) * (dz_h / l_h)
    return np.where(dz_f < 0, g, np.nan)


def _spread(g):
    """Spread of log heights: scale-free (loose boxes, the camera height) and, unlike spread / median, unbiased."""
    g = g[np.isfinite(g) & (g > 0)]
    if len(g) < REFINE_MIN:
        return math.inf
    lg = np.log(g)
    return float(np.std(lg[np.abs(lg - np.median(lg)) < 0.3]))


def refine(pose, sightings):
    """Pose with its pitch fitted to whole-body sightings [(u, v, hu, hv, aspect)], or None to keep it.

    Each sighting is one person seen with feet and head top. Only a pitch that makes their heights agree at every
    distance clearly better than the current one (REFINE_GAIN) is taken. In simulation it lands within about 1.5
    degrees (people 1.7 +- 0.08 m): near the camera tall people's heads leave the frame, which skews the sample.
    """
    if len(sightings) < REFINE_MIN:
        return None
    arr = np.asarray(sightings, float)
    if np.ptp(arr[:, 1]) < REFINE_MIN_SPREAD_V:
        return None
    fx = 0.5 / math.tan(math.radians(pose["hfov_deg"]) / 2)
    fy = fx * arr[:, 4]
    args = ((arr[:, 0] - 0.5) / fx, -(arr[:, 1] - 0.5) / fy, (arr[:, 2] - 0.5) / fx, -(arr[:, 3] - 0.5) / fy)
    now = _spread(_height_ratios(math.radians(pose["pitch_deg"]), *args))
    best = (now, pose["pitch_deg"])
    for deg in np.arange(PITCH_RANGE_DEG[0], PITCH_RANGE_DEG[1] + 1e-9, PITCH_STEP_DEG):
        sp = _spread(_height_ratios(math.radians(deg), *args))
        if sp < best[0]:
            best = (sp, float(deg))
    if not math.isfinite(best[0]) or best[0] > REFINE_GAIN * now or abs(best[1] - pose["pitch_deg"]) < 0.5:
        return None
    return dict(pose, pitch_deg=round(best[1], 2))
