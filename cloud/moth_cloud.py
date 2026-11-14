"""Moth cloud: occupancy history for a monitored room. Runs on a Vultr VM; standard library only (Python 3.9+).

The laptop's tools/occupancy_uploader.py POSTs small samples ("2 people in the room, 1 nearby, mostly near board B")
here. The Chert FaceTime agent (or anyone with the query token) asks questions through the read endpoints, which
answer with JSON plus a short plain-English `answer` a voice agent can say out loud.

PRIVACY: no MAC addresses, names or raw positions are ever stored -- only counts per moment and which board people
were closest to. Simulated decoys never reach this service (the uploader drops them). Samples older than
--retention-days are deleted.

Endpoints
  GET  /health                                   no auth
  POST /ingest            Bearer INGEST token    {"room": "...", "samples": [{"t": epoch, "in": n, "nearby": n,
                                                                              "zones": {"A": n, ...}}]}
  GET  /api/now           Bearer QUERY token     ?room=&tz=                -> is anyone in the room right now?
  GET  /api/summary       Bearer QUERY token     ?room=&date=YYYY-MM-DD&tz= -> when were people in the room that day?
  GET  /api/occupancy     Bearer QUERY token     ?room=&from=&to=&tz=      -> intervals in a range (ISO or epoch)

Run:  MOTH_INGEST_TOKEN=... MOTH_QUERY_TOKEN=... python3 moth_cloud.py --port 8000 --db /var/lib/moth/moth.db
"""

import argparse
import datetime
import hmac
import json
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

MAX_GAP_S = 90.0            # samples further apart than this mean the sensors weren't reporting (not "room empty")
MAX_BODY = 1 << 20          # 1 MB per ingest request
MAX_SAMPLES = 5000          # per ingest request
MAX_COUNT = 500             # sanity cap on people per sample
ROOM_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
ZONE_RE = re.compile(r"^[A-Za-z0-9_. -]{1,32}$")


# ---- storage --------------------------------------------------------------------------------------------------------
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
            self.db.commit()

    def add(self, room, samples):
        """Validate and store samples. Returns how many were stored. Raises ValueError on bad input."""
        if not ROOM_RE.match(str(room)):
            raise ValueError("room must be 1-64 chars of letters, digits, _ . -")
        if not isinstance(samples, list) or len(samples) > MAX_SAMPLES:
            raise ValueError(f"samples must be a list of at most {MAX_SAMPLES}")
        now = time.time()
        rows = []
        for s in samples:
            if not isinstance(s, dict):
                raise ValueError("each sample must be an object")
            t = float(s["t"])
            if not (1.5e9 < t < now + 3600):
                raise ValueError("sample time out of range")
            n_in, n_near = int(s.get("in", 0)), int(s.get("nearby", 0))
            if not (0 <= n_in <= MAX_COUNT and 0 <= n_near <= MAX_COUNT):
                raise ValueError("counts out of range")
            zones = {}
            for k, v in (s.get("zones") or {}).items():
                if ZONE_RE.match(str(k)) and int(v) > 0 and len(zones) < 16:
                    zones[str(k)] = min(int(v), MAX_COUNT)
            rows.append((room, t, n_in, n_near, json.dumps(zones, sort_keys=True)))
        with self.lock:
            self.db.executemany("INSERT OR REPLACE INTO samples VALUES (?, ?, ?, ?, ?)", rows)
            self.db.commit()
        return len(rows)

    def range(self, room, t0, t1):
        with self.lock:
            cur = self.db.execute("SELECT t, n_in, n_near, zones FROM samples WHERE room = ? AND t >= ? AND t < ? "
                                  "ORDER BY t", (room, t0, t1))
            return [(t, a, b, json.loads(z)) for t, a, b, z in cur.fetchall()]

    def latest(self, room):
        with self.lock:
            row = self.db.execute("SELECT t, n_in, n_near, zones FROM samples WHERE room = ? ORDER BY t DESC LIMIT 1",
                                  (room,)).fetchone()
        return None if row is None else (row[0], row[1], row[2], json.loads(row[3]))

    def rooms(self):
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT DISTINCT room FROM samples ORDER BY room")]

    def close(self):
        with self.lock:
            self.db.close()

    def prune(self, days):
        with self.lock:
            self.db.execute("DELETE FROM samples WHERE t < ?", (time.time() - days * 86400,))
            self.db.commit()


# ---- analysis -------------------------------------------------------------------------------------------------------
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


def summarize_day(store, room, day, tz):
    t0 = datetime.datetime.combine(day, datetime.time(0, 0), tz).timestamp()
    t1 = t0 + 86400
    samples = store.range(room, t0, t1)
    ins = intervals(samples, key=1)
    near = intervals(samples, key=2)
    covered = coverage(samples, t0, t1)
    label = _day(t0 + 3600, tz)
    if not samples:
        answer = f"I have no sensor data for {label}, so I can't say whether anyone was in the room."
    elif not ins:
        answer = f"Nobody was detected in the room on {label}."
    else:
        parts = []
        for iv in ins:
            bit = f"{_span(iv, tz)} ({_people(iv['peak'])}"
            z = _top_zone(iv["zones"])
            bit += f", mostly near board {z})" if z else ")"
            parts.append(bit)
        minutes = round(sum(iv["end"] - iv["start"] for iv in ins) / 60)
        times = "once" if len(ins) == 1 else f"{len(ins)} times"
        answer = f"On {label}, people were in the room {times}: " + "; ".join(parts) + "."
        if minutes:
            answer += f" About {minutes} minute{'s' if minutes != 1 else ''} in total."
    if near:
        answer += " Other devices were also picked up nearby, but not placed inside the room, at " + \
                  ", ".join(_span(iv, tz) for iv in near[:5]) + "."
    elapsed = min(t1, time.time()) - t0          # the part of the day that has happened so far
    if samples and elapsed > 0 and covered < 0.5 * elapsed:
        answer += f" Note: the sensors were only reporting for about {_duration(covered)} of that day, " \
                  f"so I may have missed people outside that time."
    return {"room": room, "date": day.isoformat(), "answer": answer,
            "in_room": [_interval_json(iv, tz) for iv in ins],
            "nearby": [_interval_json(iv, tz) for iv in near],
            "reporting_hours": round(covered / 3600, 2)}


def status_now(store, room, tz, now=None):
    now = time.time() if now is None else now
    last = store.latest(room)
    if last is None:
        return {"room": room, "status": "no_data", "answer": "I don't have any sensor data for that room yet."}
    t, n_in, n_near, zones = last
    age = now - t
    if age > MAX_GAP_S:
        mins = round(age / 60)
        return {"room": room, "status": "not_reporting", "last_report": round(t),
                "answer": f"The sensors haven't reported for about {mins} minute{'s' if mins != 1 else ''}, "
                          f"so I can't tell who's there right now."}
    recent = store.range(room, now - 6 * 3600, now + 1)
    if n_in > 0:
        since = intervals(recent, key=1)[-1]["start"]
        z = _top_zone(zones)
        answer = f"Yes. There {'is' if n_in == 1 else 'are'} {_people(n_in)} in the room right now, " \
                 f"since {_clock(since, tz)}" + (f", mostly near board {z}." if z else ".")
        status = "occupied"
    else:
        past = intervals(recent, key=1)
        answer = "The room is empty right now." + \
                 (f" The last person left around {_clock(past[-1]['end'], tz)}." if past else "")
        status = "empty"
    if n_near:
        answer += f" {n_near} other device{'s are' if n_near != 1 else ' is'} being picked up nearby " \
                  f"but {'are' if n_near != 1 else 'is'}n't placed inside the room."
    return {"room": room, "status": status, "in": n_in, "nearby": n_near, "zones": zones,
            "last_report": round(t), "answer": answer}


def _parse_when(value, tz):
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        d = datetime.datetime.fromisoformat(value)
        return (d if d.tzinfo else d.replace(tzinfo=tz)).timestamp()


# ---- HTTP -------------------------------------------------------------------------------------------------------------
def make_server(host, port, store, ingest_token, query_token, default_tz="UTC", default_room=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MothCloud/1"

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
            if not self._authed(ingest_token):
                return self._send(401, {"error": "bad token"})
            length = int(self.headers.get("Content-Length", 0) or 0)
            if not 0 < length <= MAX_BODY:
                return self._send(413, {"error": "body missing or too large"})
            try:
                body = json.loads(self.rfile.read(length).decode())
                stored = store.add(body.get("room"), body.get("samples"))
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                return self._send(400, {"error": str(exc)})
            self._send(200, {"ok": True, "stored": stored})

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/health":
                return self._send(200, {"ok": True})
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
            try:
                if url.path == "/api/now":
                    return self._send(200, status_now(store, room, tz))
                if url.path == "/api/summary":
                    day = datetime.date.fromisoformat(q["date"]) if q.get("date") else \
                        datetime.datetime.now(tz).date()
                    return self._send(200, summarize_day(store, room, day, tz))
                if url.path == "/api/occupancy":
                    t1 = _parse_when(q.get("to"), tz) or time.time()
                    t0 = _parse_when(q.get("from"), tz) or t1 - 86400
                    samples = store.range(room, t0, t1)
                    return self._send(200, {"room": room,
                                            "in_room": [_interval_json(iv, tz) for iv in intervals(samples, 1)],
                                            "nearby": [_interval_json(iv, tz) for iv in intervals(samples, 2)]})
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            return self._send(404, {"error": "not found"})

    return ThreadingHTTPServer((host, port), Handler)


def main():
    ap = argparse.ArgumentParser(description="Moth cloud occupancy API")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", default="/var/lib/moth/moth.db")
    ap.add_argument("--tz", default=os.environ.get("MOTH_TZ", "UTC"), help="default timezone for spoken times")
    ap.add_argument("--room", default=os.environ.get("MOTH_ROOM"), help="default room when a query names none")
    ap.add_argument("--retention-days", type=float, default=float(os.environ.get("MOTH_RETENTION_DAYS", 30)))
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
            store.prune(args.retention_days)
            time.sleep(3600)
    threading.Thread(target=pruner, daemon=True).start()

    server = make_server(args.host, args.port, store, ingest, query, args.tz, args.room)
    print(f"Moth cloud on {args.host}:{args.port}  db={args.db}  tz={args.tz}  retention={args.retention_days}d",
          flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
