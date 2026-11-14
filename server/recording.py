"""Session recordings: loading them and feeding their lines through a Tracker (lab.py, tools/analyze_session.py)."""
from __future__ import annotations

import json
from bisect import bisect_left
from pathlib import Path

from tracker import Tracker

TICK_S = 0.25


def load_session(path: Path):
    header, lines = {}, []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("type") == "header":
                header = obj
            elif isinstance(obj.get("rt"), (int, float)):
                lines.append(obj)
    lines.sort(key=lambda o: o["rt"])
    return header, lines


def apply(tr: Tracker, line: dict):
    """Turn one recorded line back into tracker calls. The only place this happens (lab.py and analyze_session.py share it).

    Line kinds: {"rt", "meta": {...}}, {"rt", "motion": {"m", "mv", "st", "hd"}}, {"rt", "anchor": {"m", "x", "y"}},
    {"rt", "trace": {"m", "x", "y"}} and {"rt", "src", "p": packet}.
    """
    if "meta" in line:
        meta = line["meta"] or {}
        tr.laptop_ip = meta.get("laptop_ip")
        tr.laptop_macs.update(str(m).lower() for m in meta.get("laptop_macs") or [])
    elif "motion" in line:
        mo = line["motion"] or {}
        hd = mo.get("hd")
        tr.set_motion(str(mo.get("m", "")), int(mo.get("mv", 0)), int(mo.get("st", 0)), line["rt"],
                      float(hd) if isinstance(hd, (int, float)) else None)
    elif "trace" in line:
        tc = line["trace"] or {}
        tr.trace(str(tc.get("m", "")), float(tc["x"]), float(tc["y"]), line["rt"])
    elif "anchor" in line:
        an = line["anchor"] or {}
        tr.anchor(str(an.get("m", "")), float(an["x"]), float(an["y"]), line["rt"])
    else:
        tr.ingest(line.get("p"), line["rt"], (line.get("src", ""), 0))


def feed(tr: Tracker, lines: list, t0: float, t1: float, on_tick=None):
    """Push lines with t0 <= rt <= t1 through tr as fast as possible, ticking every TICK_S; on_tick(t) after each."""
    for line in lines:
        if "meta" in line and line["rt"] <= t0:
            apply(tr, line)
    next_tick = None
    for line in lines[bisect_left(lines, t0, key=lambda o: o["rt"]):]:
        rt = line["rt"]
        if rt > t1:
            break
        if next_tick is None:
            next_tick = rt
        while next_tick <= rt:
            tr.tick(next_tick)
            if on_tick:
                on_tick(next_tick)
            next_tick += TICK_S
        apply(tr, line)

