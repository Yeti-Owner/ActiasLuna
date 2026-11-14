"""Where someone is from the size and left/right position of their head in the image (plain Python, no numpy).

A head (or face, or head-and-shoulders) looks smaller the further it is: its box height in the image, as a fraction
of the frame, is k / Z, with Z its distance along the camera's line of sight and k a constant per detector kind (each
detector draws its boxes differently). Its left/right offset gives the sideways distance: X = Z * x_off / f, with
x_off the box centre's offset from the image centre and f the focal length, both in units of frame height.
Unlike the floor and head-height mappings this needs neither feet in view nor a guess of anyone's height, so it also
works for people sitting down.

Setup (camera/head_setup.py): the camera's floor position is measured, then someone stands on 2-3 known spots. solve()
finds which way the camera faces, its focal length, its left/right handedness in room coordinates, and k per kind.
"""
from __future__ import annotations

import math

KINDS = ("face", "upper", "body")   # detector kinds whose box size tracks distance ("moving" blobs don't)
RANGE_REL_SD = 0.12                 # heads, faces and shoulders vary about this much in size between people
BEARING_SD = math.radians(3.0)      # left/right direction error (box centring)
MIN_SIZE = 0.02                     # boxes smaller than this fraction of the frame are too far to range
EDGE = 0.01                         # a box this close to the top or bottom of the frame is cut off: its size lies
MIN_FORWARD_M = 0.3                 # nobody stands closer than this in front of the lens
YAW_STEP = math.radians(0.5)


def observe(det: dict, frame_w: float, frame_h: float):
    """(kind, size, x_off) for a detection record from luna_camera.person_record, or None if it can't be ranged.

    size: box height as a fraction of the frame height; x_off: box centre's offset from the image centre, in frame
    heights (right is positive).
    """
    try:
        kind, size, top = det.get("k"), float(det["bh"]), float(det["by"])
        cx = float(det["bx"]) + float(det["bw"]) / 2
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if kind not in KINDS or not size >= MIN_SIZE or top <= EDGE or top + size >= 1.0 - EDGE:
        return None
    return kind, size, (cx - 0.5) * frame_w / frame_h


def _axes(yaw: float):
    """Camera forward and image-right directions on the floor for facing `yaw` (right before handedness)."""
    return (math.cos(yaw), math.sin(yaw)), (-math.sin(yaw), math.cos(yaw))


def _fit_f(cam, spots, yaw: float, hand: int):
    """Best focal length for a facing and handedness, by least squares; None if any spot is behind the camera."""
    fwd, right = _axes(yaw)
    num = den = 0.0
    ratios = []
    for (x, y), x_off in spots:
        vx, vy = x - cam[0], y - cam[1]
        forward = vx * fwd[0] + vy * fwd[1]
        if forward < MIN_FORWARD_M:
            return None
        r = hand * (vx * right[0] + vy * right[1]) / forward
        ratios.append((r, x_off))
        num += r * x_off
        den += r * r
    if den < 1e-9 or num <= 0:
        return None
    f = num / den
    err = sum((x_off - f * r) ** 2 for r, x_off in ratios)
    return f, err


def solve(cam, samples):
    """Fit the camera's facing, focal length and handedness, and the size constant per kind.

    cam: measured camera floor position (x, y). samples: [{"xy": (x, y) where the person stood,
    "obs": {kind: (size, x_off)}}]. Needs 2+ spots at different left/right positions. Returns a JSON-ready dict,
    or raises ValueError.
    """
    spots = []
    for s in samples:
        obs = s.get("obs") or {}
        if obs:
            # Left/right from any kind: all boxes are centred on the same person.
            x_offs = [o[1] for o in obs.values()]
            spots.append((tuple(s["xy"]), sorted(x_offs)[len(x_offs) // 2]))
    if len(spots) < 2:
        raise ValueError("need 2+ spots where the camera saw you")
    if max(s[1] for s in spots) - min(s[1] for s in spots) < 0.05:
        raise ValueError("spots are all straight ahead: stand on one left of view and one right of view")
    best = None
    for hand in (1, -1):
        steps = int(2 * math.pi / YAW_STEP)
        for i in range(steps):
            got = _fit_f(cam, spots, i * YAW_STEP, hand)
            if got and (best is None or got[1] < best[3]):
                best = (i * YAW_STEP, hand, got[0], got[1])
    if best is None:
        raise ValueError("no camera facing puts every spot in front of it: check the camera position")
    yaw, hand = best[0], best[1]
    fine = YAW_STEP / 25
    for i in range(-25, 26):   # refine around the coarse answer
        got = _fit_f(cam, spots, yaw + i * fine, hand)
        if got and got[1] < best[3]:
            best = (yaw + i * fine, hand, got[0], got[1])
    yaw, hand, f, err = best
    fwd, _ = _axes(yaw)
    ks = {}
    for s in samples:
        vx, vy = s["xy"][0] - cam[0], s["xy"][1] - cam[1]
        forward = vx * fwd[0] + vy * fwd[1]
        for kind, (size, _) in (s.get("obs") or {}).items():
            ks.setdefault(kind, []).append(size * forward)
    k = {kind: sorted(v)[len(v) // 2] for kind, v in ks.items()}
    rms_x = math.sqrt(err / len(spots))
    return {"version": 1, "cam": [round(cam[0], 4), round(cam[1], 4)], "yaw": round(yaw % (2 * math.pi), 6),
            "f": round(f, 5), "hand": hand, "k": {kind: round(v, 5) for kind, v in k.items()},
            "hfov_deg": round(math.degrees(2 * math.atan(0.5 * 4 / 3 / f)), 1),   # for a 4:3 frame, as a sanity check
            "rms_deg": round(math.degrees(math.atan(rms_x / f)), 2), "spots": len(spots)}


def locate(cal: dict, kind: str, size: float, x_off: float):
    """Floor (x, y) and 2x2 covariance (nested lists) for one ranged detection, or None.

    The uncertainty is RANGE_REL_SD of the distance along the sight line (head sizes differ) and BEARING_SD across it.
    """
    k = (cal.get("k") or {}).get(kind)
    if not k or size < MIN_SIZE:
        return None
    forward = k / size
    lateral = forward * x_off / cal["f"]
    fwd, right = _axes(cal["yaw"])
    hand = cal.get("hand", 1)
    x = cal["cam"][0] + forward * fwd[0] + hand * lateral * right[0]
    y = cal["cam"][1] + forward * fwd[1] + hand * lateral * right[1]
    dist = math.hypot(x - cal["cam"][0], y - cal["cam"][1])
    if dist < MIN_FORWARD_M:
        return None
    ux, uy = (x - cal["cam"][0]) / dist, (y - cal["cam"][1]) / dist
    sr, st = RANGE_REL_SD * dist, BEARING_SD * dist
    cov = [[sr * sr * ux * ux + st * st * uy * uy, (sr * sr - st * st) * ux * uy],
           [(sr * sr - st * st) * ux * uy, sr * sr * uy * uy + st * st * ux * ux]]
    return (x, y), cov
