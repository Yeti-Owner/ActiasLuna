"""Moth cloud: room activity history for one monitored room. Runs on a Vultr VM; standard library only (Python 3.9+).

The laptop server (server/cloud_sync.py inside luna_server) POSTs what it sees: occupancy counts, the live X/Y of each
person in the room, and events (entered, stood by the Amazon Alexa, moved on, left). The Chert FaceTime agent holds the
QUERY token, which can only call the read endpoints; each answers with JSON plus a plain-English `answer` a voice agent
can say out loud ("Someone entered 30 seconds ago, stood by the Amazon Alexa for 10 seconds, then wandered to the Smart
Thermostat, then left"). Points keep short IDs (A, B, C) in every field; "point_names" only changes how they are said.

PRIVACY: people arrive as per-run pseudonyms ("person-1", "body"); no MAC addresses, names or IPs are ever stored.
Simulated decoys never reach this service. Positions are deleted after --position-days, everything else after
--retention-days.

Endpoints (metres and epoch seconds on the wire)
  GET  /health                               no auth
  POST /ingest           Bearer INGEST       {"room", "run", "samples": [{t, in, nearby, zones}], "positions": [{t, pid, x, y}],
                                              "events": [{t, pid, kind, point?, dur?, x?, y?}], "points": {"A": [x, y]},
                                              "point_names": {"A": "Amazon Alexa"}}
  GET  /api/now          Bearer QUERY        ?room=&tz=                -> is anyone in the room right now?
  GET  /api/recent       Bearer QUERY        ?minutes=10               -> what happened in the last N minutes
  GET  /api/where        Bearer QUERY        ?                         -> where each person is right now
  GET  /api/events       Bearer QUERY        ?from=&to=                -> what happened in a range (ISO or epoch)
  GET  /api/positions    Bearer QUERY        ?from=&to=&pid=           -> raw position track
  GET  /api/summary      Bearer QUERY        ?date=YYYY-MM-DD          -> when were people in the room that day?
  GET  /api/occupancy    Bearer QUERY        ?from=&to=                -> occupancy intervals in a range
  GET  /api/runs         Bearer QUERY                                  -> run IDs, newest first

Every read takes ?run=: omitted = the latest run only (each luna_server start is a new run, so old tests and sessions
stay out of the answers), run=<id> = that run, run=all = everything including untagged rows.

Run:  MOTH_INGEST_TOKEN=... MOTH_QUERY_TOKEN=... python3 moth_cloud.py --port 8000 --db /var/lib/moth/moth.db
"""

import argparse
import datetime
import hmac
import json
import math
import os
import re
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None

# Samples further apart than this mean the sensors weren't reporting (not "room empty"). The laptop's heartbeat
# (cloud_sync.SAMPLE_S) must stay well under it.
MAX_GAP_S = 90.0
MAX_BODY = 4 << 20
MAX_ITEMS = 20000           # per list per ingest request
MAX_COUNT = 500             # sanity cap on people per sample
MAX_COORD_M = 1000.0
WHERE_FRESH_S = 10.0        # a position older than this is not "right now"
LOOKBACK_S = 6 * 3600       # how far back to look for the start of a visit still running at the window's start
MAX_VISITS_SPOKEN = 5
MAX_STOPS_SPOKEN = 4        # a visit with more stops than this is summarised in the middle (_spoken_stops)
SPOKEN_TAIL_STOPS = 2
PRUNE_EVERY_S = 600         # a new run's predecessor-but-one loses its positions within this long
ROOM_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
ZONE_RE = re.compile(r"^[A-Za-z0-9_. -]{1,32}$")
PID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
RUN_RE = re.compile(r"^[A-Za-z0-9_.-]{0,64}$")
NAME_RE = re.compile(r"^[A-Za-z0-9 _.'&-]{1,40}$")
BOARD_KINDS = ("board_offline", "board_online")     # pid "board", point = the board's label (cloud_sync.Observer)
KINDS = ("enter", "leave", "arrive", "depart") + BOARD_KINDS


def _finite(v, lo=-MAX_COORD_M, hi=MAX_COORD_M):
    v = float(v)
    if not (math.isfinite(v) and lo <= v <= hi):
        raise ValueError("number out of range")
    return v


class Store:
    def __init__(self, path):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS samples (room TEXT NOT NULL, t REAL NOT NULL, "
                            "n_in INTEGER NOT NULL, n_near INTEGER NOT NULL, zones TEXT NOT NULL, "
                            "PRIMARY KEY (room, t))")
            self.db.execute("CREATE TABLE IF NOT EXISTS positions (room TEXT NOT NULL, t REAL NOT NULL, "
                            "pid TEXT NOT NULL, x REAL NOT NULL, y REAL NOT NULL, PRIMARY KEY (room, t, pid))")
            self.db.execute("CREATE TABLE IF NOT EXISTS events (room TEXT NOT NULL, t REAL NOT NULL, "
                            "pid TEXT NOT NULL, kind TEXT NOT NULL, point TEXT, dur REAL, x REAL, y REAL, "
                            "PRIMARY KEY (room, t, pid, kind))")
            self.db.execute("CREATE TABLE IF NOT EXISTS meta (room TEXT NOT NULL, k TEXT NOT NULL, "
                            "v TEXT NOT NULL, PRIMARY KEY (room, k))")
            # Rows from before run IDs (and from uploaders that send none) get run '', which the default
            # "latest run" view never picks.
            for table in ("samples", "positions", "events"):
                cols = [r[1] for r in self.db.execute(f"PRAGMA table_info({table})")]
                if "run" not in cols:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN run TEXT NOT NULL DEFAULT ''")
            self.db.commit()

    @staticmethod
    def _room(room):
        if not ROOM_RE.match(str(room)):
            raise ValueError("room must be 1-64 chars of letters, digits, _ . -")
        return str(room)

    @staticmethod
    def _items(items):
        if items is None:
            return []
        if not isinstance(items, list) or len(items) > MAX_ITEMS:
            raise ValueError(f"lists must hold at most {MAX_ITEMS} items")
        for s in items:
            if not isinstance(s, dict):
                raise ValueError("each item must be an object")
        return items

    @staticmethod
    def _run(s, default):
        run = str(s.get("run", default) or "")
        if not RUN_RE.match(run):
            raise ValueError("run must be up to 64 chars of letters, digits, _ . -")
        return run

    @staticmethod
    def _where_run(run):
        """SQL tail and args restricting to one run; run None means every run."""
        return ("", []) if run is None else (" AND run = ?", [run])

    @staticmethod
    def _t(s, now):
        t = float(s["t"])
        if not (1.5e9 < t < now + 3600):
            raise ValueError("time out of range")
        return t

    def add(self, room, samples, run=""):
        """Validate and store occupancy samples. Returns how many were stored. Raises ValueError on bad input."""
        room, now, rows = self._room(room), time.time(), []
        for s in self._items(samples):
            t = self._t(s, now)
            n_in, n_near = int(s.get("in", 0)), int(s.get("nearby", 0))
            if not (0 <= n_in <= MAX_COUNT and 0 <= n_near <= MAX_COUNT):
                raise ValueError("counts out of range")
            zones = {}
            for k, v in (s.get("zones") or {}).items():
                if ZONE_RE.match(str(k)) and int(v) > 0 and len(zones) < 16:
                    zones[str(k)] = min(int(v), MAX_COUNT)
            rows.append((room, t, n_in, n_near, json.dumps(zones, sort_keys=True), self._run(s, run)))
        with self.lock:
            self.db.executemany("INSERT OR REPLACE INTO samples (room, t, n_in, n_near, zones, run) "
                                "VALUES (?, ?, ?, ?, ?, ?)", rows)
            self.db.commit()
        return len(rows)

    def add_positions(self, room, positions, run=""):
        room, now, rows = self._room(room), time.time(), []
        for p in self._items(positions):
            pid = str(p.get("pid", ""))
            if not PID_RE.match(pid):
                raise ValueError("bad pid")
            rows.append((room, self._t(p, now), pid, _finite(p["x"]), _finite(p["y"]), self._run(p, run)))
        with self.lock:
            self.db.executemany("INSERT OR REPLACE INTO positions (room, t, pid, x, y, run) VALUES (?, ?, ?, ?, ?, ?)",
                                rows)
            self.db.commit()
        return len(rows)

    def add_events(self, room, events, run=""):
        room, now, rows = self._room(room), time.time(), []
        for e in self._items(events):
            pid, kind = str(e.get("pid", "")), str(e.get("kind", ""))
            if not PID_RE.match(pid) or kind not in KINDS:
                raise ValueError("bad pid or kind")
            point = e.get("point")
            if point is not None and not ZONE_RE.match(str(point)):
                raise ValueError("bad point")
            dur = None if e.get("dur") is None else _finite(e["dur"], 0, 86400 * 7)
            x = None if e.get("x") is None else _finite(e["x"])
            y = None if e.get("y") is None else _finite(e["y"])
            rows.append((room, self._t(e, now), pid, kind, None if point is None else str(point), dur, x, y,
                         self._run(e, run)))
        with self.lock:
            self.db.executemany("INSERT OR REPLACE INTO events (room, t, pid, kind, point, dur, x, y, run) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            self.db.commit()
        return len(rows)

    def set_points(self, room, points):
        room = self._room(room)
        if not isinstance(points, dict) or len(points) > 16:
            raise ValueError("points must be an object of at most 16 entries")
        clean = {}
        for k, v in points.items():
            if not ZONE_RE.match(str(k)) or not isinstance(v, (list, tuple)) or len(v) < 2:
                raise ValueError("bad point")
            clean[str(k)] = [_finite(v[0]), _finite(v[1])]
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, 'points', ?)", (room, json.dumps(clean)))
            self.db.commit()

    def points(self, room):
        with self.lock:
            row = self.db.execute("SELECT v FROM meta WHERE room = ? AND k = 'points'", (room,)).fetchone()
        return json.loads(row[0]) if row else {}

    def set_point_names(self, room, names):
        room = self._room(room)
        if not isinstance(names, dict) or len(names) > 16:
            raise ValueError("point_names must be an object of at most 16 entries")
        clean = {}
        for k, v in names.items():
            if not ZONE_RE.match(str(k)) or not NAME_RE.match(str(v)):
                raise ValueError("bad point name")
            clean[str(k)] = str(v).strip()
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, 'point_names', ?)", (room, json.dumps(clean)))
            self.db.commit()

    def point_names(self, room):
        with self.lock:
            row = self.db.execute("SELECT v FROM meta WHERE room = ? AND k = 'point_names'", (room,)).fetchone()
        return json.loads(row[0]) if row else {}

    def range(self, room, t0, t1, run=None):
        tail, extra = self._where_run(run)
        with self.lock:
            cur = self.db.execute("SELECT t, n_in, n_near, zones FROM samples WHERE room = ? AND t >= ? AND t < ?"
                                  + tail + " ORDER BY t", [room, t0, t1] + extra)
            return [(t, a, b, json.loads(z)) for t, a, b, z in cur.fetchall()]

    def events(self, room, t0, t1, run=None):
        tail, extra = self._where_run(run)
        with self.lock:
            cur = self.db.execute("SELECT t, pid, kind, point, dur, x, y FROM events WHERE room = ? AND t >= ? "
                                  "AND t < ?" + tail + " ORDER BY t, CASE kind WHEN 'enter' THEN 0 WHEN 'arrive' "
                                  "THEN 1 WHEN 'depart' THEN 2 ELSE 3 END", [room, t0, t1] + extra)
            keys = ("t", "pid", "kind", "point", "dur", "x", "y")
            return [dict(zip(keys, r)) for r in cur.fetchall()]

    def positions(self, room, t0, t1, pid=None, limit=5000, run=None):
        tail, extra = self._where_run(run)
        q = "SELECT t, pid, x, y FROM positions WHERE room = ? AND t >= ? AND t < ?" + tail
        args = [room, t0, t1] + extra
        if pid:
            q += " AND pid = ?"
            args.append(pid)
        q += " ORDER BY t DESC LIMIT ?"
        args.append(int(limit))
        with self.lock:
            rows = self.db.execute(q, args).fetchall()
        return [{"t": t, "pid": p, "x": x, "y": y} for t, p, x, y in reversed(rows)]

    def latest(self, room, run=None):
        tail, extra = self._where_run(run)
        with self.lock:
            row = self.db.execute("SELECT t, n_in, n_near, zones FROM samples WHERE room = ?" + tail +
                                  " ORDER BY t DESC LIMIT 1", [room] + extra).fetchone()
        return None if row is None else (row[0], row[1], row[2], json.loads(row[3]))

    def last_report(self, room, run=None):
        """Newest moment the laptop told us anything, or None."""
        tail, extra = self._where_run(run)
        with self.lock:
            a = self.db.execute("SELECT MAX(t) FROM samples WHERE room = ?" + tail, [room] + extra).fetchone()[0]
            b = self.db.execute("SELECT MAX(t) FROM positions WHERE room = ?" + tail, [room] + extra).fetchone()[0]
        vals = [v for v in (a, b) if v is not None]
        return max(vals) if vals else None

    def runs(self, room):
        """[(run, first t, last t)] newest first, run IDs only (never the '' of untagged rows)."""
        with self.lock:
            rows = self.db.execute("SELECT run, MIN(t), MAX(t) FROM (SELECT run, t FROM samples WHERE room = ? "
                                   "UNION ALL SELECT run, t FROM events WHERE room = ?) WHERE run != '' "
                                   "GROUP BY run ORDER BY MAX(t) DESC", (room, room)).fetchall()
        return [(r, a, b) for r, a, b in rows]

    def latest_run(self, room):
        """The run the laptop is on (or was on last); None when no upload ever carried a run ID."""
        runs = self.runs(room)
        return runs[0][0] if runs else None

    def rooms(self):
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT DISTINCT room FROM samples UNION "
                                                  "SELECT DISTINCT room FROM events ORDER BY room")]

    def close(self):
        with self.lock:
            self.db.close()

    def prune(self, days, position_days=None, position_runs=None):
        """Drop rows older than the retention days. With position_runs, raw X/Y also only survives for that many
        newest runs per room (events and counts keep their full history for the agent)."""
        now = time.time()
        keep = {}
        if position_runs:
            keep = {room: [r for r, _, _ in self.runs(room)][:position_runs] for room in self.rooms()}
        with self.lock:
            self.db.execute("DELETE FROM samples WHERE t < ?", (now - days * 86400,))
            self.db.execute("DELETE FROM events WHERE t < ?", (now - days * 86400,))
            self.db.execute("DELETE FROM positions WHERE t < ?", (now - (position_days or days) * 86400,))
            for room, runs in keep.items():
                if runs:     # a room with no run IDs yet keeps its positions rather than losing all of them
                    self.db.execute(f"DELETE FROM positions WHERE room = ? AND run NOT IN "
                                    f"({','.join('?' * len(runs))})", [room] + runs)
            self.db.commit()


def intervals(samples, key=1, max_gap=MAX_GAP_S):
    """Merge samples into periods where samples[i][key] > 0. key=1 -> people in the room, key=2 -> nearby.
    A reporting gap longer than max_gap closes the period: we don't claim presence we didn't observe."""
    out, cur, prev_t = [], None, None
    for s in samples:
        t, n, zones = s[0], s[key], s[3]
        if cur and (n == 0 or (prev_t is not None and t - prev_t > max_gap)):
            out.append(cur)
            cur = None
        if n > 0:
            if cur is None:
                cur = {"start": t, "end": t, "peak": n, "zones": {}}
            cur["end"] = t
            cur["peak"] = max(cur["peak"], n)
            if key == 1:
                for z, c in zones.items():
                    cur["zones"][z] = cur["zones"].get(z, 0) + c
        prev_t = t
    if cur:
        out.append(cur)
    return out


def coverage(samples, t0, t1, max_gap=MAX_GAP_S):
    """Seconds within [t0, t1) the sensors were actually reporting (consecutive samples within max_gap)."""
    total = 0.0
    for a, b in zip(samples, samples[1:]):
        if b[0] - a[0] <= max_gap:
            total += min(b[0], t1) - max(a[0], t0)
    return max(0.0, total)


def visits(events):
    """Events (sorted by t) -> one dict per visit: {"pid", "start", "end", "stops": [{"point", "start", "end"}]}.
    start is None when the enter happened before the events we were given; end / a stop's end is None while ongoing."""
    out, open_v = [], {}
    for e in events:
        pid, kind, t = e["pid"], e["kind"], e["t"]
        if kind in BOARD_KINDS:
            continue
        v = open_v.get(pid)
        if kind == "enter":
            v = {"pid": pid, "start": t, "end": None, "stops": []}
            open_v[pid] = v
            out.append(v)
            continue
        if v is None:
            v = {"pid": pid, "start": None, "end": None, "stops": []}
            out.append(v)
            open_v[pid] = v
        if kind == "arrive":
            v["stops"].append({"point": e["point"], "start": t, "end": None})
        elif kind == "depart":
            stop = next((s for s in reversed(v["stops"]) if s["point"] == e["point"] and s["end"] is None), None)
            if stop is None:
                start = t - (e.get("dur") or 0)
                stop = {"point": e["point"], "start": start, "end": None}
                v["stops"].append(stop)
            stop["end"] = t
        elif kind == "leave":
            for s in v["stops"]:
                if s["end"] is None:
                    s["end"] = t
            v["end"] = t
            open_v.pop(pid, None)
    return out


def _tz(name):
    """The named timezone. Raises ValueError instead of silently falling back to UTC: a voice agent confidently
    reading out times in the wrong timezone is worse than an error."""
    if not name or name.upper() == "UTC":
        return datetime.timezone.utc
    if ZoneInfo is None:
        raise ValueError("this Python has no zoneinfo; use tz=UTC or Python 3.9+")
    try:
        return ZoneInfo(name)
    except Exception:
        raise ValueError(f"unknown timezone {name!r} (on Windows: pip install tzdata)")


def _clock(t, tz):
    return datetime.datetime.fromtimestamp(t, tz).strftime("%I:%M %p").lstrip("0")


def _day(t, tz):
    d = datetime.datetime.fromtimestamp(t, tz)
    return f"{d.strftime('%A, %B')} {d.day}"


def _duration(seconds):
    if seconds < 3600:
        m = max(1, round(seconds / 60))
        return f"{m} minute{'s' if m != 1 else ''}"
    h = round(seconds / 3600, 1)
    return f"{h:g} hour{'s' if h != 1 else ''}"


def _short(seconds):
    """A stay or gap, said precisely when short: "10 seconds", "2 minutes"."""
    seconds = max(0.0, seconds)
    if seconds < 60:
        s = max(1, round(seconds))
        return f"{s} second{'s' if s != 1 else ''}"
    return _duration(seconds)


def _when(t, now, tz):
    """Relative while recent ("30 seconds ago", "4 minutes ago"), clock time after an hour."""
    ago = now - t
    if ago < 8:
        return "just now"
    if ago < 60:
        return f"{int(5 * round(ago / 5))} seconds ago"
    if ago < 3600:
        m = round(ago / 60)
        return f"{m} minute{'s' if m != 1 else ''} ago"
    d = datetime.datetime.fromtimestamp(t, tz).date()
    on = "" if d == datetime.datetime.fromtimestamp(now, tz).date() else f" on {_day(t, tz)}"
    return f"at {_clock(t, tz)}{on}"


def _people(n):
    return "1 person" if n == 1 else f"{n} people"


def _top_zone(zones):
    return max(zones, key=zones.get) if zones else None


def _span(iv, tz):
    a, b = _clock(iv["start"], tz), _clock(iv["end"], tz)
    return a if a == b else f"{a} to {b}"


def _interval_json(iv, tz):
    return {"start": datetime.datetime.fromtimestamp(iv["start"], tz).isoformat(timespec="seconds"),
            "end": datetime.datetime.fromtimestamp(iv["end"], tz).isoformat(timespec="seconds"),
            "minutes": round((iv["end"] - iv["start"]) / 60, 1), "peak": iv["peak"],
            "mostly_near_board": _top_zone(iv["zones"])}


def _spoken_stops(stops):
    """Stops as said aloud. Back-to-back stops at one point are one stay (estimate jitter at the 1.4 m exit edge splits
    them), and a long wander keeps its first and last SPOKEN_TAIL_STOPS stops with one summary in between."""
    merged = []
    for s in stops:
        if merged and merged[-1]["point"] == s["point"] and merged[-1]["end"] is not None:
            merged[-1] = {"point": s["point"], "start": merged[-1]["start"], "end": s["end"]}
        else:
            merged.append(dict(s))
    if len(merged) <= MAX_STOPS_SPOKEN:
        return merged
    middle = merged[1:-SPOKEN_TAIL_STOPS]
    time_at = {}
    for s in middle:
        time_at[s["point"]] = time_at.get(s["point"], 0.0) + s["end"] - s["start"]
    summary = {"between": sorted(time_at), "mostly": max(time_at, key=time_at.get), "start": middle[0]["start"],
               "seconds": middle[-1]["end"] - middle[0]["start"]}
    return merged[:1] + [summary] + merged[-SPOKEN_TAIL_STOPS:]


def _spot(p, names):
    """A point said aloud: "the Fridge" when the laptop named it (Store.point_names), else "point A"."""
    n = names.get(p) if names else None
    return f"the {n}" if n else f"point {p}"


def _board(b, names):
    n = names.get(b) if names else None
    return f"the sensor by the {n}" if n else f"board {b}"


def _points_list(points, names=None):
    said = [_spot(p, names) for p in points]
    return said[0] if len(said) == 1 else ", ".join(said[:-1]) + " and " + said[-1]


def _board_phrase(e, names=None):
    return f"{_board(e['point'], names)} " + ("went offline" if e["kind"] == "board_offline" else "came back online")


def _visit_sentence(v, t0, now, tz, board_events=(), names=None):
    """board_events: board changes during this visit, woven in by time."""
    parts = []
    if v["start"] is not None and v["start"] >= t0:
        parts.append(f"Someone entered {_when(v['start'], now, tz)}")
    else:
        parts.append("Someone was already in the room")
    items = sorted([(s["start"], 0, s) for s in _spoken_stops(v["stops"])] +
                   [(e["t"], 1, e) for e in board_events], key=lambda i: (i[0], i[1]))
    still_at = None
    for i, (_, _, s) in enumerate(items):
        if "kind" in s:
            parts.append("then " + _board_phrase(s, names))
            continue
        if "between" in s:
            parts.append(f"then moved around {_points_list(s['between'], names)} for {_short(s['seconds'])}, "
                         f"mostly by {_spot(s['mostly'], names)}")
            continue
        p = _spot(s["point"], names)
        if s["end"] is None and i < len(items) - 1:
            # A board change came after they reached this point: say where they are once the story is told.
            parts.append(f"stood by {p}" if i == 0 else f"then wandered to {p}")
            still_at = p
        elif s["end"] is None:
            parts.append(f"and is standing by {p} now" if i == 0 else f"then wandered to {p}, where they are now")
        elif i == 0:
            parts.append(f"stood by {p} for {_short(s['end'] - s['start'])}")
        else:
            parts.append(f"then wandered to {p} and stood there for {_short(s['end'] - s['start'])}")
    if v["end"] is not None:
        parts.append(("then left " if items else "and left ") + _when(v["end"], now, tz))
    elif still_at:
        parts.append(f"and they're still by {still_at}")
    elif not v["stops"] or v["stops"][-1]["end"] is not None:
        parts.append("and is still in the room")
    text = parts[0]
    for p in parts[1:]:
        text += (" " if p.startswith("and ") and len(parts) == 2 else ", ") + p
    return text + "."


def _visit_json(v, tz):
    iso = (lambda t: None if t is None else datetime.datetime.fromtimestamp(t, tz).isoformat(timespec="seconds"))
    return {"person": v["pid"], "entered": iso(v["start"]), "left": iso(v["end"]),
            "stops": [{"point": s["point"], "from": iso(s["start"]), "to": iso(s["end"]),
                       "seconds": None if s["end"] is None else round(s["end"] - s["start"], 1)} for s in v["stops"]]}


def _reporting_note(store, room, t1, now, run=None):
    """A warning when the sensors went quiet, or None. Silence must never read as "nobody came"."""
    last = store.last_report(room, run)
    if last is None:
        return "I don't have any sensor data for that room yet."
    if min(t1, now) - last > MAX_GAP_S:
        return f"Heads up: the sensors haven't reported for about {_duration(min(t1, now) - last)}, " \
               f"so I may have missed things."
    return None


def boards_down(store, room, now, run=None):
    """Board label -> since when it has been offline, for boards whose latest change is board_offline."""
    state = {}
    for e in store.events(room, now - 30 * 86400, now + 1, run):
        if e["kind"] in BOARD_KINDS:
            state[e["point"]] = e
    return {b: e["t"] for b, e in sorted(state.items()) if e["kind"] == "board_offline"}


def _down_note(down, now, tz, mentioned=(), names=None):
    """One sentence on boards still offline; boards the story already said went offline just get "still"."""
    if not down:
        return None
    bits = [f"{_board(b, names)} is still offline" if b in mentioned else f"{_board(b, names)} went offline "
            f"{_when(t, now, tz)} and is still offline" for b, t in down.items()]
    text = "; ".join(bits) + ", so tracking near " + ("it" if len(down) == 1 else "them") + " may be off."
    return ("Heads up: " + text) if not all(b in mentioned for b in down) else text[0].upper() + text[1:]


def describe_activity(store, room, t0, t1, tz, now=None, run=None):
    """Who came and went in [t0, t1), spoken as a short story per visit, with board outages woven in."""
    now = time.time() if now is None else now
    events = store.events(room, t0 - LOOKBACK_S, t1, run)
    vs = [v for v in visits(events)
          if (v["end"] is None or v["end"] >= t0) and (v["start"] is None or v["start"] < t1)]
    board_ev = [e for e in events if e["kind"] in BOARD_KINDS and t0 <= e["t"] < t1]
    names = store.point_names(room)
    note = _reporting_note(store, room, t1, now, run)
    if store.last_report(room, run) is None:
        return {"room": room, "answer": note, "visits": []}
    spoken = vs[-MAX_VISITS_SPOKEN:]
    told = set()

    def during(v):
        lo = -math.inf if v["start"] is None else v["start"]
        hi = math.inf if v["end"] is None else v["end"]
        mine = [e for e in board_ev if lo <= e["t"] <= hi and id(e) not in told]
        told.update(id(e) for e in mine)
        return mine

    sentences = [(v["start"] if v["start"] is not None else t0, _visit_sentence(v, t0, now, tz, during(v), names))
                 for v in spoken]
    # Board changes while nobody was in the room get their own short sentence, in time order with the visits.
    sentences += [(e["t"], f"{_board_phrase(e, names)[0].upper()}{_board_phrase(e, names)[1:]} "
                           f"{_when(e['t'], now, tz)}.") for e in board_ev if id(e) not in told]
    sentences.sort(key=lambda s: s[0])
    if not vs:
        window = _short(min(t1, now) - t0) if t1 >= now - 1 else None
        nobody = f"Nobody has been in the room in the last {window}." if window else \
            f"Nobody was in the room between {_clock(t0, tz)} and {_clock(t1, tz)}."
        answer = " ".join([nobody] + [s for _, s in sentences])
    else:
        answer = " ".join(s for _, s in sentences)
        if len(vs) > len(spoken):
            answer = f"There were {len(vs)} visits; the latest {len(spoken)}: " + answer
        current = sum(1 for v in vs if v["end"] is None)
        if current > 1:
            answer += f" Right now there are {current} people in the room."
    if t1 >= now - 1:
        # Still down and not already in the story: say so, or a quiet board reads as an empty corner.
        down = boards_down(store, room, now, run)
        if down:
            mentioned = {e["point"] for e in board_ev if e["kind"] == "board_offline"}
            answer += " " + _down_note(down, now, tz, mentioned, names)
    if note:
        answer = note + " " + answer
    return {"room": room, "answer": answer, "visits": [_visit_json(v, tz) for v in vs],
            "board_events": [{"board": e["point"], "kind": e["kind"], "t": round(e["t"], 2)} for e in board_ev]}


def where_now(store, room, tz, now=None, run=None):
    now = time.time() if now is None else now
    note = _reporting_note(store, room, now, now, run)
    recent = store.positions(room, now - WHERE_FRESH_S, now + 1, run=run)
    latest = {}
    for p in recent:
        latest[p["pid"]] = p
    vs = {v["pid"]: v for v in visits(store.events(room, now - LOOKBACK_S, now + 1, run)) if v["end"] is None}
    points, names = store.points(room), store.point_names(room)
    people, bits = [], []
    for pid, p in sorted(latest.items()):
        v = vs.get(pid)
        stop = v["stops"][-1] if v and v["stops"] and v["stops"][-1]["end"] is None else None
        near, dist = None, None
        if points:
            near = min(points, key=lambda k: math.hypot(points[k][0] - p["x"], points[k][1] - p["y"]))
            dist = math.hypot(points[near][0] - p["x"], points[near][1] - p["y"])
        if stop:
            bits.append(f"one is standing by {_spot(stop['point'], names)} "
                        f"(for {_short(now - stop['start'])} so far)")
        elif near is not None:
            feet = max(1, round(dist / 0.3048))     # metres on the wire, feet when spoken
            bits.append(f"one is moving around about {feet} {'foot' if feet == 1 else 'feet'} from {_spot(near, names)}")
        else:
            bits.append("one is moving around the room")
        people.append({"person": pid, "x": round(p["x"], 2), "y": round(p["y"], 2), "t": round(p["t"], 2),
                       "standing_at": stop["point"] if stop else None, "nearest_point": near,
                       "metres_from_nearest_point": None if dist is None else round(dist, 2)})
    if not people:
        answer = "I can't see anyone in the room right now."
    elif len(people) == 1:
        answer = "There's 1 person in the room: " + bits[0].replace("one is", "they're", 1) + "."
    else:
        answer = f"There are {len(people)} people in the room: " + "; ".join(bits) + "."
    down = boards_down(store, room, now, run)
    if down:
        answer += " " + _down_note(down, now, tz, names=names)
    if note:
        answer = note + " " + answer
    return {"room": room, "answer": answer, "people": people, "points": points, "point_names": names,
            "boards_offline": sorted(down)}


def summarize_day(store, room, day, tz, run=None):
    t0 = datetime.datetime.combine(day, datetime.time(0, 0), tz).timestamp()
    t1 = t0 + 86400
    samples = store.range(room, t0, t1, run)
    ins = intervals(samples, key=1)
    near = intervals(samples, key=2)
    covered = coverage(samples, t0, t1)
    label = _day(t0 + 3600, tz)
    names = store.point_names(room)
    if not samples:
        answer = f"I have no sensor data for {label}, so I can't say whether anyone was in the room."
    elif not ins:
        answer = f"Nobody was detected in the room on {label}."
    else:
        parts = []
        for iv in ins:
            bit = f"{_span(iv, tz)} ({_people(iv['peak'])}"
            z = _top_zone(iv["zones"])
            bit += f", mostly near {_spot(z, names)})" if z else ")"
            parts.append(bit)
        minutes = round(sum(iv["end"] - iv["start"] for iv in ins) / 60)
        times = "once" if len(ins) == 1 else f"{len(ins)} times"
        answer = f"On {label}, people were in the room {times}: " + "; ".join(parts) + "."
        if minutes:
            answer += f" About {minutes} minute{'s' if minutes != 1 else ''} in total."
    if near:
        answer += " Other devices were also picked up nearby, but not placed inside the room, at " + \
                  ", ".join(_span(iv, tz) for iv in near[:5]) + "."
    elapsed = min(t1, time.time()) - t0
    if samples and elapsed > 0 and covered < 0.5 * elapsed:
        answer += f" Note: the sensors were only reporting for about {_duration(covered)} of that day, " \
                  f"so I may have missed people outside that time."
    return {"room": room, "date": day.isoformat(), "answer": answer,
            "in_room": [_interval_json(iv, tz) for iv in ins],
            "nearby": [_interval_json(iv, tz) for iv in near],
            "reporting_hours": round(covered / 3600, 2)}


def status_now(store, room, tz, now=None, run=None):
    now = time.time() if now is None else now
    last = store.latest(room, run)
    if last is None:
        return {"room": room, "status": "no_data", "answer": "I don't have any sensor data for that room yet."}
    t, n_in, n_near, zones = last
    names = store.point_names(room)
    age = now - t
    if age > MAX_GAP_S:
        mins = round(age / 60)
        return {"room": room, "status": "not_reporting", "last_report": round(t),
                "answer": f"The sensors haven't reported for about {mins} minute{'s' if mins != 1 else ''}, "
                          f"so I can't tell who's there right now."}
    recent = store.range(room, now - LOOKBACK_S, now + 1, run)
    if n_in > 0:
        since = intervals(recent, key=1)[-1]["start"]
        z = _top_zone(zones)
        answer = f"Yes. There {'is' if n_in == 1 else 'are'} {_people(n_in)} in the room right now" + \
                 (f", mostly near {_spot(z, names)}." if z else ".") + f" It's been occupied for {_short(now - since)}."
        status = "occupied"
    else:
        past = intervals(recent, key=1)
        answer = "The room is empty right now." + \
                 (f" The last person left {_when(past[-1]['end'], now, tz)}." if past else "")
        status = "empty"
    if n_near:
        answer += f" {n_near} other device{'s are' if n_near != 1 else ' is'} being picked up nearby " \
                  f"but {'are' if n_near != 1 else 'is'}n't placed inside the room."
    down = boards_down(store, room, now, run)
    if down:
        answer += " " + _down_note(down, now, tz, names=names)
    return {"room": room, "status": status, "in": n_in, "nearby": n_near, "zones": zones,
            "last_report": round(t), "answer": answer, "boards_offline": sorted(down), "point_names": names}


def _parse_when(value, tz):
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        d = datetime.datetime.fromisoformat(value)
        return (d if d.tzinfo else d.replace(tzinfo=tz)).timestamp()


def make_server(host, port, store, ingest_token, query_token, default_tz="UTC", default_room=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MothCloud/2"

        def log_message(self, fmt, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authed(self, token):
            got = self.headers.get("Authorization", "")
            return got.startswith("Bearer ") and hmac.compare_digest(got[7:].encode(), token.encode())

        def do_POST(self):
            if urlparse(self.path).path != "/ingest":
                return self._send(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length", 0) or 0)
            if not 0 < length <= MAX_BODY:
                return self._send(413, {"error": "body missing or too large"})
            # Read before refusing: answering with the body unread makes Windows clients see a reset, not the 401.
            raw = self.rfile.read(length)
            if not self._authed(ingest_token):
                return self._send(401, {"error": "bad token"})
            try:
                body = json.loads(raw.decode())
                room = body.get("room")
                # cloud_sync checks for "positions_stored" to tell this version from the counts-only one.
                run = body.get("run") or ""
                out = {"ok": True, "stored": store.add(room, body.get("samples"), run),
                       "positions_stored": store.add_positions(room, body.get("positions"), run),
                       "events_stored": store.add_events(room, body.get("events"), run)}
                if body.get("points"):
                    store.set_points(room, body["points"])
                if body.get("point_names"):
                    store.set_point_names(room, body["point_names"])
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                return self._send(400, {"error": str(exc)})
            self._send(200, out)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/health":
                return self._send(200, {"ok": True, "version": 3})
            if not url.path.startswith("/api/"):
                return self._send(404, {"error": "not found"})
            if not self._authed(query_token):
                return self._send(401, {"error": "bad token"})
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                tz = _tz(q.get("tz") or default_tz)
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            room = q.get("room") or default_room or (store.rooms() or [None])[0]
            if not room or not ROOM_RE.match(room):
                return self._send(400, {"error": "unknown room", "rooms": store.rooms()})
            now = time.time()
            # Default: only the latest run, so past tests and sessions never leak into answers. run=all for
            # everything, run=<id> for one.
            want = q.get("run")
            if want == "all":
                run = None
            elif want:
                if not RUN_RE.match(want):
                    return self._send(400, {"error": "bad run"})
                run = want
            else:
                run = store.latest_run(room)

            def send(obj):
                return self._send(200, dict(obj, run=run))
            try:
                if url.path == "/api/runs":
                    return send({"room": room, "runs": [{"run": r, "from": round(a), "to": round(b)}
                                                        for r, a, b in store.runs(room)]})
                if url.path == "/api/now":
                    return send(status_now(store, room, tz, now, run))
                if url.path == "/api/recent":
                    minutes = min(max(float(q.get("minutes") or 10), 0.5), 1440)
                    out = describe_activity(store, room, now - minutes * 60, now + 1, tz, now, run)
                    return send(dict(out, window_minutes=minutes))
                if url.path == "/api/where":
                    return send(where_now(store, room, tz, now, run))
                if url.path in ("/api/events", "/api/occupancy"):
                    t1 = _parse_when(q.get("to"), tz) or now
                    t0 = _parse_when(q.get("from"), tz) or t1 - 86400
                    samples = store.range(room, t0, t1, run)
                    occ = {"in_room": [_interval_json(iv, tz) for iv in intervals(samples, 1)],
                           "nearby": [_interval_json(iv, tz) for iv in intervals(samples, 2)]}
                    if url.path == "/api/occupancy":
                        return send(dict(occ, room=room))
                    out = describe_activity(store, room, t0, t1, tz, now, run)
                    return send(dict(out, **occ, events=store.events(room, t0, t1, run)))
                if url.path == "/api/positions":
                    t1 = _parse_when(q.get("to"), tz) or now
                    t0 = _parse_when(q.get("from"), tz) or t1 - 600
                    return send({"room": room, "positions": store.positions(room, t0, t1, q.get("pid"), run=run),
                                 "points": store.points(room), "point_names": store.point_names(room)})
                if url.path == "/api/summary":
                    day = datetime.date.fromisoformat(q["date"]) if q.get("date") else \
                        datetime.datetime.now(tz).date()
                    return send(summarize_day(store, room, day, tz, run))
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            return self._send(404, {"error": "not found"})

    return ThreadingHTTPServer((host, port), Handler)


def main():
    ap = argparse.ArgumentParser(description="Moth cloud room activity API")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", default="/var/lib/moth/moth.db")
    ap.add_argument("--tz", default=os.environ.get("MOTH_TZ", "UTC"), help="default timezone for spoken times")
    ap.add_argument("--room", default=os.environ.get("MOTH_ROOM"), help="default room when a query names none")
    ap.add_argument("--retention-days", type=float, default=float(os.environ.get("MOTH_RETENTION_DAYS", 30)))
    ap.add_argument("--position-days", type=float, default=float(os.environ.get("MOTH_POSITION_DAYS", 7)),
                    help="raw X/Y tracks are kept this long (events and counts keep --retention-days)")
    ap.add_argument("--position-runs", type=int, default=int(os.environ.get("MOTH_POSITION_RUNS", 2)),
                    help="raw X/Y tracks are kept only for this many newest runs (0 = no limit)")
    args = ap.parse_args()
    ingest, query = os.environ.get("MOTH_INGEST_TOKEN", ""), os.environ.get("MOTH_QUERY_TOKEN", "")
    if len(ingest) < 16 or len(query) < 16 or ingest == query:
        raise SystemExit("Set MOTH_INGEST_TOKEN and MOTH_QUERY_TOKEN (16+ chars each, different).")
    try:
        _tz(args.tz)                     # fail at startup, not mid-call, if the default timezone can't load
    except ValueError as exc:
        raise SystemExit(f"--tz: {exc}")
    store = Store(args.db)

    def pruner():
        while True:
            store.prune(args.retention_days, args.position_days, args.position_runs)
            time.sleep(PRUNE_EVERY_S)
    threading.Thread(target=pruner, daemon=True).start()

    server = make_server(args.host, args.port, store, ingest, query, args.tz, args.room)
    print(f"Moth cloud on {args.host}:{args.port}  db={args.db}  tz={args.tz}  retention={args.retention_days}d "
          f"positions={args.position_days}d/{args.position_runs or 'all'} runs", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
