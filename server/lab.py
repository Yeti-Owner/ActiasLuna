"""Settings lab: replay recorded sessions with each setting flipped and score raw estimates against ground truth.

Ground truth comes from the current calibration set:
  - check / test captures and "I'm here" marks: the phone stood at a known spot for the capture's duration. A mark
    also trained the map, so it is scored on a map without its own spot ("block");
  - walks: positions reconstructed between taps, scored on a map built without that walk's own spots, so a walk is
    never graded on its own training data.
Runs in worker processes (pure functions, picklable inputs). Truth only counts while the session was recording.
"""
from __future__ import annotations

import math
from typing import Dict, List

import radiomap as rmap
import settings as cfg
import walkcal
from replay import feed, load_session
from tracker import Tracker

WARMUP_S = 15.0
SETTLE_S = 3.0
STILL_S_MIN = 4.0


def truths(calib: dict) -> List[dict]:
    out = []
    for test in calib.get("tests", []):
        if "t" in test and test.get("phone"):
            out.append({"kind": "still", "phone": test["phone"], "t0": test["t"],
                        "t1": test["t"] + test.get("duration", 8), "pts": [(test["t"], test["x"], test["y"])],
                        "block": test.get("block")})
    groups: Dict[str, dict] = {}
    # Raw walks carry motion-aware truth (walkcal.truth); files without them only have walk spots.
    for walk in calib.get("walks", []):
        pts = [(walk["t0"] + p[0], p[1], p[2]) for p in walkcal.truth(walk)]
        if pts:
            groups[walk["id"]] = {"kind": "walk", "group": walk["id"], "phone": walk.get("phone"), "pts": pts,
                                  "gap": 0.6}
    for spot in calib.get("spots", []):
        if spot.get("kind") != "walk" or "t" not in spot or "gap" in groups.get(spot["group"], {}):
            continue
        group = groups.setdefault(spot["group"], {"kind": "walk", "group": spot["group"], "phone": spot.get("phone"),
                                                  "pts": []})
        group["pts"].append((spot["t"] + spot.get("duration", 1.5) / 2, spot["x"], spot["y"]))
    for group in groups.values():
        group["pts"].sort()
        group["t0"], group["t1"] = group["pts"][0][0] - 1.0, group["pts"][-1][0] + 1.0
        out.append(group)
    return out


def _truth_at(truth: dict, t: float):
    pts = truth["pts"]
    if truth["kind"] == "still":
        return pts[0][1], pts[0][2]
    for (ta, xa, ya), (tb, xb, yb) in zip(pts, pts[1:]):
        if ta <= t <= tb:
            if tb - ta > truth.get("gap", 3.0):
                return None
            f = (t - ta) / (tb - ta) if tb > ta else 0.0
            return xa + f * (xb - xa), ya + f * (yb - ya)
    return None


def _maps(layout, spots, links, blocked, groups, blocks=(), holder=False):
    """Keyed None (every spot), ("group", g) without walk g, ("block", b) without the spots of mark b."""
    maps = {None: rmap.RadioMap(layout, spots, links, blocked, holder)}
    for group in groups:
        maps[("group", group)] = rmap.RadioMap(layout, [s for s in spots if s.get("group") != group], links, blocked,
                                               holder)
    for block in blocks:
        maps[("block", block)] = rmap.RadioMap(layout, [s for s in spots if s.get("block") != block], links, blocked,
                                               holder)
    return maps


def _score_truth(job, st, m, truth, lines, out):
    # heading/stride as App.ctx provides them; without them steps + compass silently does nothing.
    ctx = {"map": m, "settings": st, "heading": job.get("heading") or {}, "stride": job.get("stride")}
    tk = Tracker(lambda: ctx, [])
    mac = truth["phone"]
    still = truth["kind"] == "still"
    prev = None
    errs = []

    def on_tick(t):
        nonlocal prev
        ph = tk.phones.get(mac)
        if not ph or not ph.raw or not truth["t0"] <= t <= truth["t1"]:
            return
        if still and t - truth["t0"] < SETTLE_S:
            return
        here = _truth_at(truth, t)
        if here is None:
            return
        errs.append(math.hypot(ph.raw["x"] - here[0], ph.raw["y"] - here[1]))
        # The oldest refining trail point is the backward-smoothed estimate that gets drawn for good.
        sm = ph.track.smoothed(m)
        if sm and not still and sm[0][0] >= truth["t0"]:
            then = _truth_at(truth, sm[0][0])
            if then:
                out["trail"].append(math.hypot(sm[0][1] - then[0], sm[0][2] - then[1]))
        if still and ph.est:
            if prev is not None:
                out["jitter"].append(math.hypot(ph.est["x"] - prev[0], ph.est["y"] - prev[1]))
            prev = (ph.est["x"], ph.est["y"])

    feed(tk, lines, truth["t0"] - WARMUP_S, truth["t1"], on_tick)
    out["still" if still else "walk"].extend(errs)
    if still and errs:
        out["checks"].append([truth["pts"][0][1], truth["pts"][0][2], sorted(errs)[len(errs) // 2]])


def run_variant(job: dict) -> dict:
    st = cfg.merged(job["settings"])
    blocked = job["blocked"] if st["furniture"] else []
    all_truths = job["truths"]
    walk_groups = {t["group"] for t in all_truths if t["kind"] == "walk"}
    held = {t["block"] for t in all_truths if t["kind"] == "still" and t.get("block")}
    maps = _maps(job["layout"], job["spots"], job["links"], blocked, walk_groups, held, st["holder_body"])
    out = {"still": [], "jitter": [], "walk": [], "trail": [], "checks": []}
    for path, span in job["sessions"]:
        _, lines = load_session(path)
        for truth in all_truths:
            if not (span[0] <= truth["t0"] and truth["t1"] <= span[1]):
                continue
            if truth["kind"] == "walk":
                m = maps[("group", truth["group"])]
            else:
                m = maps[("block", truth["block"]) if truth.get("block") else None]
            _score_truth(job, st, m, truth, lines, out)
    return {"name": job["name"], "key": job.get("key"), **out}


def _pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * (len(values) - 1)))] if values else None


def summarize(res: dict) -> dict:
    # still_med pools every sample, so a few good checks can hide a bad one; checks / check_worst are per check
    # ([x, y, median error]), which is what to read when there are only a handful.
    check_meds = [c[2] for c in res.get("checks", [])]
    jitter = res["jitter"]
    return {
        "name": res["name"], "key": res["key"],
        "still_med": _pct(res["still"], 0.5), "still_p90": _pct(res["still"], 0.9),
        "checks": [[round(v, 3) for v in c] for c in res.get("checks", [])],
        "check_worst": max(check_meds) if check_meds else None,
        "jitter": _pct(jitter, 0.5),
        "jumps": (sum(1 for j in jitter if j > rmap.FT) / len(jitter)) if jitter else None,
        "walk_med": _pct(res["walk"], 0.5), "walk_p90": _pct(res["walk"], 0.9),
        "trail_med": _pct(res.get("trail", []), 0.5),
        "n_still": len(res["still"]), "n_walk": len(res["walk"]),
    }


def jobs(base: dict, layout, spots, links, blocked, truth_list, sessions, heading=None, stride=None) -> List[dict]:
    common = {"layout": layout, "spots": spots, "links": links, "blocked": blocked, "truths": truth_list,
              "sessions": sessions, "heading": heading or {}, "stride": stride}
    out = [dict(common, name="your settings", key=None, settings=dict(base))]
    for key in cfg.DEFAULTS:
        if key in cfg.LAB_SKIP:
            continue
        label = f"{cfg.INFO[key][0]} {'off' if base[key] else 'on'}"
        out.append(dict(common, name=label, key=key, settings=dict(base, **{key: not base[key]})))
    return out
