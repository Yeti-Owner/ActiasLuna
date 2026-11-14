"""Image-to-floor mapping for the laptop camera (numpy only, safe to import from the server).

The camera reports where a person's feet touch the image as normalised coordinates (u across, v down, both 0..1,
so the mapping does not depend on resolution). A homography maps that point onto the floor in metres.
"""

import numpy as np

VERSION = 1
MIN_POINTS = 4
DEGENERATE = 1e-9   # relative singular value below which the calibration points are treated as collinear


def _normaliser(pts):
    """Similarity transform that centres points and scales their mean distance to sqrt(2) (Hartley)."""
    c = pts.mean(axis=0)
    d = np.sqrt(((pts - c) ** 2).sum(axis=1)).mean()
    s = np.sqrt(2.0) / d if d > 0 else 1.0
    return np.array([[s, 0.0, -s * c[0]], [0.0, s, -s * c[1]], [0.0, 0.0, 1.0]])


def _apply(H, pts):
    """Map Nx2 points through H. Returns (Nx2 points, N homogeneous scales)."""
    p = np.hstack([pts, np.ones((len(pts), 1))]) @ H.T
    return p[:, :2] / p[:, 2:3], p[:, 2]


def fit(img_pts, floor_pts):
    """Fit H with image (u, v) -> floor (x, y) from 4 or more matching points (normalised DLT).

    H is scaled so the calibration points sit in front of the camera (positive homogeneous scale); `project`
    uses that to reject points above the horizon.
    """
    img = np.asarray(img_pts, float)
    flo = np.asarray(floor_pts, float)
    if img.ndim != 2 or img.shape[1] != 2 or img.shape != flo.shape:
        raise ValueError("need two matching Nx2 point lists")
    if len(img) < MIN_POINTS:
        raise ValueError(f"need at least {MIN_POINTS} points, got {len(img)}")
    ti, tf = _normaliser(img), _normaliser(flo)
    a, _ = _apply(ti, img)
    b, _ = _apply(tf, flo)
    rows = []
    for (u, v), (x, y) in zip(a, b):
        rows.append([-u, -v, -1.0, 0.0, 0.0, 0.0, x * u, x * v, x])
        rows.append([0.0, 0.0, 0.0, -u, -v, -1.0, y * u, y * v, y])
    _, s, vt = np.linalg.svd(np.array(rows))
    # A good fit has a one-dimensional null space; a second tiny singular value means collinear points.
    if s[7] < DEGENERATE * s[0]:
        raise ValueError("calibration points are degenerate (three or more in a line?)")
    H = np.linalg.inv(tf) @ vt[-1].reshape(3, 3) @ ti
    H /= np.linalg.norm(H)
    _, w = _apply(H, img)
    if np.median(w) < 0:
        H = -H
    return H


def project(H, u, v):
    """Floor (x, y) for image point (u, v), or None if the point is at or above the horizon."""
    p = H @ np.array([u, v, 1.0])
    if p[2] <= 1e-12:
        return None
    return float(p[0] / p[2]), float(p[1] / p[2])


def project_line(H, u, v):
    """Where the sight line through (u, v) crosses the plane, in front of the camera or behind it.

    `project` rejects points behind the camera because nobody stands there; this keeps them, because the crossing
    is still a point on the sight line (fusion.py uses it to know which way that line runs). None when the line is
    parallel to the plane.
    """
    p = H @ np.array([u, v, 1.0])
    if abs(p[2]) <= 1e-12:
        return None
    return float(p[0] / p[2]), float(p[1] / p[2])


def jacobian(H, u, v):
    """2x2 derivative of floor (x, y) with respect to image (u, v); turns image noise into floor noise."""
    p = H @ np.array([u, v, 1.0])
    w2 = p[2] * p[2]
    return np.array([
        [(H[0, 0] * p[2] - p[0] * H[2, 0]) / w2, (H[0, 1] * p[2] - p[0] * H[2, 1]) / w2],
        [(H[1, 0] * p[2] - p[1] * H[2, 0]) / w2, (H[1, 1] * p[2] - p[1] * H[2, 1]) / w2],
    ])


def rms_error(H, img_pts, floor_pts):
    """Root-mean-square floor error in metres of H over the given points."""
    got, _ = _apply(H, np.asarray(img_pts, float))
    return float(np.sqrt(((got - np.asarray(floor_pts, float)) ** 2).sum(axis=1).mean()))


def leave_one_out_error(img_pts, floor_pts):
    """RMS error predicting each point from a fit to the others: an honest accuracy figure. None if < 5 points."""
    img = np.asarray(img_pts, float)
    flo = np.asarray(floor_pts, float)
    if len(img) <= MIN_POINTS:
        return None
    errs = []
    for i in range(len(img)):
        keep = np.arange(len(img)) != i
        try:
            H = fit(img[keep], flo[keep])
        except ValueError:
            return None
        xy = project(H, *img[i])
        if xy is None:
            return None
        errs.append((xy[0] - flo[i, 0]) ** 2 + (xy[1] - flo[i, 1]) ** 2)
    return float(np.sqrt(np.mean(errs)))


def to_dict(H, img_pts, floor_pts, image_size=None):
    """JSON-ready calibration record. Stores point coordinates only, never images."""
    return {
        "version": VERSION,
        "H": [[float(c) for c in row] for row in H],
        "points": [{"u": float(u), "v": float(v), "x": float(x), "y": float(y)}
                   for (u, v), (x, y) in zip(img_pts, floor_pts)],
        "rms_m": rms_error(H, img_pts, floor_pts),
        "loo_m": leave_one_out_error(img_pts, floor_pts),
        "image_size": list(image_size) if image_size else None,
    }


def from_dict(d):
    """H from a calibration record. Refits from the stored points so the record's points stay the source of truth."""
    pts = d.get("points") or []
    if len(pts) >= MIN_POINTS:
        return fit([(p["u"], p["v"]) for p in pts], [(p["x"], p["y"]) for p in pts])
    return np.asarray(d["H"], float)


def head_from_dict(d):
    """The head-height mapping (image head top -> floor spot below it) from a calibration record, or None.

    calibrate.py stores it under "head" in the same shape as the floor record, when 4+ marks were taken by standing
    on them (space), so each head point pairs with the floor spot its owner stood on.
    """
    head = d.get("head") if isinstance(d, dict) else None
    if not isinstance(head, dict):
        return None
    try:
        return from_dict(head)
    except (ValueError, KeyError, TypeError):
        return None
