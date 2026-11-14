"""Room planning from the layout polygon and scanned furniture (metres).

Where to put the boards, and the guided calibration route and check points the phone page walks you through.

Board placement score: for each standable cell, the expected distance to the cells it could be confused with, under
path-loss RSSI with the per-phone offset removed (the mean across boards is unknown, as in radiomap) and PLAN_SIGMA_DB
of noise. That captures both precision and the 3-board mirror ambiguity (collinear boards score badly). Multipath is
ignored, so it compares placements; it does not promise real accuracy.
"""
import math
from itertools import combinations
from typing import List, Optional

import radiomap as rm

PLAN_ETA = 2.5
PLAN_SIGMA_DB = 4.0
PLAN_MAX_CELLS = 160
WALL_INSET_M = 0.2
WALL_STEP_M = 0.5
SHORTLIST = 40
# Furniture labels are the ones a room scan import produces.
BOARD_SURFACES = {"Desk", "Table", "Bookshelf", "Dresser", "TV", "Cabinet"}
NOT_STANDABLE = {"Bed", "Sofa", "Desk", "Table", "Bookshelf", "Dresser", "TV", "Cabinet", "Fridge", "Oven", "Stove"}
# Tracking prior (radiomap `blocked` weights). Unlike NOT_STANDABLE this is about where a phone can BE: people sit on
# sofas and beds, so those are only mildly unlikely. Chairs are left out: scans tuck them under tables.
FURNITURE_WEIGHT = {"Bookshelf": 0.2, "Dresser": 0.3, "TV": 0.3, "Desk": 0.5, "Table": 0.5, "Cabinet": 0.3,
                    "Fridge": 0.2, "Oven": 0.3, "Stove": 0.3, "Sofa": 0.6, "Bed": 0.6}
# Fewer, farther-apart dots keep calibration quick. walkcal interpolates between taps, so dots are needed only at turns
# and every DOT_SPACING_M along a straight run (pace drifts on long legs).
LANE_M = 1.5
DOT_SPACING_M = 4.0
LANE_SAMPLE_M = 0.15
ROUTE_INSET_M = 0.35
FURNITURE_MARGIN_M = 0.25
N_CHECKS = 3
MIN_DOT_GAP_M = 0.5


def _centroid(poly):
    n = len(poly)
    return sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n


def _area(poly) -> float:
    return abs(sum(poly[i][0] * poly[i - 1][1] - poly[i - 1][0] * poly[i][1] for i in range(len(poly)))) / 2


def _seg_dist(x, y, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = ((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy + 1e-12)
    t = max(0.0, min(1.0, t))
    return math.hypot(x - a[0] - t * dx, y - a[1] - t * dy)


def _edge_dist(x, y, poly):
    return min(_seg_dist(x, y, poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))


def _poly_dist(x, y, poly):
    return 0.0 if rm.point_in_poly(x, y, poly) else _edge_dist(x, y, poly)


def blocked(objects) -> list:
    return [(o["footprint"], FURNITURE_WEIGHT[o["label"]]) for o in objects
            if o.get("label") in FURNITURE_WEIGHT and len(o.get("footprint", [])) >= 3]


def _standable(x, y, polygon, objects, margin=0.0):
    if not rm.point_in_poly(x, y, polygon):
        return False
    return all(_poly_dist(x, y, o["footprint"]) > margin for o in objects if o.get("label") in NOT_STANDABLE)


def _cells(polygon, objects, max_cells=PLAN_MAX_CELLS):
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    step = max(0.4, math.sqrt(_area(polygon) / max_cells))
    cells = []
    y = min(ys) + step / 2
    while y < max(ys):
        x = min(xs) + step / 2
        while x < max(xs):
            if _standable(x, y, polygon, objects):
                cells.append((x, y))
            x += step
        y += step
    return cells


def _wall_spots(polygon, taken: List[dict]):
    for i, a in enumerate(polygon):
        b = polygon[(i + 1) % len(polygon)]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        lo = WALL_INSET_M / max(length, 1e-9)
        hi = 1 - lo
        if hi <= lo:
            continue
        nx, ny = -(b[1] - a[1]) / length, (b[0] - a[0]) / length
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        if not rm.point_in_poly(mx + nx * WALL_INSET_M, my + ny * WALL_INSET_M, polygon):
            nx, ny = -nx, -ny
        count = max(1, int((hi - lo) * length / WALL_STEP_M))
        for j in range(count + 1):
            f = lo + (hi - lo) * j / count
            x = a[0] + f * (b[0] - a[0]) + nx * WALL_INSET_M
            y = a[1] + f * (b[1] - a[1]) + ny * WALL_INSET_M
            if rm.point_in_poly(x, y, polygon) and all(math.hypot(x - c["x"], y - c["y"]) > 0.3 for c in taken):
                taken.append({"x": x, "y": y, "near": "on the wall"})


def candidates(polygon, objects) -> List[dict]:
    spots = []
    for obj in objects:
        if obj.get("label") in BOARD_SURFACES and len(obj.get("footprint", [])) >= 3:
            x, y = _centroid(obj["footprint"])
            spots.append({"x": x, "y": y, "near": "on the " + obj["label"].lower()})
    _wall_spots(polygon, spots)
    return spots


def _levels(board, cells):
    return [-10 * PLAN_ETA * math.log10(max(0.5, math.hypot(x - board["x"], y - board["y"]))) for x, y in cells]


def _errors(level_sets, cells):
    boards, count = len(level_sets), len(cells)
    centred = []
    for i in range(count):
        col = [levels[i] for levels in level_sets]
        mean = sum(col) / boards
        centred.append([v - mean for v in col])
    k = 1 / (4 * PLAN_SIGMA_DB ** 2)
    out = []
    for i in range(count):
        vi, (xi, yi) = centred[i], cells[i]
        w_sum = d_sum = 0.0
        for j in range(count):
            vj = centred[j]
            dist2 = 0.0
            for b in range(boards):
                diff = vi[b] - vj[b]
                dist2 += diff * diff
            w = math.exp(-dist2 * k)
            w_sum += w
            d_sum += w * math.hypot(xi - cells[j][0], yi - cells[j][1])
        out.append(d_sum / w_sum)
    return out


def score(positions: List[dict], polygon, objects) -> Optional[dict]:
    cells = _cells(polygon, objects)
    if not cells or len(positions) < 3:
        return None
    return rm.err_summary(_errors([_levels(p, cells) for p in positions], cells))


def suggest_boards(polygon, objects, n: int = 3) -> Optional[dict]:
    cells = _cells(polygon, objects)
    cands = candidates(polygon, objects)
    if not cells or len(cands) < n:
        return None
    levels = [_levels(c, cells) for c in cands]

    def spread(idx):
        return min(math.hypot(cands[a]["x"] - cands[b]["x"], cands[a]["y"] - cands[b]["y"])
                   for a, b in combinations(idx, 2))

    def cost(idx):
        return sum(_errors([levels[i] for i in idx], cells)) / len(cells)

    # Shortlist the most spread-out sets cheaply, score those properly, then nudge the winner one board at a time.
    shortlist = sorted(combinations(range(len(cands)), n), key=spread, reverse=True)[:SHORTLIST]
    best = min(shortlist, key=cost)
    best_cost = cost(best)
    improved = True
    while improved:
        improved = False
        for slot in range(n):
            here = cands[best[slot]]
            for j, cand in enumerate(cands):
                if j in best or math.hypot(cand["x"] - here["x"], cand["y"] - here["y"]) > 1.5:
                    continue
                trial = best[:slot] + (j,) + best[slot + 1:]
                trial_cost = cost(trial)
                if trial_cost < best_cost - 1e-6:
                    best, best_cost, improved = trial, trial_cost, True
    spots = [dict(cands[i]) for i in best]
    # Every RX must hear the AP: the AP takes the spot whose longest link is shortest.
    ap = min(range(n), key=lambda i: max(math.hypot(spots[i]["x"] - s["x"], spots[i]["y"] - s["y"]) for s in spots))
    for i, spot in enumerate(spots):
        spot["role"] = "AP" if i == ap else "RX"
        spot["x"], spot["y"] = round(spot["x"], 2), round(spot["y"], 2)
    spots.sort(key=lambda s: s["role"] != "AP")
    return {"spots": spots, **rm.err_summary(_errors([levels[i] for i in best], cells))}


def assign_boards(spots: List[dict], boards: List[dict]) -> List[dict]:
    """Map planned spots to config/boards.json entries: AP spot to the AP board, RX spots to the rest by label."""
    receivers = iter(sorted((b for b in boards if b.get("role") != "AP"), key=lambda b: b.get("label", "")))
    ap = next((b for b in boards if b.get("role") == "AP"), None)
    out = []
    for spot in spots:
        board = ap if spot["role"] == "AP" else next(receivers, None)
        out.append(dict(spot, id=board["base_mac"] if board else None, label=board["label"] if board else "?"))
    return out


def _lane_runs(ok, u0, u1, v):
    samples = max(2, int((u1 - u0) / LANE_SAMPLE_M))
    runs, cur = [], []
    for k in range(samples):
        u = u0 + (k + 0.5) * (u1 - u0) / samples
        if ok(u, v):
            cur.append(u)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def _route_along(polygon, objects, flip):
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    u0, u1, v0, v1 = min(xs), max(xs), min(ys), max(ys)
    if flip:
        u0, u1, v0, v1 = v0, v1, u0, u1
    width = (v1 - v0) - 2 * ROUTE_INSET_M
    if width <= 0 or (u1 - u0) <= 2 * ROUTE_INSET_M:
        return []
    lanes = max(2, math.ceil(width / LANE_M - 1e-9) + 1)

    def to_xy(u, v):
        return (v, u) if flip else (u, v)

    def ok(u, v):
        x, y = to_xy(u, v)
        return (_standable(x, y, polygon, objects, FURNITURE_MARGIN_M)
                and _edge_dist(x, y, polygon) >= ROUTE_INSET_M - 1e-6)

    route = []
    for lane in range(lanes):
        v = v0 + ROUTE_INSET_M + width * lane / (lanes - 1)
        dots = []
        for run in _lane_runs(ok, u0, u1, v):
            a, b = run[0], run[-1]
            if b - a < 0.3:
                dots.append((a + b) / 2)
                continue
            parts = max(1, math.ceil((b - a) / DOT_SPACING_M - 1e-9))
            dots.extend(a + (b - a) * k / parts for k in range(parts + 1))
        if lane % 2:
            dots.reverse()
        route.extend([round(c, 2) for c in to_xy(u, v)] for u in dots)
    return route


def _clear(a, b, polygon, objects):
    steps = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 0.1))
    return all(_standable(a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps, polygon, objects)
               for k in range(steps + 1))


def walk_route(polygon, objects) -> List[List[float]]:
    """Serpentine calibration route as [x, y, detour].

    Lanes LANE_M apart, each split into runs of free floor (clear of walls and furniture) with dots at both ends and
    at most DOT_SPACING_M apart. Both directions are tried and the one covering more floor wins. detour=1 when the
    straight line from the previous dot crosses furniture; the phone page sends it with the mark and walkcal drops
    that leg.
    """
    def coverage(route):
        return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(route, route[1:])) + 0.5 * len(route)

    best = max((_route_along(polygon, objects, flip) for flip in (False, True)), key=coverage)
    dots = []
    for p in best:
        if not dots or math.hypot(p[0] - dots[-1][0], p[1] - dots[-1][1]) >= MIN_DOT_GAP_M:
            dots.append(p)
    return [[x, y, 0 if i == 0 or _clear(dots[i - 1], (x, y), polygon, objects) else 1]
            for i, (x, y) in enumerate(dots)]


def reverse_route(route: List[List[float]]) -> List[List[float]]:
    """The same dots walked backwards; detour flags (which describe the arriving leg) shift by one.

    Used for alternate walks (App.route): the walker's body shadows boards behind them, so a map built only from
    walks facing one way is right only for people facing that way.
    """
    n = len(route)
    return [[route[n - 1 - j][0], route[n - 1 - j][1], 0 if j == 0 else route[n - j][2]] for j in range(n)]


def check_points(polygon, objects, route=None) -> List[List[float]]:
    """Spread-out standing spots away from the route, so checks measure places the map was not trained on."""
    route = route if route is not None else walk_route(polygon, objects)
    pool = [c for c in _cells(polygon, objects, 400)
            if _standable(c[0], c[1], polygon, objects, FURNITURE_MARGIN_M)
            and _edge_dist(c[0], c[1], polygon) >= ROUTE_INSET_M
            and all(math.hypot(c[0] - r[0], c[1] - r[1]) > 0.3 for r in route)]
    if not pool:
        return []
    cx, cy = _centroid(polygon)
    picks = [min(pool, key=lambda c: math.hypot(c[0] - cx, c[1] - cy))]
    while len(picks) < min(N_CHECKS, len(pool)):
        picks.append(max(pool, key=lambda c: min(math.hypot(c[0] - p[0], c[1] - p[1]) for p in picks)))
    return [[round(x, 2), round(y, 2)] for x, y in picks]
