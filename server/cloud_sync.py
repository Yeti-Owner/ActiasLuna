"""Room activity -> Moth cloud (cloud/moth_cloud.py on Vultr): live X/Y, entered / left, and stops at points A, B, C.

App.cloud_loop runs Observer.step at OBSERVE_S under App.lock; Uploader posts from its own thread so a slow network
never holds the lock. Config is config/cloud.json ({url, ingest_token, room, optional points}); without it sync is off.

Who is reported: phones the tracker follows (already consent-gated by the AP station list in Tracker._obs) and the
device-free body, each as a per-run pseudonym ("person-1", "body"). No MAC, name or IP leaves the laptop. Decoys
live in App.decoys and never get here. Positions are Phone.raw, never the jammed or smoothed display dot, except
while the holder traces on the phone map: then the finger point is sent instead, as an ordinary position.

Each luna_server start is one run (App.start_cloud names it); every item carries its run, so spooled items from an
earlier run still upload under that run, and the cloud answers only about the latest run by default.

Wire format (POST /ingest, metres, epoch seconds; moth_cloud.Store validates it):
  {"room", "points": {"A": [x, y]}, "samples": [{t, in, nearby, zones}], "positions": [{t, pid, x, y}],
   "events": [{t, pid, kind: enter|leave|arrive|depart, point?, dur?, x?, y?}]}   each item also has "run"
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Optional

import radiomap as rmap

OBSERVE_S = 0.25
POSITION_S = 0.25           # 4 positions/s per person: the frontend's live room follows within about a second
# Heartbeat: must stay well under moth_cloud.MAX_GAP_S (90 s) or the cloud reports "not reporting".
SAMPLE_S = 10.0
NODE_FRESH_S = 10.0          # no board heard for this long = not watching; send nothing rather than "empty"
NEARBY_S = 30.0
ENTER_S = 2.0                # seen this long before "entered" (a one-tick blip is not a visit)
LEAVE_S = 8.0                # unseen this long before "left"
POINT_NEAR_M = 1.0
POINT_EXIT_M = 1.4           # hysteresis against estimate jitter at the edge of POINT_NEAR_M
POINT_STAY_S = 3.0           # near a point this long = "stood by it"
POINT_EXIT_S = 1.5
DEDUPE_BODY_M = 1.0          # a body estimate this close to a phone is that phone's holder
# A trace point this fresh replaces the phone's estimate (matches app.js SNAP_FRESH_S; phone.js posts every 250 ms).
TRACE_FRESH_S = 1.0
# A traced point may sit this far outside the room's bounding box and still count as inside (phone.js lets the
# finger go TRACE_MARGIN_M past the outline); estimates must be inside the polygon.
TRACE_MARGIN_M = 1.0
FLUSH_S = 0.5
BATCH = 2000
SPOOL_MAX = 200_000


class Person:
    def __init__(self, pid: str, t: float):
        self.pid = pid
        self.first = t
        self.last = t
        self.entered = False
        self.x = self.y = 0.0
        self.last_pos = -math.inf
        self.cand: Optional[str] = None
        self.cand_since = 0.0
        self.at: Optional[str] = None
        self.at_since = 0.0
        self.out_since: Optional[float] = None


class Observer:
    """Turns per-tick sightings into positions, samples and events. Pure: no I/O, no clock of its own."""

    def __init__(self):
        self.people: Dict[str, Person] = {}
        self.pids: Dict[str, str] = {}
        self.last_sample = -math.inf
        self.last_key = None

    def pid(self, mac: str) -> str:
        if mac not in self.pids:
            self.pids[mac] = f"person-{len(self.pids) + 1}"
        return self.pids[mac]

    @staticmethod
    def _nearest(points: dict, x: float, y: float):
        if not points:
            return None, math.inf
        k = min(points, key=lambda p: math.hypot(points[p][0] - x, points[p][1] - y))
        return k, math.hypot(points[k][0] - x, points[k][1] - y)

    def _points(self, p: Person, t: float, points: dict, ev: list):
        if p.at is not None:
            d = math.hypot(points[p.at][0] - p.x, points[p.at][1] - p.y) if p.at in points else math.inf
            if d <= POINT_EXIT_M:
                p.out_since = None
                return
            p.out_since = t if p.out_since is None else p.out_since
            if t - p.out_since < POINT_EXIT_S:
                return
            ev.append({"t": round(p.out_since, 2), "pid": p.pid, "kind": "depart", "point": p.at,
                       "dur": round(p.out_since - p.at_since, 1)})
            p.at, p.out_since, p.cand = None, None, None
        near, d = self._nearest(points, p.x, p.y)
        if near is None or d > POINT_NEAR_M:
            p.cand = None
            return
        if p.cand != near:
            p.cand, p.cand_since = near, t
        elif t - p.cand_since >= POINT_STAY_S:
            p.at, p.at_since = near, p.cand_since
            ev.append({"t": round(p.cand_since, 2), "pid": p.pid, "kind": "arrive", "point": near,
                       "x": round(p.x, 2), "y": round(p.y, 2)})

    def _leave(self, p: Person, ev: list):
        if p.at is not None:
            end = p.out_since if p.out_since is not None else p.last
            ev.append({"t": round(end, 2), "pid": p.pid, "kind": "depart", "point": p.at,
                       "dur": round(end - p.at_since, 1)})
        ev.append({"t": round(p.last, 2), "pid": p.pid, "kind": "leave", "x": round(p.x, 2), "y": round(p.y, 2)})

    def step(self, t: float, seen: Dict[str, tuple], points: dict, nearby: int, watching: bool) -> dict:
        """seen: pid -> (x, y) of people inside the room this tick. Returns {samples, positions, events}."""
        out = {"samples": [], "positions": [], "events": []}
        ev = out["events"]
        for pid, (x, y) in seen.items():
            p = self.people.get(pid)
            if p is None:
                p = self.people[pid] = Person(pid, t)
            p.last, p.x, p.y = t, float(x), float(y)
            if not p.entered and t - p.first >= ENTER_S:
                p.entered = True
                ev.append({"t": round(p.first, 2), "pid": pid, "kind": "enter", "x": round(p.x, 2),
                           "y": round(p.y, 2)})
            if p.entered:
                self._points(p, t, points, ev)
                if t - p.last_pos >= POSITION_S:
                    p.last_pos = t
                    out["positions"].append({"t": round(t, 2), "pid": pid, "x": round(p.x, 3), "y": round(p.y, 3)})
        for pid in list(self.people):
            p = self.people[pid]
            if t - p.last > (LEAVE_S if p.entered else ENTER_S):
                if p.entered:
                    self._leave(p, ev)
                del self.people[pid]
        if watching:
            inside = [p for p in self.people.values() if p.entered]
            zones: Dict[str, int] = {}
            for p in inside:
                z = p.at or self._nearest(points, p.x, p.y)[0]
                if z is not None:
                    zones[z] = zones.get(z, 0) + 1
            key = (len(inside), nearby, tuple(sorted(zones.items())))
            if key != self.last_key or t - self.last_sample >= SAMPLE_S:
                self.last_key, self.last_sample = key, t
                out["samples"].append({"t": round(t, 2), "in": len(inside), "nearby": nearby, "zones": zones})
        ev.sort(key=lambda e: e["t"])
        return out


def sightings(live, layout: dict, observer: Observer, t: float):
    """(seen, nearby, watching) from a tracker.Tracker. Call under App.lock."""
    poly = layout["polygon"]
    seen, nearby = {}, 0
    for mac, ph in live.phones.items():
        est = ph.est or {}
        trace = getattr(ph, "trace", None)
        xy, traced = None, False
        # Trace points are stamped in wall time (luna_server._phone_trace), the same clock as t.
        if trace and t - trace[-1][0] <= TRACE_FRESH_S:
            xy, traced = (trace[-1][1], trace[-1][2]), True
        elif ph.raw and est.get("kind") not in (None, "stale"):
            xy = ph.raw["x"], ph.raw["y"]
        if xy:
            x, y = xy
            if rmap.point_in_poly(x, y, poly) or (traced and _near_box(x, y, poly, TRACE_MARGIN_M)):
                seen[observer.pid(mac)] = (x, y)
                continue
        if ph.last is not None and t - ph.last <= NEARBY_S:
            nearby += 1
    body = live.body
    if body and body.get("present"):
        bx, by = float(body["x"]), float(body["y"])
        if all(math.hypot(bx - x, by - y) > DEDUPE_BODY_M for x, y in seen.values()):
            seen["body"] = (bx, by)
    watching = any(n.last is not None and t - n.last <= NODE_FRESH_S for n in live.nodes.values())
    return seen, nearby, watching


def _near_box(x: float, y: float, poly, margin: float) -> bool:
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return min(xs) - margin <= x <= max(xs) + margin and min(ys) - margin <= y <= max(ys) + margin


def points_from(layout: dict, label, override=None) -> dict:
    """Named points people stand by: config/cloud.json "points" if given, else the boards (A, B, C)."""
    if override:
        return {str(k): (float(v[0]), float(v[1])) for k, v in override.items()}
    return {str(label(nid)): (p["x"], p["y"]) for nid, p in layout["receivers"].items()}


def new_run_id(t: float) -> str:
    return time.strftime("run-%Y%m%d-%H%M%S", time.localtime(t))


def load_config(path: Path) -> Optional[dict]:
    """config/cloud.json, or None when missing or still holding the example placeholders."""
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not all(cfg.get(k) for k in ("url", "ingest_token", "room")):
        return None
    if "YOUR-" in cfg["url"] or "paste" in cfg["ingest_token"]:
        return None
    return cfg


class Spool:
    """Unsent items on disk, so nothing is lost while the laptop is on the offline demo Wi-Fi."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.items = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    self.items.append(json.loads(line))
                except ValueError:
                    pass

    def add(self, kind: str, items: list, run: str = ""):
        if not items:
            return
        rows = [{kind: dict(i, run=run) if run else i} for i in items]
        with self.lock:
            self.items.extend(rows)
            if len(self.items) > SPOOL_MAX:
                self.items = self.items[-SPOOL_MAX:]
            with open(self.path, "a", encoding="utf-8") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)

    def head(self, n: int) -> list:
        with self.lock:
            return self.items[:n]

    def drop(self, n: int):
        with self.lock:
            self.items = self.items[n:]
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(json.dumps(r) + "\n" for r in self.items)
            os.replace(tmp, self.path)

    def __len__(self):
        return len(self.items)


class Uploader:
    def __init__(self, cfg: dict, spool: Spool, log=print):
        self.cfg = cfg
        self.spool = spool
        self.log = log
        self.points: dict = {}
        self.backoff = 5.0
        self.warned_old = False
        self.state = "starting"

    def post(self, batch: list, timeout=8.0) -> dict:
        body = {"room": self.cfg["room"], "samples": [], "positions": [], "events": [],
                "points": {k: [round(v[0], 3), round(v[1], 3)] for k, v in self.points.items()}}
        keys = {"s": "samples", "p": "positions", "e": "events"}
        for row in batch:
            for k, v in row.items():
                body[keys[k]].append(v)
        req = urllib.request.Request(self.cfg["url"].rstrip("/") + "/ingest", data=json.dumps(body).encode(),
                                     method="POST", headers={"Content-Type": "application/json",
                                                             "Authorization": f"Bearer {self.cfg['ingest_token']}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

    def flush_once(self) -> bool:
        batch = self.spool.head(BATCH)
        if not batch:
            return True
        try:
            out = self.post(batch)
        except urllib.error.HTTPError as exc:
            why = ("INGEST token rejected: put the real one in config/cloud.json and restart" if exc.code == 401
                   else f"cloud refused the upload ({exc.code})")
            if exc.code == 400:
                self.spool.drop(len(batch))      # malformed for this server: retrying would block everything behind it
                why += "; dropped that batch"
            self._fail(why)
            return False
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._fail(f"cloud unreachable ({getattr(exc, 'reason', exc)})")
            return False
        self.spool.drop(len(batch))
        self.backoff = 5.0
        if "positions_stored" not in out and not self.warned_old:
            self.warned_old = True
            self.log("cloud: the Vultr server runs the old counts-only moth_cloud.py, so positions and events are "
                     "being thrown away. Redeploy it (cloud/README.md, step 2).")
        if self.state != "ok":
            self.log(f"cloud: uploading to {self.cfg['url']} (room {self.cfg['room']})")
        self.state = "ok"
        return True

    def _fail(self, why: str):
        if self.state != why:
            self.log(f"cloud: {why}; {len(self.spool)} items saved for later")
        self.state = why
        self.backoff = min(self.backoff * 2, 300.0)

    def run(self):
        while True:
            if self.flush_once():
                time.sleep(FLUSH_S if len(self.spool) < BATCH else 0.05)
            else:
                time.sleep(self.backoff)
