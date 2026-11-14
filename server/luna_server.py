"""ActiasLuna laptop server (Python 3.10+, numpy).

Threads: udp_loop (ingest), subscribe_loop (LUNA_SUB to boards every second), tick_loop (estimator at 4 Hz), HTTP
handlers, plus replay and evaluation workers. All shared state is guarded by App.lock. The dashboard binds to
127.0.0.1 only; the phone page answers only the demo subnet. LUNA_DATA overrides the data directory.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import traceback
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parents[1] / "tools"))

import import_scan  # noqa: E402
import lab  # noqa: E402
import planner  # noqa: E402
import radiomap as rmap  # noqa: E402
import sensing  # noqa: E402
import settings as cfg  # noqa: E402
import walkcal  # noqa: E402
from replay import TICK_S, Replay, load_session  # noqa: E402
from crowd import Crowd, jam_offset  # noqa: E402
from room import Room  # noqa: E402
from tracker import Status, Tracker, capture_phase, fmt_mac  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("LUNA_DATA", ROOT / "data"))
STATIC = Path(__file__).resolve().parent / "static"
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MIME = {"js": "application/javascript", "css": "text/css", "html": "text/html", "json": "application/json",
        "glb": "model/gltf-binary", "png": "image/png", "svg": "image/svg+xml"}
SSE_S = 0.25
LINK_CHECK_S = 30.0
ORIENTATIONS = ("hand", "front_pocket", "back_pocket", "bag")
EMPTY_LEAVE_S = 15.0
EMPTY_S = 30.0
SCAN_MATCH_M = 0.3
SCAN_MAX_BYTES = 300 << 20
BUSY = (409, "a capture is already running")
# "I'm here": MARK_DELAY_S lets the hand settle after the tap, then MARK_S of standing readings. Stretches the phone
# reports moving (MARK_MOVE_PAD_S either side, reports lag the motion) are dropped; under MARK_MIN_S left, or under 2
# boards with MARK_MIN_FRAMES, the mark is refused rather than training the map on a wrong position.
MARK_DELAY_S = 1.0
MARK_S = 8.0
MARK_MIN_S = 4.0
MARK_MOVE_PAD_S = 0.75
MARK_MIN_FRAMES = 10
# Someone passing between the phone and a board only weakens it: a board's packets more than MARK_SHADOW_DB under its
# median over the mark are that, not the spot, and are dropped.
MARK_SHADOW_DB = 6.0
LEGS_KEEP = 400

FT = rmap.FT
DEFAULT_LAYOUT = {
    "name": "default",
    "polygon": [[0, 0], [10 * FT, 0], [10 * FT, 11 * FT], [0, 11 * FT]],
    "receivers": {},
}


class BadRequest(Exception):
    pass


def require(cond, message: str):
    if not cond:
        raise BadRequest(message)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def finite(value) -> float:
    f = float(value)
    if not math.isfinite(f):
        raise ValueError("number must be finite")
    return f


def orientation(body: dict) -> str:
    o = str(body.get("orientation", "hand"))
    require(o in ORIENTATIONS, "orientation")
    return o


def parse_layout(raw) -> dict:
    """Layout from untrusted JSON; raises on anything malformed."""
    poly = [[finite(p[0]), finite(p[1])] for p in raw["polygon"]]
    if len(poly) < 3:
        raise ValueError("polygon needs 3+ points")
    receivers = {str(k): {"x": finite(v["x"]), "y": finite(v["y"])} for k, v in (raw.get("receivers") or {}).items()}
    return {"name": str(raw.get("name", "default")), "polygon": poly, "receivers": receivers}


def _line_rt(line):
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    return obj["rt"] if isinstance(obj, dict) and isinstance(obj.get("rt"), (int, float)) else None


def session_span(path: Path):
    """(first rt, last rt) of a recording, reading only its head and tail."""
    try:
        with open(path, "rb") as fh:
            first = next((t for t in map(_line_rt, fh) if t is not None), None)
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 65536))
            tail = fh.read().splitlines()[1:]
            last = next((t for t in map(_line_rt, reversed(tail)) if t is not None), None)
    except OSError:
        return None
    return (first, last) if first is not None and last is not None else None


def _bbox(poly):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


class App:
    def __init__(self, args, net: dict):
        self.args = args
        self.net = net
        self.boards = read_json(ROOT / "config" / "boards.json", {}).get("boards", [])
        self.lock = threading.RLock()
        self.state = read_json(DATA / "state.json", {})
        self.settings = cfg.merged(self.state.get("settings"))
        self.layout = self._valid_layout(read_json(DATA / "layouts" / "current.json", None))
        self.calib = self._load_calib(self.calib_name())
        self.lab = {"state": "idle"}
        self.blocked = []
        self.scan_objects = []
        self._route_cache = (None, None)
        self.map = None
        self.df_map = None
        self.map_links = {}
        self.evaluation = None
        self._eval_gen = 0
        self.live = Tracker(self.ctx, self.boards)
        self.live.on_capture_done = self._capture_done
        # Display-only decoy phones for the 3D view, toggled by the Uno Q button or /api/crowd (crowd.py).
        self.decoys = Crowd()
        self.decoy_room = None
        self._decoy_origin = (0.0, 0.0)
        self.rec_file = None
        self.rec_path = None
        self.rec_count = 0
        self.replay = None
        self.targets = []
        for item in (args.targets or "").split(","):
            item = item.strip()
            if item:
                host, port = item.rsplit(":", 1)
                self.targets.append((host, int(port)))
        self.known_nodes = set()
        self.sock = None
        self._snap_cache = {}
        self.phone_port = None
        self.phone_https = False
        self._plan_cache = (None, [])
        # mac -> (rt, x, y) where that phone's last accepted mark ended; the next mark's leg starts there.
        self.last_mark = {}
        # mac -> outcome of that phone's latest mark, shown on its page (phone_state "result").
        self.mark_results = {}
        self._rebuild()

    @staticmethod
    def _valid_layout(raw):
        try:
            return parse_layout(raw)
        except (TypeError, KeyError, ValueError, IndexError, AttributeError):
            return parse_layout(DEFAULT_LAYOUT)

    def calib_name(self) -> str:
        """One calibration set per room, named after the layout: a new room starts fresh, an old one comes back."""
        return re.sub(r"[^A-Za-z0-9._-]+", "_", self.layout.get("name") or "default")[:60] or "default"

    def _load_calib(self, name: str) -> dict:
        calib = read_json(DATA / "calibration" / f"{name}.json", None)
        if not isinstance(calib, dict) or not isinstance(calib.get("spots"), list):
            calib = {"name": name, "created": time.time(), "spots": [], "tests": []}
        calib["name"] = name
        calib.setdefault("tests", [])
        # df_base: empty-room link baseline; df_walk: link windows along phone walks; heading: {mac: [offset rad,
        # agreement]}; stride: metres per counted step. See sensing.py and walkcal.py.
        calib.setdefault("df_walk", [])
        calib.setdefault("heading", {})
        # Raw walk captures. Walk spots, heading and stride derive from them (and from legs), so a new walkcal.VERSION
        # rebuilds them here instead of asking for another walk.
        calib.setdefault("walks", [])
        # Walks between consecutive "I'm here" marks: map spots (walkcal.leg_spots), compass and stride.
        calib.setdefault("legs", [])
        if calib.get("walkcal_v") != walkcal.VERSION and (calib["walks"] or calib["legs"]):
            self._derive_walks(calib)
        return calib

    @staticmethod
    def _derive_walks(calib: dict):
        ids = {w["id"] for w in calib["walks"]}
        calib["spots"] = [s for s in calib["spots"]
                          if s.get("group") not in ids and not str(s.get("group", "")).startswith("leg:")]
        for walk in calib["walks"]:
            calib["spots"].extend(walkcal.walk_spots(walk, walk["id"]))
        for leg in calib["legs"]:
            calib["spots"].extend(walkcal.leg_spots(leg))
        App._refit_motion(calib)
        calib["walkcal_v"] = walkcal.VERSION

    @staticmethod
    def _refit_motion(calib: dict):
        """Compass offsets and stride from every stored walk and mark leg, then each mark's facing from them (marks
        made before the offset was known get it now). Files from before raw walks were kept have nothing to refit
        from, so their values stay."""
        if calib["walks"] or calib["legs"]:
            calib["heading"], stride = walkcal.motion_fit(calib["walks"], calib["legs"])
            if stride:
                calib["stride"] = stride
            else:
                calib.pop("stride", None)
        for spot in calib["spots"]:
            offset = calib["heading"].get(spot.get("phone"))
            if spot.get("kind") == "mark" and spot.get("compass") is not None:
                spot["facing"] = None if offset is None else round(math.radians(spot["compass"]) + offset[0], 3)

    def _save_calib(self):
        write_json(DATA / "calibration" / f"{self.calib['name']}.json", self.calib)

    def _scan_objects(self) -> list:
        # Furniture is only trusted when the scan outline matches the current layout (same coordinate frame).
        scan = read_json(DATA / "scan" / "room.json", None)
        if not isinstance(scan, dict) or not scan.get("polygon"):
            return []
        if any(abs(a - b) > SCAN_MATCH_M for a, b in zip(_bbox(scan["polygon"]), _bbox(self.layout["polygon"]))):
            return []
        return [o for o in scan.get("objects", []) if len(o.get("footprint", [])) >= 3]

    def route(self):
        """Guided calibration route and check points for the phone page; recomputed only when the room changes."""
        sig = json.dumps([self.layout["polygon"], self.scan_objects])
        if self._route_cache[0] != sig:
            dots = planner.walk_route(self.layout["polygon"], self.scan_objects)
            checks = planner.check_points(self.layout["polygon"], self.scan_objects, dots)
            self._route_cache = (sig, (dots, checks))
        dots, checks = self._route_cache[1]
        return (planner.reverse_route(dots) if self.route_reversed(dots) else dots), checks

    def route_reversed(self, dots=None) -> bool:
        """Next walk goes whichever way fewer stored walks went, judged by which route end their first tap was near."""
        if dots is None:
            dots = self._route_cache[1][0] if self._route_cache[1] else []
        if len(dots) < 2:
            return False
        forward = backward = 0
        for walk in self.calib.get("walks", []):
            marks = sorted(walk.get("marks") or [])
            if not marks:
                continue
            x, y = marks[0][1], marks[0][2]
            if math.hypot(x - dots[0][0], y - dots[0][1]) <= math.hypot(x - dots[-1][0], y - dots[-1][1]):
                forward += 1
            else:
                backward += 1
        return forward > backward

    def _rebuild(self, evaluate=True):
        links = self.live.links_dict()
        self.map_links = links
        self.scan_objects = self._scan_objects()
        try:   # decoys stand off the scanned furniture; same 0..w frame as _tick_decoys (layout bbox min corner)
            self.decoy_room = Room.from_dict({"polygon": self.layout.get("polygon"), "objects": self.scan_objects})
        except ValueError:
            self.decoy_room = None
        self.blocked = planner.blocked(self.scan_objects) if self.settings["furniture"] else []
        has_boards = bool(self.layout.get("receivers"))
        self.map = (rmap.RadioMap(self.layout, self.calib["spots"], links, self.blocked, self.settings["holder_body"])
                    if has_boards else None)
        base = self.calib.get("df_base")
        self.df_map = (sensing.DFMap(self.layout, base, self.calib.get("df_walk"), self.blocked)
                       if base and has_boards else None)
        if not evaluate:
            return
        self._eval_gen += 1
        if not (self.calib["spots"] and self.map):
            self.evaluation = None
            return
        # Slow, so it runs outside the lock; the generation number discards results from superseded rebuilds and the
        # previous evaluation stays visible until the new one lands.
        gen, layout, spots, blocked = self._eval_gen, self.layout, list(self.calib["spots"]), self.blocked

        def worker():
            try:
                result = rmap.evaluate(layout, spots, links, blocked=blocked)
            except Exception:
                traceback.print_exc()
                return
            with self.lock:
                if gen == self._eval_gen:
                    self.evaluation = result

        threading.Thread(target=worker, daemon=True).start()

    def _headings(self) -> dict:
        return {mac: h[0] for mac, h in self.calib.get("heading", {}).items()}

    def ctx(self) -> dict:
        return {"map": self.map, "settings": self.settings, "df": self.df_map, "layout": self.layout,
                "objects": [o["footprint"] for o in self.scan_objects], "heading": self._headings(),
                "stride": self.calib.get("stride")}

    def start_spot(self, body: dict, phone: str, default_s: float, delay: float):
        """Standing calibrate/test capture from a request body; the caller checked nothing is running."""
        kind = body.get("kind", "calibrate")
        require(kind in ("calibrate", "test"), "kind")
        x, y = finite(body["x"]), finite(body["y"])
        require(rmap.point_in_poly(x, y, self.layout["polygon"]), "spot must be inside the room")
        duration = max(3.0, min(120.0, finite(body.get("duration", default_s))))
        self.live.start_capture(kind, phone, x, y, orientation(body), duration, time.time() + delay)

    def start_mark(self, phone: str, body: dict):
        """Mark "I'm here" at (x, y): a standing capture. The leg walked since this phone's previous mark is cut
        from the motion log now, while the log still holds it."""
        x, y = finite(body["x"]), finite(body["y"])
        require(rmap.point_in_poly(x, y, self.layout["polygon"]), "tap inside the room")
        now = time.time()
        self.live.start_capture("mark", phone, x, y, orientation(body), MARK_S, now + MARK_DELAY_S)
        prev = self.last_mark.get(phone)
        if prev and now - prev[0] <= walkcal.LEG_MAX_S:
            self.live.capture["leg"] = {"phone": phone, "t0": prev[0], "x0": prev[1], "y0": prev[2], "x1": x, "y1": y,
                                        "t1": round(now - prev[0], 3),
                                        "motion": self.live.motion_between(phone, prev[0], now),
                                        "samples": self.live.samples_between(phone, prev[0], now)}
        self.mark_results.pop(phone, None)

    def start_empty(self, phone: str):
        """Empty-room recording for device-free sensing; starts after EMPTY_LEAVE_S so the person can leave."""
        self.live.start_capture("empty", phone, 0.0, 0.0, "hand", EMPTY_S, time.time() + EMPTY_LEAVE_S)

    def sensing_on(self) -> bool:
        if any(self.settings[k] for k in cfg.SENSE_KEYS):
            return True
        return bool(self.live.capture and self.live.capture["kind"] == "empty")

    def set_setting(self, key: str, on: bool):
        require(key in cfg.DEFAULTS, "unknown setting")
        self.settings[key] = bool(on)
        self.state["settings"] = dict(self.settings)
        write_json(DATA / "state.json", self.state)
        if key in ("furniture", "holder_body"):
            self._rebuild()

    def start_lab(self):
        """Score every settings variant on recorded sessions in worker processes; results land in self.lab."""
        if self.lab.get("state") == "running":
            return
        truth_list = lab.truths(self.calib)
        sessions = []
        for path in sorted((DATA / "sessions").glob("*.jsonl")):
            span = session_span(path)
            if span and any(span[0] <= t["t0"] and t["t1"] <= span[1] for t in truth_list):
                sessions.append((str(path), span))
        if not sessions:
            self.lab = {"state": "empty", "truths": len(truth_list)}
            return
        jobs = lab.jobs(dict(self.settings), self.layout, list(self.calib["spots"]), dict(self.map_links),
                        planner.blocked(self.scan_objects), truth_list, sessions, self._headings(),
                        self.calib.get("stride"))
        self.lab = {"state": "running", "started": time.time(), "sessions": len(sessions), "truths": len(truth_list)}

        def worker():
            from concurrent.futures import ProcessPoolExecutor
            try:
                workers = max(1, min(len(jobs), (os.cpu_count() or 2) - 1))
                with ProcessPoolExecutor(max_workers=workers) as pool:
                    rows = [lab.summarize(r) for r in pool.map(lab.run_variant, jobs)]
                result = {"state": "done", "rows": rows, "finished": time.time(), "sessions": len(sessions),
                          "truths": len(truth_list)}
            except Exception as exc:
                traceback.print_exc()
                result = {"state": "error", "error": str(exc)}
            with self.lock:
                self.lab = result

        threading.Thread(target=worker, daemon=True).start()

    def _note(self, text: str):
        self.live.events.append([time.time(), text])

    def _capture_done(self, cap: dict):
        kind = cap["kind"]
        if kind == "empty":
            self._finish_empty(cap)
        elif kind == "walk":
            self._finish_walk(cap)
        elif kind == "mark":
            self._finish_mark(cap)
        else:
            self._finish_spot(cap)

    def _finish_empty(self, cap: dict):
        items = sensing.link_items(cap.get("links"))
        base = sensing.baseline([w for _, w in sensing.windows(items, 0.0, cap["duration"])])
        if base:
            self.calib["df_base"] = base
            self._save_calib()
            self._rebuild(evaluate=False)
        hint = "" if base else " (no link data: are the boards running current firmware?)"
        self._note(f"empty room recorded: {len(base)} board-to-board links{hint}")

    def _finish_walk(self, cap: dict):
        walk_id = f"walk{int(cap['t0'])}"
        spots = walkcal.walk_spots(cap, walk_id)
        self.calib["spots"].extend(spots)
        if spots:
            self.calib["walks"].append(walkcal.raw_walk(cap, walk_id))
            self.calib["walkcal_v"] = walkcal.VERSION
        self.calib["df_walk"].extend(walkcal.df_walk(cap))
        self._refit_motion(self.calib)
        self._save_calib()
        if spots:
            self._rebuild()
        standing = sum(1 for s in spots if s.get("still"))
        if not spots:
            note = " (need 2+ taps while walking)"
        elif not cap.get("motion"):
            note = " - Motion was off on the phone, so positions between taps are guesses: turn it on and walk again"
        else:
            note = ""
        self._note(f"walk saved: {standing} standing + {len(spots) - standing} walking points "
                   f"from {len(cap['marks'])} taps{note}")

    def _finish_spot(self, cap: dict):
        record = {k: cap[k] for k in ("kind", "phone", "x", "y", "orientation", "duration", "per_node", "warnings",
                                      "samples")}
        record["t"] = cap["t0"]
        if cap["kind"] == "calibrate":
            self.calib["spots"].append(record)
            message = "calibration spot saved"
        else:
            errs = []
            if self.map:
                errs = [round(e, 3) for e in rmap.window_errors(self.map, cap["samples"], cap["x"], cap["y"])]
            record["errors_m"] = errs
            del record["samples"]
            self.calib["tests"].append(record)
            message = "accuracy test: " + (f"median error {sorted(errs)[len(errs) // 2] / FT:.1f} ft"
                                           if errs else "no estimate")
        self._save_calib()
        if cap["kind"] == "calibrate":
            self._rebuild()
        frames = sum(v["n"] for v in cap["per_node"].values())
        warn = " WARN " + "; ".join(cap["warnings"]) if cap["warnings"] else ""
        self._note(f"{message} at ({cap['x'] / FT:.1f}, {cap['y'] / FT:.1f}) ft, {frames} frames{warn}")

    @staticmethod
    def _still_samples(cap: dict):
        """(samples outside the stretches the phone reported moving and not shadowed, seconds of standing left)."""
        moving = []
        rows = sorted(cap.get("motion") or [])
        for prev, cur in zip([None] + rows, rows):
            stepped = prev is not None and cur[1] is not None and prev[1] is not None and cur[1] > prev[1]
            if cur[3] or stepped:
                # A report describes about the last second.
                moving.append((cur[0] - 1.0 - MARK_MOVE_PAD_S, cur[0] + MARK_MOVE_PAD_S))
        kept = [s for s in cap["samples"] if not any(a <= s[0] <= b for a, b in moving)]
        levels = {}
        for s in kept:
            levels.setdefault(s[1], []).append(s[3])
        median = {b: sorted(v)[len(v) // 2] for b, v in levels.items()}
        kept = [s for s in kept if s[3] >= median[s[1]] - MARK_SHADOW_DB]
        step = 0.05
        still_s = step * sum(1 for i in range(int(cap["duration"] / step))
                             if not any(a <= i * step <= b for a, b in moving))
        return kept, still_s

    def _finish_mark(self, cap: dict):
        """Score the current map where the person says they stand (an honest check: the spot isn't in it yet), then
        add the spot, learn from the walk that led here and pin the live dot to it."""
        phone, x, y, now = cap["phone"], cap["x"], cap["y"], time.time()
        kept, still_s = self._still_samples(cap)
        per = rmap.per_node(kept)
        heard = [b for b, v in per.items() if v["n"] >= MARK_MIN_FRAMES]
        why = None
        if still_s < MARK_MIN_S:
            why = "you moved during the mark: stand still until the phone buzzes"
        elif len(heard) < 2:
            why = f"only {len(heard)} board(s) heard this phone: keep this page open and the screen on"
        if why:
            self.last_mark.pop(phone, None)
            self.mark_results[phone] = {"t": now, "ok": False, "x": x, "y": y, "why": why}
            self._note(f"mark at ({x / FT:.1f}, {y / FT:.1f}) ft not saved: {why}")
            return
        block = f"mark{int(cap['t0'] * 1000)}"
        errs = [round(e, 3) for e in rmap.window_errors(self.map, kept, x, y)] if self.map else []
        err = sorted(errs)[len(errs) // 2] if errs else None
        headings = [r[2] for r in cap.get("motion") or [] if r[2] is not None]
        compass = None
        if headings:
            c = sum(math.cos(math.radians(h)) for h in headings)
            s = sum(math.sin(math.radians(h)) for h in headings)
            compass = round(math.degrees(math.atan2(s, c)) % 360, 1)
        common = {"phone": phone, "x": x, "y": y, "orientation": cap["orientation"], "duration": round(still_s, 2),
                  "per_node": per, "warnings": cap["warnings"], "t": cap["t0"], "block": block}
        # "calibrated": the map already had spots, so errors_m measures calibration, not the bare path-loss model.
        self.calib["tests"].append(dict(common, kind="mark", errors_m=errs, calibrated=bool(self.calib["spots"])))
        # One group per phone and grip: its marks share one offset in the map fit (radiomap._fit).
        # compass: the phone's mean heading (deg) during the mark; facing derives from it (_refit_motion).
        self.calib["spots"].append(dict(common, kind="mark", samples=kept, compass=compass, facing=None,
                                        group=f"mark:{phone}:{cap['orientation']}"))
        self.calib["df_walk"].extend(walkcal.df_walk({"marks": [[0.0, x, y], [cap["duration"], x, y]],
                                                      "links": cap.get("links"), "t0": cap["t0"]}))
        leg = cap.get("leg")
        if leg:
            self.calib["legs"] = (self.calib["legs"] + [leg])[-LEGS_KEEP:]
            self.calib["spots"].extend(walkcal.leg_spots(leg))
        self._refit_motion(self.calib)
        self._save_calib()
        self._rebuild()
        self.last_mark[phone] = (now, x, y)
        self.live.anchor(phone, x, y, now)
        self._write_rec({"rt": round(now, 4), "anchor": {"m": phone, "x": x, "y": y}})
        self.mark_results[phone] = {"t": now, "ok": True, "x": x, "y": y, "err_m": err, "block": block,
                                    "calibrated": self.calib["tests"][-1]["calibrated"],
                                    "learned": bool(leg) and walkcal.leg_fit(leg) is not None}
        off = f", was {err / FT:.1f} ft off" if err is not None else ""
        self._note(f"mark saved at ({x / FT:.1f}, {y / FT:.1f}) ft{off}")

    def undo_mark(self, phone: str):
        """Forget this phone's latest mark (a mistaken tap): its spot, its check and the leg that ended there."""
        last = self.mark_results.get(phone)
        if not last or not last.get("ok"):
            return 409, "no mark to undo"
        block = last["block"]
        self.calib["spots"] = [s for s in self.calib["spots"] if s.get("block") != block]
        self.calib["tests"] = [t for t in self.calib["tests"] if t.get("block") != block]
        legs = self.calib["legs"]
        if legs and legs[-1]["phone"] == phone and (legs[-1]["x1"], legs[-1]["y1"]) == (last["x"], last["y"]):
            gone = walkcal.leg_id(legs.pop())
            self.calib["spots"] = [s for s in self.calib["spots"] if s.get("group") != gone]
        self._refit_motion(self.calib)
        self._save_calib()
        self._rebuild()
        self.last_mark.pop(phone, None)
        self.mark_results[phone] = dict(last, ok=False, undone=True, why="undone")
        self._note(f"mark at ({last['x'] / FT:.1f}, {last['y'] / FT:.1f}) ft undone")

    def clear_calib(self):
        # The empty-room baseline describes the room, not the walks, so it survives.
        self.last_mark.clear()
        self.mark_results.clear()
        self.calib.update(spots=[], tests=[], df_walk=[], heading={}, walks=[], legs=[])
        self.calib.pop("stride", None)
        self._save_calib()
        self._rebuild()

    def calib_summary(self) -> dict:
        calib = self.calib
        tests = calib.get("tests", [])
        spots = [{"i": i, "x": s["x"], "y": s["y"], "orientation": s.get("orientation", "hand"),
                  "phone": s.get("phone"), "frames": sum(v["n"] for v in s["per_node"].values()),
                  "nodes": len(s["per_node"]), "warn": s.get("warnings", []), "kind": s.get("kind", "calibrate"),
                  "group": s.get("group")} for i, s in enumerate(calib["spots"])]
        return {
            "name": calib["name"],
            "spots": spots,
            "tests": [{"x": t["x"], "y": t["y"], "errors_m": t.get("errors_m", [])} for t in tests[-30:]],
            "test_summary": rmap.err_summary([e for t in tests for e in t.get("errors_m", [])]) or {"windows": 0},
            "evaluation": self.evaluation,
            "map": self.map.info() if self.map else None,
            "df": {"empty": bool(calib.get("df_base")), "links": len(calib.get("df_base") or {}),
                   "walk_windows": len(calib.get("df_walk") or []),
                   "heading": {m: round(math.degrees(h[0]), 1) for m, h in calib.get("heading", {}).items()},
                   "stride": calib.get("stride")},
        }

    def _write_rec(self, obj, compact=True):
        if not self.rec_file:
            return
        try:
            self.rec_file.write(json.dumps(obj, separators=(",", ":") if compact else None) + "\n")
        except OSError as exc:
            print(f"recording stopped: {exc}", flush=True)
            self.rec_file = None

    def set_recording(self, on: bool):
        with self.lock:
            if on and not self.rec_file:
                self.rec_path = DATA / "sessions" / time.strftime("session_%Y%m%d_%H%M%S.jsonl")
                self.rec_path.parent.mkdir(parents=True, exist_ok=True)
                self.rec_file = open(self.rec_path, "a", encoding="utf-8")
                self.rec_count = 0
                self._write_rec({"type": "header", "started": time.time(), "layout": self.layout,
                                 "calibration": self.calib["name"]}, compact=False)
                self._write_rec({"rt": time.time(), "meta": self._meta()})
            elif not on and self.rec_file:
                self.rec_file.close()
                self.rec_file = None

    def _meta(self) -> dict:
        return {"laptop_ip": self.live.laptop_ip, "laptop_macs": sorted(self.live.laptop_macs)}

    def record(self, rt, src, pkt):
        if not self.rec_file:
            return
        self._write_rec({"rt": round(rt, 4), "src": src[0], "p": pkt})
        self.rec_count += 1
        if self.rec_count % 50 == 0 and self.rec_file:
            self.rec_file.flush()

    def start_replay(self, fname: str, speed: float, start: float = 0.0):
        self.stop_replay()
        path = DATA / "sessions" / fname
        if not SAFE_NAME.match(fname) or not path.exists():
            raise ValueError("no such session")
        header, lines = load_session(path)
        if not lines:
            raise ValueError("session has no packets")
        self.replay = Replay(fname, header, lines, self.lock, self.ctx, self.boards, speed, start)
        self.replay.start()

    def stop_replay(self):
        if self.replay:
            self.replay.stop()
            self.replay = None

    def open_udp(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        sock.bind(("0.0.0.0", self.args.udp_port))
        sock.settimeout(0.5)
        self.sock = sock

    def udp_loop(self):
        while True:
            try:
                data, src = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                # Windows raises WSAECONNRESET here after an ICMP port-unreachable from an earlier send.
                continue
            rt = time.time()
            try:
                pkt = json.loads(data.decode("utf-8", "replace"))
            except ValueError:
                with self.lock:
                    self.live.bad_packets += 1
                continue
            with self.lock:
                # A crowd button sending to the board port drives the decoys; it is not a board.
                if isinstance(pkt, dict) and self.decoys.handle_packet(pkt):
                    continue
                self.known_nodes.add(src[0])
                self.live.ingest(pkt, rt, src)
                self.record(rt, src, pkt)

    def demo_prefix(self) -> str:
        return self.net.get("ap_ip", "192.168.4.1").rsplit(".", 1)[0] + "."

    def laptop_ip(self):
        ap = self.net.get("ap_ip", "192.168.4.1")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect((ap, 9))
                ip = probe.getsockname()[0]
        except OSError:
            return None
        return ip if ip.startswith(self.demo_prefix()) else None

    def _sub_message(self) -> bytes:
        with self.lock:
            sense = self.sensing_on()
            peers = sorted({n.ip for n in self.live.nodes.values() if n.ip and n.ip.count(".") == 3})[:4]
            air = sorted({n.ifm for n in self.live.nodes.values() if n.ifm})[:4]
            csi = self.settings["csi_motion"] or sense
        # Boards ping peers and report link CSI only while sensing (firmware main.c apply_subscription, ping_task).
        text = f"LUNA_SUB v1 csi={int(bool(csi))} sense={int(sense)}"
        if sense:
            text += f" peers={','.join(peers)} lk={','.join(air)}"
        return text.encode()

    def subscribe_loop(self):
        """Broadcast finds boards on the demo subnet, unicast keeps known ones subscribed. Boards reply to this
        socket's port, so replies pass the firewall as return traffic. --targets (simulator) replaces both."""
        port = int(self.net.get("node_port", 5006))
        broadcast = self.demo_prefix() + "255"
        macs_due = 0.0
        while True:
            msg = self._sub_message()
            ip = self.laptop_ip()
            # Re-read now and then: Windows can give the Wi-Fi card a different random MAC on each network.
            macs = own_macs() if time.monotonic() >= macs_due else set()
            if macs:
                macs_due = time.monotonic() + 10.0
            with self.lock:
                if ip != self.live.laptop_ip or not macs <= self.live.laptop_macs:
                    if ip != self.live.laptop_ip:
                        self._note(f"laptop on demo Wi-Fi as {ip}" if ip else "laptop is NOT on the demo Wi-Fi")
                    self.live.laptop_ip = ip
                    self.live.laptop_macs |= macs
                    self._write_rec({"rt": time.time(), "meta": self._meta()})
                dests = set(self.targets) if self.targets else {(addr, port) for addr in self.known_nodes}
            if ip and not self.targets:
                dests.add((broadcast, port))
            for dest in dests:
                try:
                    self.sock.sendto(msg, dest)
                except OSError:
                    pass
            time.sleep(1.0)

    def _links_drifted(self) -> bool:
        new, old = self.live.links_dict(), self.map_links
        return new.keys() != old.keys() or any(abs(new[k] - old[k]) > 2.0 for k in new)

    def tick_loop(self):
        last_check = time.time()
        due = time.monotonic()
        while True:
            now = time.time()
            try:
                with self.lock:
                    if now - last_check > LINK_CHECK_S:
                        last_check = now
                        if self._links_drifted():
                            self._rebuild()
                    self.live.tick(now)
                    self._tick_decoys(now)
            except Exception:
                traceback.print_exc()
                with self.lock:
                    self.live.capture = None
                    self._note("internal error in tick (see server console)")
            due += TICK_S
            delay = due - time.monotonic()
            if delay < -1.0:
                due = time.monotonic()
            elif delay > 0:
                time.sleep(delay)

    def _tracked_center(self):
        """Where the real person is (layout frame): a live phone's estimate, else the phone-free body, else None."""
        for ph in self.live.phones.values():
            if ph.est and ph.est["kind"] != Status.STALE.value:
                return ph.est["x"], ph.est["y"]
        body = self.live.body
        return (body["x"], body["y"]) if body else None

    def _tick_decoys(self, now):
        """Advance the decoys inside the room, gathered around the tracked person. Under self.lock (tick_loop)."""
        x0, y0, x1, y1 = _bbox(self.layout["polygon"])
        self._decoy_origin = (x0, y0)
        c = self._tracked_center()
        center = None if c is None else (c[0] - x0, c[1] - y0)   # the crowd works in a 0..w / 0..d frame
        boards = {b: (p["x"] - x0, p["y"] - y0) for b, p in self.layout["receivers"].items()}
        before = self.decoys.members()
        self.decoys.tick(now, self.decoy_room or (max(x1 - x0, 0.5), max(y1 - y0, 0.5)), center=center,
                         boards=boards)
        after = self.decoys.members()
        # Same event lines tracker.Tracker writes for real phones, so the event log looks the same.
        for mac in after.keys() - before.keys():
            self.live.events.append([now, f"device joined {fmt_mac(mac)} {after[mac]}"])
            self.live.events.append([now, f"tracking {fmt_mac(mac)}"])
        for mac in before.keys() - after.keys():
            self.live.events.append([now, f"device left {fmt_mac(mac)}"])

    def _decoy_snapshot(self):
        """Decoys moved from the crowd's frame into the layout frame (app.js mergeDecoys shows them as phones)."""
        ox, oy = self._decoy_origin
        return [dict(d, x=round(d["x"] + ox, 2), y=round(d["y"] + oy, 2),
                     trail=[[p[0], round(p[1] + ox, 3), round(p[2] + oy, 3)] for p in d["trail"]])
                for d in self.decoys.snapshot()]

    def _jam(self, snap: dict) -> dict:
        """While the crowd is on, push real people's displayed dots and trail (from when it came on) around with
        crowd.jam_offset. Copies what it changes: the tracker's own est / trail, Phone.raw and accuracy never move."""
        since = self.decoys.on_since
        if since is None:
            return snap
        x0, y0, x1, y1 = _bbox(self.layout["polygon"])

        def moved(mac, t, x, y):
            dx, dy = jam_offset(mac, t)
            return (round(min(max(x + dx, x0 + 0.15), x1 - 0.15), 3),
                    round(min(max(y + dy, y0 + 0.15), y1 - 0.15), 3))

        def trail(mac, points):
            return [p if p[0] < since else [p[0], *moved(mac, p[0], p[1], p[2]), *p[3:]] for p in points]

        phones = []
        for ph in snap["phones"]:
            est = ph.get("est")
            if est and est.get("kind") != Status.STALE.value:
                x, y = moved(ph["mac"], snap["now"], est["x"], est["y"])
                ph = dict(ph, est=dict(est, x=x, y=y), trail=trail(ph["mac"], ph["trail"]))
            phones.append(ph)
        snap["phones"] = phones
        body = snap.get("body")
        if body:
            x, y = moved("body", snap["now"], body["x"], body["y"])
            snap["body"] = dict(body, x=x, y=y, trail=trail("body", body.get("trail", [])))
        return snap

    def set_decoys(self, on):
        with self.lock:
            self.decoys.set(on)
            return self.decoys.state.value == "on"

    def toggle_decoys(self):
        with self.lock:
            self.decoys.toggle()
            return self.decoys.state.value == "on"

    def decoy_udp_loop(self):
        """The Uno Q white button (tools/unoq_button_adb.py) sends crowd packets here (crowd.Crowd.handle_packet)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", self.args.decoy_udp_port))
        except OSError as exc:
            print(f"decoy button UDP port {self.args.decoy_udp_port} unavailable ({exc}); web toggle still works",
                  flush=True)
            return
        sock.settimeout(0.5)
        while True:
            try:
                data, _ = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                p = json.loads(data.decode())
            except (ValueError, UnicodeDecodeError):
                continue
            with self.lock:
                self.decoys.handle_packet(p)

    def snapshot(self, view: str) -> dict:
        with self.lock:
            recording = {"on": bool(self.rec_file),
                         "file": self.rec_path.name if self.rec_path and self.rec_file else None,
                         "packets": self.rec_count}
            extra = {"view": view, "layout": self.layout, "calib": self.calib_summary(), "recording": recording,
                     "phone_cal": {"pin": None, "port": self.phone_port, "https": self.phone_https},
                     "settings": self.settings, "settings_info": cfg.INFO,
                     "settings_defaults": cfg.DEFAULTS, "lab": self.lab,
                     "furniture_prior": bool(self.blocked), "ssid": self.net.get("ssid"), "wall": time.time()}
            extra["decoys"] = self._decoy_snapshot()
            replay = self.replay
            if replay:
                extra["replay"] = replay.info()
                if view == "replay":
                    return replay.tracker.snapshot(extra)
            return self._jam(self.live.snapshot(extra))

    def snapshot_json(self, view: str) -> str:
        """Shared by all SSE clients so several open tabs cost one snapshot per tick."""
        now = time.monotonic()
        cached = self._snap_cache.get(view)
        if cached and now - cached[0] < SSE_S * 0.8:
            return cached[1]
        text = json.dumps(self.snapshot(view), separators=(",", ":"))
        self._snap_cache[view] = (now, text)
        return text

    def phone_for_ip(self, ip: str, override=None):
        # Requests from localhost may name the phone (simulator and local testing).
        if ip == "127.0.0.1" and override:
            return str(override).lower().replace(":", "")
        return next((m for m, s in self.live.stations.items() if s.get("ip") == ip), None)

    def mark_plan(self):
        sig = json.dumps([self.layout["polygon"], self.scan_objects])
        if self._plan_cache[0] != sig:
            self._plan_cache = (sig, planner.mark_plan(self.layout["polygon"], self.scan_objects))
        return self._plan_cache[1]

    def _plan_state(self, near=None) -> dict:
        """phone_state's "plan" ([[x, y, done]...] in walking order) and "next" ([x, y] or None)."""
        plan = self.mark_plan()
        standing = [s for s in self.calib["spots"] if s.get("still") is not False]
        done = planner.plan_done(plan, [(s["x"], s["y"]) for s in standing])
        marks = [s for s in standing if s.get("kind") == "mark"]
        last = max(marks, key=lambda s: s.get("t", 0)) if marks else None
        i = planner.plan_next(plan, done, (last["x"], last["y"]) if last else None, near)
        return {"plan": [[x, y, d] for (x, y), d in zip(plan, done)], "next": None if i is None else plan[i]}

    def _mark_rows(self):
        """[[x, y, error m before the map learned that spot, or None]] for each marked spot."""
        errs = {t["block"]: t.get("errors_m") for t in self.calib["tests"] if t.get("block") and t.get("calibrated")}
        out = []
        for s in self.calib["spots"]:
            if s.get("kind") != "mark":
                continue
            e = errs.get(s.get("block"))
            out.append([s["x"], s["y"], round(sorted(e)[len(e) // 2], 3) if e else None])
        return out

    def phone_state(self, mac) -> dict:
        layout = self.layout
        route, checks = self.route()
        out = {"polygon": layout["polygon"],
               "boards": [{"label": self.live.label(k), "x": v["x"], "y": v["y"]}
                          for k, v in layout["receivers"].items()],
               "objects": [{"label": o["label"], "footprint": o["footprint"]} for o in self.scan_objects],
               "route": route, "checks": checks, "walks": len(self.calib.get("walks", [])),
               "route_reversed": self.route_reversed(),
               "tests": [{"x": t["x"], "y": t["y"], "errors_m": t.get("errors_m", [])}
                         for t in self.calib.get("tests", [])[-20:]],
               "spots": [[s["x"], s["y"], s.get("kind", "calibrate")] for s in self.calib["spots"]],
               "me": None, "capture": None, "https": self.phone_https}
        marks = self._mark_rows()
        recent = [e for *_, e in marks[-8:] if e is not None]
        out.update(marks=marks, typical_m=sorted(recent)[len(recent) // 2] if len(recent) >= 2 else None,
                   stride=self.calib.get("stride"), result=None, compass=False)
        if not mac:
            out.update(self._plan_state())
            return out
        ph = self.live.phones.get(mac)
        report = self.live.fresh_report(mac)
        now = time.time()
        out["me"] = {"mac": mac, "name": self.live.stations.get(mac, {}).get("nm", ""),
                     "est": ph.est if ph else None, "motion": report["mv"] if report else None}
        est = ph.est if ph else None
        out.update(self._plan_state((est["x"], est["y"]) if est else None))
        out["result"] = self.mark_results.get(mac)
        out["compass"] = mac in self.calib.get("heading", {})
        cap = self.live.capture
        if cap and cap["phone"] == mac:
            out["capture"] = {"kind": cap["kind"], "phase": capture_phase(cap, now),
                              "elapsed": round(now - cap["t0"], 1), "duration": cap["duration"],
                              "x": cap["x"], "y": cap["y"],
                              "marks": len(cap["marks"]), "frames": sum(s[2] for s in cap["samples"])}
        return out


APP: App = None  # type: ignore


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, ctype, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, "application/json", json.dumps(obj).encode())

    def _reply(self, result):
        """Route handlers return None for success or (http status, error message)."""
        if result is None:
            return self._json({"ok": True})
        return self._json({"error": result[1]}, result[0])

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        if length > 1 << 20:
            raise ValueError("body too large")
        body = json.loads(self.rfile.read(length).decode())
        if not isinstance(body, dict):
            raise ValueError("body must be an object")
        return body

    def _file(self, path: Path, ctype):
        try:
            data = path.read_bytes()
        except OSError:
            return self._json({"error": "not found"}, 404)
        textual = ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml")
        self._send(200, ctype + "; charset=utf-8" if textual else ctype, data)

    def _query(self, url) -> dict:
        return {k: v[0] for k, v in parse_qs(url.query).items()}

    def do_GET(self):
        url = urlparse(self.path)
        view = "replay" if self._query(url).get("view") == "replay" else "live"
        path = url.path
        if path in ("/", "/index.html"):
            return self._file(STATIC / "index.html", "text/html")
        if path.startswith("/static/"):
            parts = path[len("/static/"):].split("/")
            if not parts or not all(SAFE_NAME.match(p) and p not in (".", "..") for p in parts):
                return self._json({"error": "bad path"}, 404)
            return self._file(STATIC.joinpath(*parts), MIME.get(parts[-1].rsplit(".", 1)[-1], "text/plain"))
        if path == "/scan.glb":
            return self._file(DATA / "scan" / "scan.glb", "model/gltf-binary")
        if path == "/api/scan":
            return self._json(read_json(DATA / "scan" / "room.json", None))
        if path == "/api/state":
            return self._json(APP.snapshot(view))
        if path == "/api/crowd":   # toggles the decoys; ?on=1 / ?on=0 sets them
            on = self._query(url).get("on")
            state = APP.toggle_decoys() if on is None else APP.set_decoys(on in ("1", "true", "on"))
            return self._json({"on": state})
        if path == "/api/plan":
            return self._json(self._plan())
        if path == "/api/events":
            return self._sse(view)
        if path == "/api/sessions":
            return self._json(self._sessions())
        if path == "/api/calibration/export":
            with APP.lock:
                return self._json(APP.calib)
        return self._json({"error": "not found"}, 404)

    @staticmethod
    def _plan() -> dict:
        with APP.lock:
            poly, objects = APP.layout["polygon"], list(APP.scan_objects)
            placed = [dict(v) for v in APP.layout.get("receivers", {}).values()]
        plan = planner.suggest_boards(poly, objects, max(3, len(APP.boards))) or {"spots": []}
        plan["spots"] = planner.assign_boards(plan["spots"], APP.boards)
        plan["current"] = planner.score(placed, poly, objects) if len(placed) >= 3 else None
        plan["furniture"] = bool(objects)
        return plan

    @staticmethod
    def _sessions() -> list:
        out = []
        for path in sorted((DATA / "sessions").glob("*.jsonl"), reverse=True):
            try:
                st = path.stat()
            except OSError:
                continue
            out.append({"file": path.name, "bytes": st.st_size, "mtime": st.st_mtime})
        return out

    def _sse(self, view):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(b"retry: 1000\n\n")
            while True:
                self.wfile.write(f"data: {APP.snapshot_json(view)}\n\n".encode())
                self.wfile.flush()
                time.sleep(SSE_S)
        except OSError:
            return

    def _scan_import(self, url):
        """Raw .glb/.zip body (or none, to re-turn the stored scan); the heavy mesh work runs outside APP.lock."""
        q = self._query(url)
        # Not a CORS-safelisted type, so another web page can't post a file here without a preflight we never answer.
        if self.headers.get("Content-Type") != "application/octet-stream":
            return self._json({"error": "send the file as application/octet-stream"}, 415)
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > SCAN_MAX_BYTES:
            return self._json({"error": "scan file too large"}, 413)
        old = read_json(DATA / "scan" / "room.json", {}) or {}
        try:
            if length > 0:
                raw, source, rotate = self.rfile.read(length), Path(q.get("name", "scan.glb")).name[:80], 0
            else:
                raw, source = (DATA / "scan" / "scan.glb").read_bytes(), old.get("source", "scan.glb")
                rotate = (int(old.get("rotate", 0)) + 1) % 4
            room = import_scan.import_room(raw, source, rotate, DATA)
        except FileNotFoundError:
            return self._json({"error": "no room scan imported yet"}, 404)
        except (ValueError, KeyError, IndexError, zipfile.BadZipFile) as exc:
            return self._json({"error": f"couldn't read that scan: {exc}"}, 400)
        with APP.lock:
            layout = dict(APP.layout, polygon=room["polygon"])
            # Calibration is kept per layout name, so a different scan gets a fresh set and an old room's comes back.
            if source != old.get("source"):
                layout["name"] = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(source).stem)[:60] or "room"
            _post_layout(APP, {"layout": layout})
        w, d = room["polygon"][2]
        outside = [k for k, r in layout["receivers"].items() if not (0 <= r["x"] <= w and 0 <= r["y"] <= d)]
        return self._json({"ok": True, "w": w, "d": d, "objects": len(room["objects"]), "outside": outside})

    def do_POST(self):
        url = urlparse(self.path)
        path = url.path
        if path == "/api/scan/import":
            return self._scan_import(url)
        try:
            body = self._body()
        except ValueError:
            return self._json({"error": "bad json"}, 400)
        route = DASHBOARD_POST.get(path)
        if route is None:
            return self._json({"error": "not found"}, 404)
        try:
            with APP.lock:
                return self._reply(route(APP, body))
        except (KeyError, BadRequest, ValueError, TypeError, IndexError, AttributeError) as exc:
            return self._json({"error": f"bad request: {exc}"}, 400)
        except OSError as exc:
            return self._json({"error": f"file error: {exc}"}, 500)


def _post_layout(a: App, b: dict):
    a.layout = parse_layout(b["layout"])
    write_json(DATA / "layouts" / "current.json", a.layout)
    if a.calib["name"] != a.calib_name():
        a.calib = a._load_calib(a.calib_name())
    a._rebuild()


def _post_capture(a: App, b: dict):
    phone = str(b["phone"]).lower().replace(":", "")
    require(re.fullmatch(r"[0-9a-f]{12}", phone), "phone")
    if a.live.capture:
        return BUSY
    a.start_spot(b, phone, 10, max(0.0, min(30.0, finite(b.get("delay", 0)))))


def _post_spot_delete(a: App, b: dict):
    i = int(b["i"])
    require(0 <= i < len(a.calib["spots"]), "no such spot")
    del a.calib["spots"][i]
    a._save_calib()
    a._rebuild()


def _post_group_delete(a: App, b: dict):
    group = str(b["group"])
    a.calib["spots"] = [s for s in a.calib["spots"] if s.get("group") != group]
    a.calib["walks"] = [w for w in a.calib["walks"] if w.get("id") != group]
    a._save_calib()
    a._rebuild()


def _post_tests_clear(a: App, b: dict):
    a.calib["tests"] = []
    a._save_calib()


def _post_replay_control(a: App, b: dict):
    if a.replay:
        if "paused" in b:
            a.replay.set_paused(bool(b["paused"]))
        if "speed" in b:
            a.replay.set_speed(finite(b["speed"]))


def _post_session_delete(a: App, b: dict):
    name = str(b["file"])
    require(SAFE_NAME.match(name), "file")
    if a.rec_path and a.rec_path.name == name and a.rec_file:
        return 409, "stop recording first"
    if a.replay and a.replay.file == name:
        a.stop_replay()
    try:
        (DATA / "sessions" / name).unlink()
    except FileNotFoundError:
        return 404, "no such session"


# Handlers run under APP.lock; each returns None (ok) or (status, error).
DASHBOARD_POST = {
    "/api/layout": _post_layout,
    "/api/capture": _post_capture,
    "/api/capture/cancel": lambda a, b: a.live.cancel_capture(),
    "/api/spot/delete": _post_spot_delete,
    "/api/spot/delete_group": _post_group_delete,
    "/api/tests/clear": _post_tests_clear,
    "/api/calib/clear": lambda a, b: a.clear_calib(),
    "/api/empty/start": lambda a, b: BUSY if a.live.capture else a.start_empty(""),
    "/api/record": lambda a, b: a.set_recording(bool(b.get("on"))),
    "/api/settings": lambda a, b: a.set_setting(str(b["key"]), bool(b["on"])),
    "/api/lab/run": lambda a, b: a.start_lab(),
    "/api/replay/start": lambda a, b: a.start_replay(str(b["file"]), finite(b.get("speed", 1.0)),
                                                     finite(b.get("start", 0))),
    "/api/replay/control": _post_replay_control,
    "/api/replay/stop": lambda a, b: a.stop_replay(),
    "/api/session/delete": _post_session_delete,
}


class QuietServer(ThreadingHTTPServer):
    # Phones probing http on the https port, or dropping TLS mid-handshake, are routine; skip the tracebacks.
    def handle_error(self, request, client_address):
        pass


class PhoneHandler(Handler):
    """Phone calibration page, served on all interfaces so phones on the demo Wi-Fi reach it.

    Only the calibration flow is exposed, and only to callers on the demo subnet (the WPA2 network is the gate; there
    is no PIN). The phone is identified by its IP in the AP's station list.
    """

    def _allowed(self) -> bool:
        ip = self.client_address[0]
        return ip.startswith(APP.demo_prefix()) or ip == "127.0.0.1"

    def do_GET(self):
        if not self._allowed():
            return self._json({"error": "forbidden"}, 403)
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self._file(STATIC / "phone.html", "text/html")
        if url.path == "/phone.js":
            return self._file(STATIC / "phone.js", "application/javascript")
        if url.path == "/ping":
            return self._send(204, "text/plain", b"")
        if url.path == "/api/state":
            q = self._query(url)
            ip = self.client_address[0]
            with APP.lock:
                return self._json(APP.phone_state(APP.phone_for_ip(ip, q.get("mac"))))
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._allowed():
            return self._json({"error": "forbidden"}, 403)
        path = urlparse(self.path).path
        try:
            body = self._body()
        except ValueError:
            return self._json({"error": "bad json"}, 400)
        route = PHONE_POST.get(path)
        try:
            with APP.lock:
                ip = self.client_address[0]
                mac = APP.phone_for_ip(ip, body.get("mac"))
                if not mac:
                    return self._json({"error": "this phone is not on the demo Wi-Fi "
                                                "(or the AP has not listed it yet)"}, 409)
                if route is None:
                    return self._json({"error": "not found"}, 404)
                cap = APP.live.capture
                if path in ("/api/mark", "/api/capture", "/api/walk/start", "/api/empty/start") and cap:
                    return self._reply(BUSY)
                mine = cap is not None and cap["phone"] == mac
                return self._reply(route(APP, body, mac, mine))
        except (KeyError, BadRequest, ValueError, TypeError) as exc:
            return self._json({"error": f"bad request: {exc}"}, 400)


def _phone_mark(a: App, b: dict, mac: str, mine: bool):
    x, y = finite(b["x"]), finite(b["y"])
    require(rmap.point_in_poly(x, y, a.layout["polygon"]), "tap inside the room")
    if not (mine and a.live.mark(x, y, time.time(), bool(b.get("detour")))):
        return 409, "no walk running"


def _phone_motion(a: App, b: dict, mac: str, mine: bool):
    mv, steps = int(b.get("mv", 0)), int(b.get("st", 0))
    hd = b.get("hd")
    hd = float(hd) % 360 if isinstance(hd, (int, float)) and math.isfinite(hd) else None
    a.live.set_motion(mac, mv, steps, time.time(), hd)
    line = {"m": mac, "mv": mv, "st": steps, "hd": hd}
    # ac (bounce rms) is recorded only, for retuning phone.js thresholds against real walks.
    if isinstance(b.get("ac"), (int, float)) and math.isfinite(b["ac"]):
        line["ac"] = round(float(b["ac"]), 2)
    a._write_rec({"rt": round(time.time(), 4), "motion": line})


# (app, body, phone mac, whether the running capture is this phone's) -> None (ok) or (status, error).
PHONE_POST = {
    "/api/mark": lambda a, b, mac, mine: a.start_mark(mac, b),
    "/api/mark/undo": lambda a, b, mac, mine: BUSY if a.live.capture else a.undo_mark(mac),
    "/api/walk/start": lambda a, b, mac, mine: a.live.start_walk(mac, orientation(b), time.time()),
    "/api/capture": lambda a, b, mac, mine: a.start_spot(b, mac, 12, 3.0),
    "/api/walk/mark": _phone_mark,
    "/api/motion": _phone_motion,
    "/api/empty/start": lambda a, b, mac, mine: a.start_empty(mac),
    "/api/calib/clear": lambda a, b, mac, mine: (409, "a capture is running") if a.live.capture else a.clear_calib(),
    "/api/walk/stop": lambda a, b, mac, mine: a.live.stop_walk() if mine else None,
    "/api/cancel": lambda a, b, mac, mine: a.live.cancel_capture() if mine else None,
}


def own_macs() -> set:
    """This laptop's network-card MACs as 12 lowercase hex digits (empty if the OS won't say)."""
    text = ""
    try:
        if sys.platform == "win32":
            text = subprocess.run(["getmac", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=5,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        elif sys.platform == "darwin":
            text = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
        else:
            text = "\n".join(p.read_text() for p in Path("/sys/class/net").glob("*/address"))
    except (OSError, subprocess.SubprocessError):
        return set()
    found = {re.sub(r"[^0-9a-f]", "", m.lower()) for m in re.findall(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", text)}
    return found - {"000000000000", "ffffffffffff"}


def _start_phone_server(port: int):
    try:
        server = QuietServer(("0.0.0.0", port), PhoneHandler)
    except OSError as exc:
        print(f"WARNING: phone calibration page off: cannot open port {port} ({exc})", flush=True)
        return
    server.daemon_threads = True
    APP.phone_port = port
    # https lets phones use motion sensors (browsers require a secure context). The self-signed cert comes from
    # tools/gen_secrets.py; phones show a one-time warning for it.
    cert, key = ROOT / "config" / "certs" / "servercert.pem", ROOT / "config" / "certs" / "prvtkey.pem"
    if cert.exists() and key.exists():
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        server.socket = tls.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
        APP.phone_https = True
    threading.Thread(target=server.serve_forever, daemon=True).start()


def main():
    global APP
    net = read_json(ROOT / "config" / "network.json", {})
    parser = argparse.ArgumentParser(description="ActiasLuna laptop server: telemetry, tracking, recording, dashboard.")
    parser.add_argument("--port", type=int, default=int(net.get("dashboard_port", 8080)))
    parser.add_argument("--udp-port", type=int, default=int(net.get("server_udp_port", 5007)))
    parser.add_argument("--decoy-udp-port", type=int, default=int(net.get("decoy_udp_port", 5005)),
                        help="UDP port the Uno Q white button toggles the simulated decoys on (0 = off)")
    parser.add_argument("--targets", default="", help="board addresses host:port,... (simulator)")
    parser.add_argument("--no-record", action="store_true")
    parser.add_argument("--phone-port", type=int, default=int(net.get("phone_port", 8081)),
                        help="port for the phone calibration page on the demo Wi-Fi (0 = off)")
    args = parser.parse_args()
    for sub in ("sessions", "calibration", "layouts"):
        (DATA / sub).mkdir(parents=True, exist_ok=True)
    APP = App(args, net)
    try:
        APP.open_udp()
    except OSError as exc:
        sys.exit(f"cannot open UDP port {args.udp_port} ({exc}). Is another ActiasLuna server running?")
    try:
        dashboard = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        sys.exit(f"cannot open dashboard port {args.port} ({exc}). Is another ActiasLuna server running?")
    dashboard.daemon_threads = True
    if args.phone_port:
        _start_phone_server(args.phone_port)
    if not args.no_record:
        APP.set_recording(True)
    loops = [APP.udp_loop, APP.subscribe_loop, APP.tick_loop]
    if args.decoy_udp_port:
        loops.append(APP.decoy_udp_loop)
    for loop in loops:
        threading.Thread(target=loop, daemon=True).start()
    rec = APP.rec_path.name if APP.rec_file else "off"
    print(f"ActiasLuna dashboard: http://127.0.0.1:{args.port}/  (udp {args.udp_port}, recording={rec})", flush=True)
    if not args.targets and not APP.laptop_ip():
        print(f"Note: the laptop isn't on the demo Wi-Fi '{net.get('ssid', '?')}' yet. Map, layout and 3D view work "
              "now; boards and phones appear once it joins.", flush=True)
    try:
        dashboard.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        APP.set_recording(False)


if __name__ == "__main__":
    main()
