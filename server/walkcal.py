"""Calibration walks: turn a walk capture into map training spots and ground truth.

The walker goes dot to dot and taps each dot on the phone page while standing on it. Marks are [t, x, y] or
[t, x, y, 1]; the trailing 1 (detour) means the leg ending there was not straight.

People do not move at constant speed between taps: they tap, look for the next dot, walk a burst, stop, tap (in real
walks 60-70 % of the time was standing). So the position between two taps comes from the phone's motion reports
[t, steps, compass, moving]: on the first dot until movement starts, along the leg in proportion to time spent
moving, and on the second dot after it stops. Without motion reports it falls back to constant speed.

Spots have the standing-spot shape plus "group" (one phone offset per walk), "block" (held out together in
leave-one-out), "eval_s" (window length), "still" (False = averaged while moving, less fading) and "facing" (map angle
the walker faced so radiomap can undo their body's shadow); see radiomap._fit / _noise / evaluate. Bump VERSION
whenever the spots produced from the same capture change: luna_server rebuilds stored spots from the raw walks.
"""
from __future__ import annotations

import math
from typing import List, Optional

import radiomap as rmap
import sensing

VERSION = 1
STEP_S = 0.25
WALK_WIN_S = 1.0
MIN_DWELL_S = 1.0
DWELL_EVAL_S = 3.0
MAX_SPEED = 2.5
MIN_SPEED = 0.35
# Motion reports describe roughly the last second, so activity is shifted back by MOTION_LAG_S. A leg that has reports
# but no detected movement only yields truth within TAP_BEFORE_S before / TAP_AFTER_S after its taps.
MOTION_LAG_S = 0.6
MOTION_GAP_S = 3.0
# A counted step means moving until STEP_HOLD_S after its report (steps come ~0.55 s apart, reports more often).
STEP_HOLD_S = 0.6
TURN_S = 0.5
BLOCK_WINDOWS = 4
TAP_BEFORE_S = 1.5
TAP_AFTER_S = 1.0


def _marks(capture: dict) -> List[list]:
    return sorted(capture.get("marks") or [])


def _motion(capture: dict) -> List[list]:
    return sorted(row for row in capture.get("motion") or [] if row[1] is not None)


def _activity(motion: List[list], times: List[float]) -> List[int]:
    """1 for each time where the phone was moving (steps counted or its moving flag set), else 0."""
    active = [0] * len(times)
    for prev, cur in zip(motion, motion[1:]):
        stepped = cur[1] > prev[1]
        flagged = len(cur) > 3 and cur[3]
        if cur[0] - prev[0] > MOTION_GAP_S or not (stepped or flagged):
            continue
        start = prev[0] - MOTION_LAG_S
        end = cur[0] - MOTION_LAG_S + (STEP_HOLD_S if stepped else 0.0)
        for i, t in enumerate(times):
            if start <= t < end:
                active[i] = 1
    return active


def truth(capture: dict) -> List[tuple]:
    """Known walker positions [(t, x, y, moving, facing or None)] every STEP_S, t relative to the capture start.

    Unknown stretches (detours, legs walked implausibly fast or slow) are simply absent.
    """
    marks, motion = _marks(capture), _motion(capture)
    out: List[tuple] = []
    for (t0, x0, y0, *_), (t1, x1, y1, *rest) in zip(marks, marks[1:]):
        if t1 <= t0:
            continue
        times = [t0 + i * STEP_S for i in range(int((t1 - t0) / STEP_S) + 1)]
        length = math.hypot(x1 - x0, y1 - y0)
        heading = math.atan2(y1 - y0, x1 - x0) if length > 1e-6 else None
        detour = bool(rest and rest[0])
        if not motion:
            if not detour and length / (t1 - t0) <= MAX_SPEED:
                for t in times:
                    f = (t - t0) / (t1 - t0)
                    out.append((t, x0 + f * (x1 - x0), y0 + f * (y1 - y0), 1, heading))
            continue
        active = _activity(motion, times)
        total = sum(active)
        moving_s = total * STEP_S
        if not detour and moving_s > 0 and MIN_SPEED <= length / moving_s <= MAX_SPEED:
            covered = 0
            for t, a in zip(times, active):
                covered += a
                f = covered / total
                out.append((t, x0 + f * (x1 - x0), y0 + f * (y1 - y0), a, heading if a else None))
        else:
            out.extend((t, x0, y0, 0, None) for t in times if t - t0 <= TAP_AFTER_S)
            out.extend((t, x1, y1, 0, None) for t in times if t1 - t <= TAP_BEFORE_S and t - t0 > TAP_AFTER_S)
    return out


def _compass_at(motion: List[list], t: float) -> Optional[float]:
    nearest = min(motion, key=lambda row: abs(row[0] - t), default=None)
    if nearest and nearest[2] is not None and abs(nearest[0] - t) < 1.5:
        return nearest[2]
    return None


def heading_fit(capture: dict, min_n: int = 10):
    """Compass offset for steps + compass tracking, as (offset rad, agreement 0..1) or None.

    Walking a straight leg the walker faces the next dot, so leg angle minus compass heading is the room's offset
    (magnetic north vs map axes, plus phone bias). The tracker uses map angle = radians(heading) + offset.
    """
    motion = _motion(capture)
    taps = [m[0] for m in _marks(capture)]
    sum_c = sum_s = 0.0
    used = 0
    for t, _, _, moving, angle in truth(capture):
        # Walkers turn towards the next dot around each tap, so readings near a tap are skipped.
        if not moving or angle is None or any(abs(t - tap) < TURN_S for tap in taps):
            continue
        compass = _compass_at(motion, t)
        if compass is None:
            continue
        diff = angle - math.radians(compass)
        sum_c += math.cos(diff)
        sum_s += math.sin(diff)
        used += 1
    if used < min_n:
        return None
    return math.atan2(sum_s, sum_c), math.hypot(sum_c, sum_s) / used


def _runs(tr: List[tuple]):
    """Yield (i, j, still) for consecutive truth stretches that become one spot each."""
    i = 0
    while i < len(tr):
        still = not tr[i][3]
        limit = 1e9 if still else WALK_WIN_S
        j = i
        while j + 1 < len(tr):
            nxt = tr[j + 1]
            if (not nxt[3]) != still or nxt[0] - tr[j][0] >= 2 * STEP_S or nxt[0] - tr[i][0] >= limit - 1e-6:
                break
            if still and (nxt[1], nxt[2]) != (tr[i][1], tr[i][2]):
                break
            j += 1
        if tr[j][0] + STEP_S - tr[i][0] >= (MIN_DWELL_S if still else WALK_WIN_S / 2) - 1e-6:
            yield i, j, still
        i = j + 1


def walk_spots(capture: dict, walk_id: str) -> List[dict]:
    tr = truth(capture)
    if len(_marks(capture)) < 2 or not tr:
        return []
    samples = sorted(capture.get("samples") or [])
    motion = _motion(capture)
    fit = heading_fit(capture)
    offset = fit[0] if fit and fit[1] > 0.5 else None
    spots: List[dict] = []

    for i, j, still in _runs(tr):
        t0, t1 = tr[i][0], tr[j][0] + STEP_S
        window = [s for s in samples if t0 <= s[0] < t1]
        per = rmap.per_node(window, rmap.MIN_FRAMES)
        if len(per) < 2:
            continue
        seg = tr[i:j + 1]
        compass = _compass_at(motion, (t0 + t1) / 2) if offset is not None else None
        if compass is not None:
            facing = math.radians(compass) + offset
        else:
            facing = None if still else seg[len(seg) // 2][4]
        # Truth points mark the start of each STEP_S slice; readings are centred half a slice later.
        after = tr[j + 1] if j + 1 < len(tr) and tr[j + 1][0] - tr[j][0] < 2 * STEP_S else tr[j]
        x = (sum(p[1] for p in seg) + 0.5 * (after[1] - seg[0][1])) / len(seg)
        y = (sum(p[2] for p in seg) + 0.5 * (after[2] - seg[0][2])) / len(seg)
        spots.append({
            "kind": "walk", "phone": capture["phone"], "x": round(x, 3), "y": round(y, 3),
            "orientation": capture.get("orientation", "hand"), "duration": round(t1 - t0, 3),
            "per_node": per, "warnings": [],
            "samples": [[round(s[0] - t0, 3)] + list(s[1:]) for s in window], "t": capture["t0"] + t0,
            "facing": None if facing is None else round(facing, 3), "group": walk_id,
            "block": f"{walk_id}:{len(spots)}", "still": bool(still),
            "eval_s": round(min(t1 - t0, DWELL_EVAL_S if still else WALK_WIN_S), 3),
        })

    # Adjacent moving windows sit a step apart and share their error, so up to BLOCK_WINDOWS are held out together.
    block, prev, members = None, None, 0
    for spot in spots:
        if not spot["still"]:
            joins = (prev is not None and not prev["still"] and spot["t"] - prev["t"] < 2 * WALK_WIN_S
                     and members < BLOCK_WINDOWS)
            if joins:
                spot["block"] = block
                members += 1
            else:
                members = 1
            block = spot["block"]
        prev = spot
    return spots


def stride_fit(capture: dict):
    """Metres per counted step over legs where the step counter ran (detectors and strides vary per person/phone)."""
    marks, motion = _marks(capture), _motion(capture)
    if len(marks) < 2 or len(motion) < 4:
        return None

    def steps_near(t):
        nearest = min(motion, key=lambda row: abs(row[0] - t))
        return nearest[1] if abs(nearest[0] - t) < 1.0 else None

    legs = []
    for (t0, x0, y0, *_), (t1, x1, y1, *rest) in zip(marks, marks[1:]):
        if rest and rest[0]:
            continue
        a, b = steps_near(t0), steps_near(t1)
        if a is None or b is None or b <= a:
            continue
        legs.append((math.hypot(x1 - x0, y1 - y0), b - a))
    # A leg where the counter missed most steps would stretch the stride: prefer legs with plausible step lengths.
    plausible = [(d, n) for d, n in legs if 0.3 <= d / n <= 1.1] or legs
    steps = sum(n for _, n in plausible)
    if steps < 10:
        return None
    return min(1.0, max(0.4, sum(d for d, _ in plausible) / steps))


def raw_walk(capture: dict, walk_id: str) -> dict:
    """The capture as stored under "walks" in the calibration file: enough to rebuild spots and truth later."""
    return {"id": walk_id, "phone": capture["phone"], "orientation": capture.get("orientation", "hand"),
            "t0": capture["t0"], "marks": capture.get("marks") or [], "motion": capture.get("motion") or [],
            "samples": capture.get("samples") or []}


def df_walk(capture: dict) -> List[dict]:
    """Device-free training windows: link readings along the walk, tagged with the walker's position.

    The walker's body disturbs the board-to-board links; sensing.DFMap fits its per-link models on these.
    """
    marks = _marks(capture)
    items = sensing.link_items(capture.get("links"))
    tr = truth(capture)
    if len(marks) < 2 or not items or not tr:
        return []
    out = []
    k = 0
    for tc, window in sensing.windows(items, marks[0][0], marks[-1][0], step=0.5):
        while k + 1 < len(tr) and tr[k + 1][0] <= tc:
            k += 1
        if abs(tr[k][0] - tc) > STEP_S:
            continue
        links = {}
        for key, (rssi, shape, change, frames, sd) in window.items():
            links[key] = [round(rssi, 2), [round(v, 2) for v in shape] if shape else None,
                          None if change is None else round(change, 3), frames, round(sd, 2)]
        out.append({"x": round(tr[k][1], 3), "y": round(tr[k][2], 3), "t": capture["t0"] + tc, "l": links})
    return out
