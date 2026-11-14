"""Radio map plus the grid-Bayes building blocks shared by every estimator.

For receiver n the expected RSSI at x is  M_n(x) = T_n - 10 * eta_n * log10(d) + f_n(x),  where f_n is a Gaussian
process residual fitted to calibration spots (zero and uncertain far from them). phone_filter.py owns the per-phone
time filter built on this. Units are metres. Tuning tools overwrite the module constants below at runtime, so functions
must look them up at call time and never bind them as default arguments.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

FT = 0.3048
CELL_M = 0.5 * FT
ETA_PRIOR, ETA_SD = 2.2, 0.7
T_PRIOR_SD = 8.0
D0 = 0.3
# DELTA_SD: prior sd (dB) of an unknown phone's offset (model, pocket). DELTA_FIT_SD: one calibration group versus the
# others (same phone, so small). Live readings are compared only after solving that offset per cell.
DELTA_SD = 12.0
DELTA_FIT_SD = 4.0
# GP residual: GP_SF is the prior sd (dB) of what log-distance misses (walls, furniture), GP_LEN its length scale.
# Spot noise: a standing spot sits inside one multipath fade (SN_STILL); a walked window averages several (SN_MOVE).
GP_SF = 3.0
GP_LEN = 1.2
SN_STILL = 3.0
SN_MOVE = 2.0
LOCAL_VAR = 1.0
SIGMA_UNCAL = 6.0
# SIGMA_OBS: fading sd (dB) of one live reading on top of the map's own uncertainty. FRAME_VAR / frames adds the
# averaging noise of a window with few frames.
SIGMA_OBS = 4.0
FRAME_VAR = 9.0
ROBUST_K = 2.5
MIX = 0.02
MIN_FRAMES = 3
# "shadow_guard": readings weaker than the offset-corrected prediction switch from quadratic to linear cost at SHADOW_K
# sd (instead of ROBUST_K), so one blocked board can't drag the fix away. Stronger-than-predicted readings keep
# ROBUST_K because bodies only attenuate. The offset is then re-solved once with those low readings down-weighted.
SHADOW_K = 1.5
SHADOW_W = 0.35
# "holder_body": the person holding the phone shadows boards behind them. Expected loss for a board at angle phi from
# the facing direction is HOLDER_DB * max(0, -cos(phi)). Spots record their facing so the map is fitted body-free;
# live, facing comes from the compass or, without it, the direction-averaged loss HOLDER_DB / pi is assumed.
HOLDER_DB = 3.0

Live = Dict[str, Tuple[float, int]]
Blocked = List[Tuple[List[List[float]], float]]

_grids: Dict[tuple, "Grid"] = {}


def point_in_poly(x: float, y: float, poly: List[List[float]]) -> bool:
    inside = False
    px, py = poly[-1]
    for qx, qy in poly:
        if (qy > y) != (py > y) and x < (px - qx) * (y - qy) / (py - qy + 1e-12) + qx:
            inside = not inside
        px, py = qx, qy
    return inside


def solve(A, b) -> List[float]:
    A = np.asarray(A, float)
    b = np.asarray(b, float)
    try:
        x = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        x = np.linalg.lstsq(A, b, rcond=None)[0]
    return [float(v) for v in x]


def link_gains(links: Dict[Tuple[str, str], float], nodes: List[str]) -> Dict[str, float]:
    """Per-board receive gain from reciprocal board-to-board links.

    With equal transmit power, m(i->j) - m(j->i) ~= g_j - g_i. Solved in least squares with sum(g) = 0; this is what
    makes the uncalibrated map roughly right.
    """
    known = set(nodes)
    diffs = [(a, b, rssi - links[(b, a)]) for (a, b), rssi in links.items()
             if a < b and (b, a) in links and a in known and b in known]
    gains = {n: 0.0 for n in nodes}
    if not diffs:
        return gains
    pos = {n: i for i, n in enumerate(nodes)}
    size = len(nodes)
    A = np.ones((size, size)) + 1e-3 * np.eye(size)
    rhs = np.zeros(size)
    for a, b, diff in diffs:
        i, j = pos[a], pos[b]
        A[i, i] += 1
        A[j, j] += 1
        A[i, j] -= 1
        A[j, i] -= 1
        rhs[j] += diff
        rhs[i] -= diff
    sol = solve(A, rhs)
    return {n: sol[pos[n]] for n in nodes}


class Grid:
    """The room as a grid of CELL_M cells inside its polygon.

    Beliefs are flat arrays over `cells` (row-major, inside cells only); `flat` maps each into the dense ny*nx array
    the motion operators use. Shared by RadioMap and sensing.DFMap so their log-likelihoods add. Cached: never mutate.
    """

    def __init__(self, poly):
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        self.x0, self.y0 = min(xs), min(ys)
        self.nx = max(1, int(math.ceil((max(xs) - self.x0) / CELL_M)))
        self.ny = max(1, int(math.ceil((max(ys) - self.y0) / CELL_M)))
        centres: List[Tuple[float, float]] = []
        flat: List[int] = []
        for iy in range(self.ny):
            cy = self.y0 + (iy + 0.5) * CELL_M
            for ix in range(self.nx):
                cx = self.x0 + (ix + 0.5) * CELL_M
                if point_in_poly(cx, cy, poly):
                    centres.append((cx, cy))
                    flat.append(iy * self.nx + ix)
        self.cells = tuple(centres)
        self.flat = np.asarray(flat, dtype=np.intp)
        self.cx = np.asarray([c[0] for c in centres], float)
        self.cy = np.asarray([c[1] for c in centres], float)
        mask = np.zeros(self.ny * self.nx)
        mask[self.flat] = 1.0
        self.mask = mask.reshape(self.ny, self.nx)
        self._kernels: Dict[int, tuple] = {}
        self._masks: Dict[Tuple[int, int], np.ndarray] = {}

    def dense(self, v) -> np.ndarray:
        out = np.zeros(self.ny * self.nx)
        out[self.flat] = v
        return out.reshape(self.ny, self.nx)

    def _sparse(self, d: np.ndarray) -> np.ndarray:
        return d.reshape(-1)[self.flat]

    def _kernel(self, sd_m: float):
        key = int(round(sd_m / CELL_M * 16))
        cached = self._kernels.get(key)
        if cached is not None:
            return cached
        sd_cells = max(key, 1) / 16.0

        def gauss_band(n):
            idx = np.arange(n)
            k = np.exp(-0.5 * ((idx[:, None] - idx[None, :]) / sd_cells) ** 2)
            return k / k.sum(axis=0, keepdims=True).max()

        ky, kx = gauss_band(self.ny), gauss_band(self.nx)
        norm = ky @ self.mask @ kx.T
        norm[norm <= 0] = 1.0
        if len(self._kernels) > 64:
            self._kernels.clear()
        self._kernels[key] = (ky, kx, norm)
        return ky, kx, norm

    def diffuse(self, b: np.ndarray, sd_m: float) -> np.ndarray:
        """Gaussian random-walk step of sd_m metres.

        Walls reflect: each source cell's kernel is renormalised over existing cells, so mass is conserved and
        diffuse_adj is the exact transpose (phone_filter.Track.smoothed depends on that).
        """
        if sd_m < CELL_M / 16:
            return b
        ky, kx, norm = self._kernel(sd_m)
        return self._sparse(ky @ (self.dense(b) / norm) @ kx.T)

    def diffuse_adj(self, b: np.ndarray, sd_m: float) -> np.ndarray:
        if sd_m < CELL_M / 16:
            return b
        ky, kx, norm = self._kernel(sd_m)
        return self._sparse((ky.T @ self.dense(b) @ kx) / norm)

    @staticmethod
    def _bilinear(dx: float, dy: float):
        fx, fy = dx / CELL_M, dy / CELL_M
        ix, iy = math.floor(fx), math.floor(fy)
        ax, ay = fx - ix, fy - iy
        return ((ix, iy, (1 - ax) * (1 - ay)), (ix + 1, iy, ax * (1 - ay)),
                (ix, iy + 1, (1 - ax) * ay), (ix + 1, iy + 1, ax * ay))

    @staticmethod
    def _translate(d: np.ndarray, ix: int, iy: int) -> np.ndarray:
        out = np.zeros_like(d)
        ny, nx = d.shape
        if abs(ix) >= nx or abs(iy) >= ny:
            return out
        dst = (slice(max(0, iy), ny + min(0, iy)), slice(max(0, ix), nx + min(0, ix)))
        src = (slice(max(0, -iy), ny - max(0, iy)), slice(max(0, -ix), nx - max(0, ix)))
        out[dst] = d[src]
        return out

    def _mask_from(self, ix: int, iy: int) -> np.ndarray:
        """Which cells have an existing cell (ix, iy) away; shift and shift_adj share it. Read-only."""
        key = (ix, iy)
        cached = self._masks.get(key)
        if cached is None:
            if len(self._masks) > 256:
                self._masks.clear()
            cached = self._masks[key] = self._translate(self.mask, -ix, -iy)
        return cached

    def shift(self, b: np.ndarray, dx: float, dy: float) -> np.ndarray:
        """Move belief by (dx, dy) metres, split bilinearly over 4 cells. Mass that would leave the room stays put.

        Used for step + compass dead reckoning; shift_adj is its exact transpose.
        """
        d = self.dense(b)
        moved = np.zeros_like(d)
        landed = np.zeros_like(d)
        for ix, iy, w in self._bilinear(dx, dy):
            if w > 0:
                moved += w * self._translate(d, ix, iy)
                landed += w * self._mask_from(ix, iy)
        return self._sparse(moved * self.mask + d * (1.0 - landed))

    def shift_adj(self, b: np.ndarray, dx: float, dy: float) -> np.ndarray:
        d = self.dense(b)
        moved = np.zeros_like(d)
        landed = np.zeros_like(d)
        for ix, iy, w in self._bilinear(dx, dy):
            if w > 0:
                moved += w * self._translate(d, -ix, -iy)
                landed += w * self._mask_from(ix, iy)
        return self._sparse(moved + d * (1.0 - landed))


def grid_of(poly: List[List[float]]) -> Optional[Grid]:
    if len(poly) < 3:
        return None
    key = tuple((float(p[0]), float(p[1])) for p in poly)
    grid = _grids.get(key)
    if grid is None:
        if len(_grids) > 16:
            _grids.clear()
        grid = _grids[key] = Grid(key)
    return grid


def cell_prior(cells, blocked: Optional[Blocked]) -> Optional[np.ndarray]:
    """Per-cell weight from furniture footprints (lowest weight covering the cell), or None if nothing applies."""
    if not blocked:
        return None
    weights = [min((w for poly, w in blocked if point_in_poly(x, y, poly)), default=1.0) for x, y in cells]
    if min(weights, default=1.0) >= 1.0:
        return None
    return np.asarray(weights, float)


def posterior(prior, ll, k: float, cell_w) -> np.ndarray:
    """Tempered Bayes update: prior * exp(k * (ll - max)) * cell weight, normalised. Shared with sensing.DFMap."""
    ll = np.asarray(ll, float)
    post = np.exp(k * (ll - ll.max()))
    if prior is not None:
        post = post * np.asarray(prior, float)
    if cell_w is not None:
        post = post * cell_w
    total = post.sum()
    if total > 0:
        return post / total
    return np.full(len(ll), 1.0 / max(1, len(ll)))


def per_node(samples, min_n: int = 1) -> Dict[str, dict]:
    """Frame-weighted mean and pooled sd per board from samples [t, board, frames, rssi, sd]; small boards dropped."""
    grouped: Dict[str, list] = {}
    for _, board, frames, rssi, sd in samples:
        grouped.setdefault(board, []).append((frames, rssi, sd))
    out = {}
    for board, rows in grouped.items():
        total = sum(r[0] for r in rows)
        if total < max(1, min_n):
            continue
        mean = sum(n * r for n, r, _ in rows) / total
        var = sum(n * (sd * sd + (r - mean) ** 2) for n, r, sd in rows) / total
        out[board] = {"mean": round(mean, 2), "sd": round(math.sqrt(var), 2), "n": total}
    return out


def _sq_exp(ax, ay, bx, by) -> np.ndarray:
    d2 = (ax[:, None] - bx[None, :]) ** 2 + (ay[:, None] - by[None, :]) ** 2
    return GP_SF ** 2 * np.exp(-d2 / (2 * GP_LEN ** 2))


class RadioMap:
    """Expected RSSI per board per grid cell, and the likelihood of live readings against it.

    blocked: [(footprint polygon, weight 0..1)] from the room scan. Cells inside get their prior scaled by the weight
    on every update, so low weights compound into a soft wall (nobody stands inside a bookshelf).
    """

    def __init__(self, layout: dict, spots: List[dict], links: Optional[Dict[Tuple[str, str], float]] = None,
                 blocked: Optional[Blocked] = None, holder: bool = False):
        self.holder = holder
        self.poly = layout.get("polygon") or []
        self.rx = {name: (float(r["x"]), float(r["y"])) for name, r in layout.get("receivers", {}).items()}
        self.nodes = sorted(self.rx)
        self.node_i = {n: i for i, n in enumerate(self.nodes)}
        self.links = links or {}
        self.gains = link_gains(self.links, self.nodes)
        self.grid = grid_of(self.poly)
        if self.grid:
            self.cells, self.cx, self.cy = self.grid.cells, self.grid.cx, self.grid.cy
        else:
            self.cells, self.cx, self.cy = (), np.zeros(0), np.zeros(0)
        self.blocked = blocked or []
        self.prior = cell_prior(self.cells, self.blocked)
        self.spots = [s for s in spots if any(n in self.rx and st.get("n", 0) >= MIN_FRAMES
                                              for n, st in s.get("per_node", {}).items())]
        self.fit = self._fit()
        self._build()

    def _logd(self, n: str, x, y):
        bx, by = self.rx[n]
        dist = np.hypot(np.asarray(x, float) - bx, np.asarray(y, float) - by)
        return np.log10(np.maximum(D0, dist))

    def _holder_loss(self, n: str, x: float, y: float, facing: Optional[float]) -> float:
        if not self.holder:
            return 0.0
        if facing is None:
            return HOLDER_DB / math.pi
        bx, by = self.rx[n]
        return HOLDER_DB * max(0.0, -math.cos(math.atan2(by - y, bx - x) - facing))

    def _observations(self):
        out = []
        for k, spot in enumerate(self.spots):
            for n, st in spot["per_node"].items():
                if n in self.rx and st.get("n", 0) >= MIN_FRAMES:
                    out.append((k, n, st["mean"] + self._holder_loss(n, spot["x"], spot["y"], spot.get("facing"))))
        return out

    @staticmethod
    def _noise(spot: dict) -> float:
        # walkcal sets still=False on windows averaged while walking; a plain standing spot has no "still" key.
        return SN_MOVE if spot.get("still") is False else SN_STILL

    def _fit(self) -> dict:
        """Ridge least squares over [T_n..., eta_n..., delta_g...].

        delta_g is the phone offset of each spot group (one walk; default the spot alone), solved jointly so pocket or
        orientation offsets don't bend T_n or eta_n. eta is per board since each sees the room through other walls.
        """
        nodes, spots = self.nodes, self.spots
        if not nodes:
            return {"T": {}, "eta": ETA_PRIOR, "eta_n": {}, "delta": [], "rms": None, "n_obs": 0, "base": 0.0}
        obs = self._observations()
        base = -40.0
        if obs:
            base = sum(r + 10 * ETA_PRIOR * float(self._logd(n, spots[k]["x"], spots[k]["y"])) - self.gains[n]
                       for k, n, r in obs) / len(obs)
        t_start = {n: base + self.gains[n] for n in nodes}
        if not obs:
            return {"T": t_start, "eta": ETA_PRIOR, "eta_n": {n: ETA_PRIOR for n in nodes}, "delta": [],
                    "rms": None, "n_obs": 0, "base": base}

        group_of: Dict[str, int] = {}
        spot_group = [group_of.setdefault(str(s.get("group", f"spot{k}")), len(group_of)) for k, s in enumerate(spots)]
        n_nodes, n_groups = len(nodes), len(group_of)
        n_par = 2 * n_nodes + n_groups
        rows = len(obs) + n_par
        X = np.zeros((rows, n_par))
        y = np.zeros(rows)
        w = np.zeros(rows)
        for i, (k, n, r) in enumerate(obs):
            j = self.node_i[n]
            X[i, j] = 1.0
            X[i, n_nodes + j] = -10 * float(self._logd(n, spots[k]["x"], spots[k]["y"]))
            X[i, 2 * n_nodes + spot_group[k]] = 1.0
            y[i] = r
            w[i] = 1.0 / self._noise(spots[k]) ** 2
        row = len(obs)
        for n in nodes:
            j = self.node_i[n]
            X[row, j], y[row], w[row] = 1.0, t_start[n], 1 / T_PRIOR_SD ** 2
            X[row + 1, n_nodes + j], y[row + 1], w[row + 1] = 1.0, ETA_PRIOR, 1 / ETA_SD ** 2
            row += 2
        # A lone group's offset is indistinguishable from T_n, so pin it to zero.
        group_sd = DELTA_FIT_SD if n_groups > 1 else 1e-3
        for g in range(n_groups):
            X[row, 2 * n_nodes + g], y[row], w[row] = 1.0, 0.0, 1 / group_sd ** 2
            row += 1
        Xw = X * w[:, None]
        sol = np.asarray(solve(X.T @ Xw, Xw.T @ y))
        resid = y[:len(obs)] - X[:len(obs)] @ sol
        T = {n: float(sol[self.node_i[n]]) for n in nodes}
        eta_n = {n: float(sol[n_nodes + self.node_i[n]]) for n in nodes}
        return {"T": T, "eta": sum(eta_n.values()) / n_nodes, "eta_n": eta_n,
                "delta": [float(sol[2 * n_nodes + g]) for g in spot_group],
                "rms": float(np.sqrt(np.mean(resid ** 2))), "n_obs": len(obs), "base": base}

    def _build(self):
        T, eta_n, delta = self.fit["T"], self.fit["eta_n"], self.fit["delta"]
        n_cells = len(self.cells)
        self.MU = np.zeros((len(self.nodes), n_cells))
        self.VAR = np.full((len(self.nodes), n_cells), (GP_SF if self.spots else SIGMA_UNCAL) ** 2)
        self.ANG = np.zeros((len(self.nodes), n_cells))
        obs = self._observations()
        for i, n in enumerate(self.nodes):
            bx, by = self.rx[n]
            self.ANG[i] = np.arctan2(by - self.cy, bx - self.cx)
            self.MU[i] = T[n] - 10 * eta_n.get(n, ETA_PRIOR) * self._logd(n, self.cx, self.cy)
            mine = [(k, r) for k, board, r in obs if board == n]
            if not mine or not n_cells:
                continue
            used = [self.spots[k] for k, _ in mine]
            sx = np.asarray([s["x"] for s in used], float)
            sy = np.asarray([s["y"] for s in used], float)
            trend = T[n] - 10 * eta_n[n] * self._logd(n, sx, sy)
            resid = np.asarray([r - delta[k] for k, r in mine]) - trend
            K_inv = np.linalg.inv(_sq_exp(sx, sy, sx, sy) + np.diag([self._noise(s) ** 2 for s in used]))
            k_star = _sq_exp(self.cx, self.cy, sx, sy)
            alpha = K_inv @ resid
            self.MU[i] += k_star @ alpha
            var = np.maximum(0.05, GP_SF ** 2 - np.einsum("ci,ij,cj->c", k_star, K_inv, k_star))
            # GP variance knows how many spots are nearby, not whether they agree. Where neighbouring spots (or two
            # walks over the same floor) disagree beyond their noise, add the local excess of the exact leave-one-out
            # residuals alpha_i / Kinv_ii.
            if LOCAL_VAR:
                diag = np.diag(K_inv)
                loo_excess = (alpha / diag) ** 2 - 1.0 / diag
                near = k_star / GP_SF ** 2
                var = var + LOCAL_VAR * np.maximum(0.0, (near @ loo_excess) / (near.sum(1) + 1.0))
            self.VAR[i] = var

    def usable(self, live: Live) -> List[Tuple[str, float, int]]:
        return [(n, r, k) for n, (r, k) in live.items() if n in self.rx and k >= MIN_FRAMES]

    def loglik_off(self, live: Live, shadow: bool = False, facing: Optional[float] = None, off: float = 0.0,
                   off_sd: Optional[float] = None):
        """Per-cell log-likelihood of one set of readings, and the per-cell MAP phone offset.

        The common offset d has prior N(off, off_sd^2): wide for an unknown phone, narrow once phone_filter.py learned
        it. Solving d per cell is why a uniformly quieter phone doesn't look far from everything. shadow applies the
        asymmetric cost at SHADOW_K; facing is the holder's map angle if known.
        """
        use = self.usable(live)
        if len(use) < 2 or not self.cells:
            return None
        rows = [self.node_i[n] for n, _, _ in use]
        rssi = np.asarray([r for _, r, _ in use])[:, None]
        frame_var = np.asarray([FRAME_VAR / k for _, _, k in use])[:, None]
        W = 1.0 / (self.VAR[rows] + frame_var + SIGMA_OBS ** 2)
        E = rssi - self.MU[rows] - off
        if self.holder:
            if facing is None:
                E = E + HOLDER_DB / math.pi
            else:
                E = E + HOLDER_DB * np.maximum(0.0, -np.cos(self.ANG[rows] - facing))
        prec = 1.0 / (DELTA_SD if off_sd is None else max(off_sd, 1e-3)) ** 2
        d = (W * E).sum(0) / (W.sum(0) + prec)
        if shadow:
            weak = (E < d) & ((E - d) ** 2 * W > SHADOW_K ** 2)
            W_low = np.where(weak, W * SHADOW_W, W)
            d = (W_low * E).sum(0) / (W_low.sum(0) + prec)
        z2 = (E - d) ** 2 * W
        z = np.sqrt(z2)
        k = np.where(E < d, SHADOW_K, ROBUST_K) if shadow else ROBUST_K
        huber = np.where(z < k, z2, k * (2 * z - k))
        cost = huber.sum(0) + d * d * prec
        return -0.5 * cost, d + off

    def predict(self, belief, dt: float, speed: float) -> np.ndarray:
        """Random-walk motion spreading by speed * dt, plus a MIX floor. Used by sensing.Person."""
        b = self.grid.diffuse(np.asarray(belief, float), speed * min(max(dt, 0.05), 2.0))
        return (1.0 - MIX) * b + MIX / len(b)

    def summary(self, post) -> dict:
        post = np.asarray(post, float)
        mx = float(post @ self.cx)
        my = float(post @ self.cy)
        spread = float(post @ ((self.cx - mx) ** 2 + (self.cy - my) ** 2))
        return {"x": mx, "y": my, "unc": max(1.0 * FT, math.sqrt(max(spread, 0.0)))}

    def static_estimate(self, live: Live, facing: Optional[float] = None) -> Optional[dict]:
        fit = self.loglik_off(live, facing=facing)
        if fit is None:
            return None
        return self.summary(posterior(None, fit[0], 1.0, self.prior))

    def info(self) -> dict:
        fit = self.fit
        return {"eta": round(fit["eta"], 2),
                "rms_db": None if fit["rms"] is None else round(fit["rms"], 1),
                "offsets_db": {n: round(t - fit.get("base", 0), 1) for n, t in fit["T"].items()},
                "link_gains_db": {n: round(g, 1) for n, g in self.gains.items()},
                "spots": len(self.spots), "cells": len(self.cells)}


def window_live(samples: List[list], t0: float, t1: float) -> Live:
    sums: Dict[str, List[float]] = {}
    for s in samples:
        t, board, frames, rssi = s[0], s[1], s[2], s[3]
        if t0 <= t < t1:
            acc = sums.setdefault(board, [0.0, 0])
            acc[0] += rssi * frames
            acc[1] += frames
    return {board: (acc[0] / acc[1], int(acc[1])) for board, acc in sums.items() if acc[1]}


def window_errors(rm: RadioMap, samples: List[list], x: float, y: float, w: float = 2.0) -> List[float]:
    """Errors (m) of static estimates over consecutive w-second windows of capture samples against truth (x, y)."""
    end = max((s[0] for s in samples), default=0.0)
    errs, t = [], 0.0
    while t + w <= end + 1e-6:
        est = rm.static_estimate(window_live(samples, t, t + w))
        if est:
            errs.append(math.hypot(est["x"] - x, est["y"] - y))
        t += w
    return errs


def err_summary(errs: List[float]) -> Optional[dict]:
    if not errs:
        return None
    ordered = sorted(errs)
    n = len(ordered)
    return {"windows": n, "median_m": ordered[n // 2], "p80_m": ordered[int(0.8 * (n - 1))],
            "mean_m": sum(ordered) / n}


def evaluate(layout: dict, spots: List[dict], links=None, window_s: float = 2.0,
             blocked: Optional[Blocked] = None) -> dict:
    """Leave-one-block-out single-snapshot error (m), plus a no-calibration baseline.

    A spot's "block" (default: itself) is held out as a unit so neighbouring walk windows can't leak into each
    other's test; "eval_s" sets a spot's window. Slow, so the server runs it on a thread; "n_spots" tells callers
    whether a result is current. This is the floor the time filter starts from, not live accuracy.
    """
    if not spots:
        return {"loo": None, "model_only": None, "n_spots": 0}
    blocks: Dict[str, List[int]] = {}
    for k, spot in enumerate(spots):
        blocks.setdefault(str(spot.get("block", f"spot{k}")), []).append(k)

    def spot_errors(rm: RadioMap, spot: dict) -> List[float]:
        samples = spot.get("samples") or []
        if not samples:
            return []
        if "eval_s" in spot:
            est = rm.static_estimate(window_live(samples, 0.0, float(spot["eval_s"])), spot.get("facing"))
            return [math.hypot(est["x"] - spot["x"], est["y"] - spot["y"])] if est else []
        return window_errors(rm, samples, spot["x"], spot["y"], window_s)

    holder = any(s.get("facing") is not None for s in spots)
    bare = RadioMap(layout, [], links, blocked, holder)
    model_only = [e for s in spots for e in spot_errors(bare, s)]
    loo = None
    if len(blocks) >= 2:
        errs: List[float] = []
        for members in blocks.values():
            held = set(members)
            rm = RadioMap(layout, [s for k, s in enumerate(spots) if k not in held], links, blocked, holder)
            for k in members:
                errs.extend(spot_errors(rm, spots[k]))
        loo = err_summary(errs)
    return {"loo": loo, "model_only": err_summary(model_only), "n_spots": len(spots)}
