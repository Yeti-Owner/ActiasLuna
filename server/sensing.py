"""Device-free sensing: locate one person without a phone from how they disturb the board-to-board links.

While sensing is on every board pings the others (firmware main.c, LUNA_SUB "sense=1") and each receiving board
reports per directed link (tx board -> rx board): RSSI mean/sd, CSI amplitude in 13 subcarrier bins ("cs") and
frame-to-frame CSI change ("cm"). Per link and window, against an empty-room baseline, the features are:

  A  attenuation (baseline RSSI - RSSI, dB): the body blocking the direct path.
  V  log(RSSI sd / empty sd): even a standing person sways and breathes, modulating their reflection more the closer
     they are to the link. Strongest feature in simulation.
  P  V as a pattern: u = (sd / empty sd)^2 - 1 per link is modelled as alpha * G_link(p) with an unknown activity level
     alpha per cell (like radiomap's phone offset) under a log-normal prior, so a very still person isn't read as
     "far from every link".
  M  log(cm / empty cm): CSI frame-to-frame change. AGC normalises CSI level, so it sees less than V.
  S  CSI shape change (rms dB across bins): weak at 20 MHz.

Model per link and feature: E[F] = empty mean + a * g(p). A uses g = exp(-x / w) with x = |p-tx| + |p-rx| - |tx-rx|
(Fresnel ellipse: blocking needs the body on the line). V/M/S/P use g = (|tx-rx| / (|p-tx| * |p-rx|))^w (bistatic
reflection relative to the direct path). a and w are fitted from a phone walk (the walker's own body is the target),
plus a residual field. Grid and motion model are radiomap's, so phone tracking can add this log-likelihood
("fuse_body"). Assumes at most one person. RSSI-only links (no CSI) use A and V.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Dict, List, Optional, Tuple

import radiomap as rmap

N_BINS = 13
WIN_S = 1.0
KEEP_S = 30.0
MIN_N = 3
# Per feature: (default amplitude, default shape parameter, shape grid, sd floor). Defaults apply until a walk exists.
MODEL = {
    "A": (2.7, 0.08, (0.04, 0.08, 0.12, 0.2, 0.35, 0.6), 0.6),
    "V": (1.0, 1.0, (0.5, 0.75, 1.0, 1.5, 2.0, 3.0), 0.12),
    "S": (0.15, 1.0, (0.5, 1.0, 2.0), 0.05),
    "M": (0.2, 1.0, (0.5, 1.0, 2.0), 0.1),
    "P": (3.0, 1.0, (0.5, 0.75, 1.0, 1.5, 2.0, 3.0), 0.0),
}
FEAT_IDX = {"A": 0, "V": 1, "S": 2, "M": 3}
P_REL_FLOOR = 0.15
ALPHA_SD = 0.8
ALPHAS = (0.08, 0.15, 0.25, 0.4, 0.6, 1.0, 1.6, 2.5)
U_MAX = 30.0
RES_LEN = 0.6
RES_SHRINK = 1.5
MIN_FIT = 20
HUBER = 2.5
TEMPER = 0.8
MOVE_V = math.log(4.0)
PRESENT_MARGIN = 3.0
# Each update counts in proportion to how strongly it says someone is here (best cell vs empty room, log-lik units,
# over GATE). A very still person looks almost like an empty room; without the gate the filter would read that as
# "far from every link" and drag the dot away instead of holding where they stopped.
GATE = 6.0
GATE_MIN = 0.05
SD_MIN = 0.3
# Motion speeds (m/s). Still is deliberately slow: a person who stopped is where they stopped, and standing still is
# exactly when the fluctuation features are weakest.
SPEED_MOVING, SPEED_STILL, SPEED_UNKNOWN = 1.2, 0.35, 0.9


def key(tx: str, rx: str) -> str:
    return f"{tx}>{rx}"


def _shape_db(bins: List[float]) -> List[float]:
    db = [20 * math.log10(max(0.5, b)) for b in bins]
    mean = sum(db) / len(db)
    return [v - mean for v in db]


def _huber(z: float) -> float:
    return z * z if z < HUBER else HUBER * (2 * z - HUBER)


class LinkBuffer:
    """Recent raw samples per directed link: (rt, rssi, frames, cs bins or None, cm or None, rssi sd)."""

    def __init__(self):
        self.s: Dict[str, deque] = {}

    def add(self, tx: str, rx: str, rt: float, r: float, n: int, cs, cm, sd: float = 0.0):
        buf = self.s.setdefault(key(tx, rx), deque())
        bins = [float(c) for c in cs] if isinstance(cs, list) and len(cs) == N_BINS else None
        change = float(cm) if isinstance(cm, (int, float)) and bins else None
        buf.append((rt, r, n, bins, change, float(sd or 0.0)))
        while buf and rt - buf[0][0] > KEEP_S:
            buf.popleft()

    def window(self, t0: float, t1: float) -> Dict[str, list]:
        return window(((link, s) for link, buf in self.s.items() for s in buf), t0, t1)


def link_items(rows) -> List[tuple]:
    """Capture link rows [t, link, rssi, frames, cs, cm, sd] (Tracker._obs) -> [(link, sample)] for window()."""
    return [(link, (t, r, n, cs, cm, sd or 0.0)) for t, link, r, n, cs, cm, sd in rows or []]


def window(items, t0: float, t1: float) -> Dict[str, list]:
    """{link: [rssi, shape dB or None, cm or None, frames, rssi sd]} over samples with t0 <= rt < t1."""
    acc: Dict[str, dict] = {}
    for link, (rt, r, n, cs, cm, sd) in items:
        if n <= 0 or not t0 <= rt < t1:
            continue
        a = acc.get(link)
        if a is None:
            a = acc[link] = {"rn": 0.0, "n": 0, "bins": [0.0] * N_BINS, "cn": 0, "cm": 0.0, "mn": 0, "sq": 0.0}
        a["rn"] += r * n
        a["n"] += n
        a["sq"] += n * (sd * sd + r * r)
        if cs:
            for i, c in enumerate(cs):
                a["bins"][i] += c * n
            a["cn"] += n
            if cm is not None:
                a["cm"] += cm * n
                a["mn"] += n
    out = {}
    for link, a in acc.items():
        if a["n"] < MIN_N:
            continue
        mean = a["rn"] / a["n"]
        shape = _shape_db([b / a["cn"] for b in a["bins"]]) if a["cn"] else None
        change = a["cm"] / a["mn"] if a["mn"] else None
        out[link] = [mean, shape, change, a["n"], math.sqrt(max(0.0, a["sq"] / a["n"] - mean * mean))]
    return out


def windows(items: List[tuple], t0: float, t1: float, w: float = WIN_S, step: Optional[float] = None):
    ordered = sorted(items, key=lambda it: it[1][0])
    step = step or w
    out, t, lo = [], t0, 0
    while t + w <= t1 + 1e-9:
        while lo < len(ordered) and ordered[lo][1][0] < t:
            lo += 1
        hi = lo
        while hi < len(ordered) and ordered[hi][1][0] < t + w:
            hi += 1
        win = window(ordered[lo:hi], t, t + w)
        if win:
            out.append((t + w / 2, win))
        t += step
    return out


def features(win: Dict[str, list], base: dict) -> Dict[str, tuple]:
    """Per link (A, V, S, M, frames), raw (empty-room means not subtracted). S and M are None without CSI."""
    out = {}
    for link, (rssi, shape, change, frames, sd) in win.items():
        b = base.get(link)
        if not b:
            continue
        S = M = None
        if shape and "shape" in b:
            S = math.sqrt(sum((s - m) ** 2 for s, m in zip(shape, b["shape"])) / N_BINS)
        if change is not None and b.get("cm0"):
            M = math.log(max(1e-3, change) / b["cm0"])
        out[link] = (b["r"] - rssi, math.log(max(SD_MIN, sd) / b["sd0"]), S, M, frames)
    return out


def baseline(wins: List[Dict[str, list]]) -> dict:
    """Empty-room baseline per link: mean RSSI and shape, quiet fluctuation level, and every feature's spread."""
    out = {}
    for link in {k for w in wins for k in w}:
        vals = [w[link] for w in wins if link in w]
        if len(vals) < 5:
            continue
        mean = sum(v[0] for v in vals) / len(vals)
        sd0 = max(SD_MIN, sorted(v[4] for v in vals)[len(vals) // 2])
        b = {"r": round(mean, 2), "n": len(vals), "sd0": round(sd0, 3)}
        shapes = [v[1] for v in vals if v[1]]
        if len(shapes) >= 5:
            b["shape"] = [round(sum(s[i] for s in shapes) / len(shapes), 3) for i in range(N_BINS)]
            changes = sorted(v[2] for v in vals if v[2] is not None)
            if changes:
                b["cm0"] = round(max(1e-3, changes[len(changes) // 2]), 4)
        feats = [features({link: v}, {link: b})[link] for v in vals]
        for name, i in FEAT_IDX.items():
            xs = [f[i] for f in feats if f[i] is not None]
            if len(xs) < 5:
                continue
            m = sum(xs) / len(xs)
            b["m" + name] = round(m, 4)
            b["sd" + name] = round(max(MODEL[name][3], math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))), 4)
        out[link] = b
    return out


def _pattern(f: tuple) -> Tuple[float, float]:
    """Fluctuation pattern term for one link: (u, precision) with u = (sd / empty sd)^2 - 1."""
    u = min(U_MAX, math.exp(2 * f[1]) - 1)
    return u, 1.0 / ((1 + max(0.0, u)) * math.sqrt(2.0 / max(4, f[4])) + P_REL_FLOOR) ** 2


def moving(feats: Dict[str, tuple]) -> int:
    levels = [f[1] for f in feats.values() if f[1] is not None]
    if not levels:
        return -1
    return 1 if max(levels) > MOVE_V else 0


def _fit_amplitude(basis, pts, ys: List[float], shapes, a_def: float, w_def: float,
                   lam: float = 2.0) -> Tuple[float, float]:
    """Per shape parameter, least-squares amplitude shrunk toward a_def; return the best (a, w)."""
    best_sse, best_a, best_w = float("inf"), a_def, w_def
    for w in shapes:
        g = [basis(x, y, w) for x, y in pts]
        a = max(0.0, (sum(gi * yi for gi, yi in zip(g, ys)) + lam * a_def) / (sum(gi * gi for gi in g) + lam))
        sse = sum((yi - a * gi) ** 2 for gi, yi in zip(g, ys))
        if sse < best_sse:
            best_sse, best_a, best_w = sse, a, w
    return best_a, best_w


def _residual_field(resid, x, y, inv) -> float:
    w_sum = we_sum = 0.0
    for rx, ry, e in resid:
        w = math.exp(-((x - rx) ** 2 + (y - ry) ** 2) * inv)
        w_sum += w
        we_sum += w * e
    return we_sum / (w_sum + RES_SHRINK)


class DFMap:
    """Per-link feature models on radiomap's grid.

    walk: [{"x", "y", "l": {link: window list}}] from phone walks. use: which features to model.
    """

    def __init__(self, layout: dict, base: dict, walk: Optional[List[dict]] = None,
                 blocked: Optional[rmap.Blocked] = None, use: str = "AVM"):
        self.poly = layout.get("polygon") or []
        self.rx = {name: (float(r["x"]), float(r["y"])) for name, r in layout.get("receivers", {}).items()}
        self.base = base or {}
        # Same cached Grid object as RadioMap; predict/summary (borrowed below) rely on grid, cx and cy.
        self.grid = rmap.grid_of(self.poly)
        if self.grid:
            self.cells, self.cx, self.cy = self.grid.cells, self.grid.cx, self.grid.cy
        else:
            self.cells, self.cx, self.cy = (), [], []
        self.blocked = blocked or []
        self.prior = rmap.cell_prior(self.cells, self.blocked)
        self.links = [k for k in self.base if all(n in self.rx for n in k.split(">"))]
        self.walk = walk or []
        self.use = use
        self.fit: Dict[str, dict] = {}
        self.model: Dict[str, Dict[str, Tuple[List[float], float]]] = {}
        self._build()

    def _basis(self, name: str, link: str):
        tx, rx = link.split(">")
        (ax, ay), (bx, by) = self.rx[tx], self.rx[rx]
        direct = math.hypot(ax - bx, ay - by)
        if name == "A":
            return lambda x, y, w: math.exp(-(math.hypot(x - ax, y - ay) + math.hypot(x - bx, y - by) - direct) / w)
        return lambda x, y, w: (direct / max(0.25, math.hypot(x - ax, y - ay) * math.hypot(x - bx, y - by))) ** w

    def _build(self):
        inv = 1.0 / (2 * RES_LEN ** 2)
        for link in self.links:
            b = self.base[link]
            pts = [(w["x"], w["y"], features({link: w["l"][link]}, self.base)[link])
                   for w in self.walk if w["l"].get(link)]
            fits, models = {}, {}
            for name in self.use:
                if name == "P":
                    if "sdV" in b:
                        self._build_pattern(link, pts, fits, models)
                    continue
                if "sd" + name not in b:
                    continue
                i = FEAT_IDX[name]
                m0 = b["m" + name]
                obs = [(x, y, f[i] - m0) for x, y, f in pts if f[i] is not None]
                a_def, w_def, shapes, _ = MODEL[name]
                basis = self._basis(name, link)
                fitted = len(obs) >= MIN_FIT
                if fitted:
                    a, w = _fit_amplitude(basis, [o[:2] for o in obs], [o[2] for o in obs], shapes, a_def, w_def)
                    resid = [(ox, oy, oe - a * basis(ox, oy, w)) for ox, oy, oe in obs]
                else:
                    a, w, resid = a_def, w_def, []
                sd = b["sd" + name]
                if resid:
                    sd = max(sd, math.sqrt(sum(r[2] ** 2 for r in resid) / len(resid)))
                mu = [m0 + a * basis(x, y, w) + (_residual_field(resid, x, y, inv) if resid else 0.0)
                      for x, y in self.cells]
                fits[name] = {"a": round(a, 3), "w": w, "sd": round(sd, 3), "n": len(obs)}
                models[name] = (mu, sd)
            if models:
                self.fit[link] = fits
                self.model[link] = models

    def _build_pattern(self, link, pts, fits, models):
        obs = [(x, y, min(U_MAX, math.exp(2 * f[1]) - 1)) for x, y, f in pts if f[1] is not None]
        a_def, w_def, shapes, _ = MODEL["P"]
        basis = self._basis("P", link)
        if len(obs) >= MIN_FIT:
            a, w = _fit_amplitude(basis, [o[:2] for o in obs], [o[2] for o in obs], shapes, a_def, w_def)
        else:
            a, w = a_def, w_def
        fits["P"] = {"a": round(a, 3), "w": w, "n": len(obs)}
        models["P"] = ([a * basis(x, y, w) for x, y in self.cells], 0.0)

    def usable(self, feats: Dict[str, tuple]) -> List[str]:
        return [link for link in self.model if link in feats]

    def loglik(self, feats: Dict[str, tuple]) -> Optional[List[float]]:
        links = self.usable(feats)
        if len(links) < 2 or not self.cells:
            return None
        out = [0.0] * len(self.cells)
        pattern = []
        for link in links:
            f = feats[link]
            for name, (mu, sd) in self.model[link].items():
                if name == "P":
                    if f[1] is not None:
                        u, prec = _pattern(f)
                        pattern.append((u, mu, prec))
                    continue
                v = f[FEAT_IDX[name]]
                if v is None:
                    continue
                inv_sd = 1.0 / sd
                for i, m in enumerate(mu):
                    out[i] -= 0.5 * _huber(abs(v - m) * inv_sd)
        if len(pattern) >= 2:
            s_uu = sum(prec * u * u for u, _, prec in pattern)
            levels = [(al, (math.log(al) / ALPHA_SD) ** 2) for al in ALPHAS]
            for i in range(len(out)):
                s_um = s_mm = 0.0
                for u, mu, prec in pattern:
                    m = mu[i]
                    s_um += prec * u * m
                    s_mm += prec * m * m
                out[i] -= 0.5 * min(s_uu - 2 * al * s_um + al * al * s_mm + pen for al, pen in levels)
        return out

    def empty_loglik(self, feats: Dict[str, tuple]) -> Optional[float]:
        """Log-likelihood of an empty room (every feature at its empty mean), comparable with loglik's cells."""
        links = self.usable(feats)
        if len(links) < 2:
            return None
        total = 0.0
        for link in links:
            f, b = feats[link], self.base[link]
            for name, (_, sd) in self.model[link].items():
                if name == "P":
                    if f[1] is not None:
                        u, prec = _pattern(f)
                        total -= 0.5 * u * u * prec
                    continue
                v = f[FEAT_IDX[name]]
                if v is not None:
                    total -= 0.5 * _huber(abs(v - b["m" + name]) / sd)
        return total

    predict = rmap.RadioMap.predict
    summary = rmap.RadioMap.summary

    def info(self) -> dict:
        return {"links": len(self.model), "walk_windows": len(self.walk), "fit": self.fit}


class Person:
    """One device-free track. Presence is a leaky evidence integrator with hysteresis, so it doesn't flicker."""

    def __init__(self):
        self.belief = None
        self.t = None
        self.raw = None
        self.evidence = 0.0
        self.present = False
        self.moving = -1

    def update(self, m: DFMap, feats: Dict[str, tuple], t: float) -> Optional[dict]:
        ll = m.loglik(feats)
        if ll is None:
            return None
        empty = m.empty_loglik(feats)
        self.moving = moving(feats)
        strength = 1.0
        if empty is not None:
            margin = max(ll) - empty
            self.evidence = 0.75 * self.evidence + 0.25 * margin
            self.present = self.evidence > PRESENT_MARGIN * (0.6 if self.present else 1.0)
            if self.belief is not None:
                strength = min(1.0, max(GATE_MIN, margin / GATE))
        dt = (t - self.t) if self.t else 0.25
        speed = {1: SPEED_MOVING, 0: SPEED_STILL}.get(self.moving, SPEED_UNKNOWN)
        prior = None
        if self.belief is not None and len(self.belief) == len(ll):
            prior = m.predict(self.belief, dt, speed)
        self.belief = rmap.posterior(prior, ll, TEMPER * strength, m.prior)
        self.t = t
        self.raw = m.summary(self.belief)
        return self.raw
