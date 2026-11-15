"""Frontend laptop: serves the website with its 3D room fed from the Vultr cloud, so no boards or luna_server needed.

    py server/room_viewer.py                # http://127.0.0.1:8090/, opens the browser
    py server/room_viewer.py --last-known   # debug: keep drawing the last known positions when Vultr is stale

Reads config/cloud.json (url + query_token; copy it from the backend laptop). The QUERY token stays here: the browser
only talks to /api/cloud/room, never to Vultr. Routes: / and /static/* = the built website (web/static, Vite base
/static/), /room/ = server/static/cloud_room.html with view3d.js, /scan.glb and /api/scan = data/scan.
"""
import argparse
import json
import mimetypes
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "server" / "static"
WEB = ROOT / "web" / "static"
DATA = ROOT / "data"
CFG = ROOT / "config" / "cloud.json"

POLL_S = 0.25              # matches cloud_sync.POSITION_S on the backend
STATUS_EVERY_S = 2.0       # /api/now (the heartbeat that says the backend is up) changes slowly
STALE_S = 300.0            # no upload from the backend laptop for this long = draw nobody
PERSON_FRESH_S = 3.0       # a person is in the room if seen within this of the newest upload
TRAIL_S = 10.0
FIRST_WINDOW_S = 120.0     # positions window before any cloud timestamp is known (covers clock skew)
LAST_KNOWN_EVERY_S = 30.0  # the full-history fetch for --last-known is heavier, so it refreshes slowly
TIMEOUT_S = 8.0

MIME = {"js": "application/javascript", "mjs": "application/javascript", "css": "text/css", "html": "text/html",
        "json": "application/json", "svg": "image/svg+xml", "glb": "model/gltf-binary", "woff2": "font/woff2",
        "png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "ico": "image/x-icon"}


def people_from(positions, ref, kind):
    """Latest point and short trail per pid, for pids seen within PERSON_FRESH_S of ref (a cloud timestamp)."""
    by_pid = {}
    for p in positions:
        by_pid.setdefault(p["pid"], []).append(p)
    out = []
    for pid, track in sorted(by_pid.items()):
        last = track[-1]
        if last["t"] < ref - PERSON_FRESH_S:
            continue
        trail = [[round(q["t"], 2), q["x"], q["y"]] for q in track if q["t"] >= last["t"] - TRAIL_S]
        out.append({"pid": pid, "x": last["x"], "y": last["y"], "t": round(last["t"], 2), "kind": kind,
                    "trail": trail})
    return out


class Cloud:
    """Polls Vultr in one thread; handlers only read self.view."""

    def __init__(self, cfg, last_known):
        self.url = cfg.get("url", "").rstrip("/")
        self.token = cfg.get("query_token", "")
        self.room = cfg.get("room")
        self.last_known = last_known
        self.lock = threading.Lock()
        self.view = {"status": "connecting", "people": [], "points": {}, "last_report": None, "age_s": None,
                     "stale_s": STALE_S, "last_known": last_known}
        self._history = None    # (fetched at, newest t, people) for --last-known
        self._status = (-1e18, {})   # (fetched at, /api/now answer)
        self._newest = None     # newest cloud timestamp seen; positions are fetched relative to it, so the two
                                # laptops' clocks never need to agree

    def _get(self, path, **q):
        if self.room:
            q["room"] = self.room
        req = urllib.request.Request(f"{self.url}{path}?{urlencode(q)}",
                                     headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.load(r)

    def poll(self):
        now = time.time()
        if now - self._status[0] >= STATUS_EVERY_S:
            self._status = (now, self._get("/api/now"))
        status = self._status[1]
        since = now - FIRST_WINDOW_S if self._newest is None else self._newest - TRAIL_S - PERSON_FRESH_S
        recent = self._get("/api/positions", **{"from": since, "to": max(now, self._newest or now) + 86400})
        pos = recent.get("positions") or []
        stamps = [t for t in (status.get("last_report"), pos[-1]["t"] if pos else None) if t is not None]
        last = max(stamps) if stamps else None
        if last is not None:
            self._newest = max(last, self._newest or last)
        if last is None and self.last_known:
            last = self._history_people(now)[0]
        age = None if last is None else max(0.0, now - last)
        if age is not None and age <= STALE_S:
            state, people = "live", people_from(pos, last, "estimated")
        elif self.last_known and last is not None:
            state, people = "stale", self._history_people(now)[1]
        else:
            state, people = ("stale" if last is not None else "no_data"), []
        view = {"status": state, "people": people, "points": recent.get("points") or {},
                "last_report": last, "age_s": None if age is None else round(age, 1), "stale_s": STALE_S,
                "last_known": self.last_known, "run": recent.get("run")}
        with self.lock:
            self.view = view

    def _history_people(self, now):
        """(newest position t, people at that moment) from the latest run; refetched every LAST_KNOWN_EVERY_S."""
        if self._history is None or now - self._history[0] > LAST_KNOWN_EVERY_S:
            pos = self._get("/api/positions", **{"from": 1, "to": now + 86400}).get("positions") or []
            newest = pos[-1]["t"] if pos else None
            self._history = (now, newest, people_from(pos, newest, "stale") if pos else [])
        return self._history[1], self._history[2]

    def run(self):
        while True:
            try:
                self.poll()
            except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
                with self.lock:
                    self.view = dict(self.view, status="offline", error=str(exc)[:200])
            time.sleep(POLL_S)

    def snapshot(self):
        with self.lock:
            return dict(self.view, now=time.time())


def make_handler(cloud):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            pass

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, "application/json; charset=utf-8", json.dumps(obj).encode())

        def _file(self, base, rel):
            path = (base / rel).resolve()
            if not path.is_relative_to(base.resolve()) or not path.is_file():
                return self._json({"error": "not found"}, 404)
            ext = path.suffix.lstrip(".").lower()
            ctype = MIME.get(ext) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
                ctype += "; charset=utf-8"
            self._send(200, ctype, path.read_bytes())

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/cloud/room":
                return self._json(cloud.snapshot())
            if path == "/api/scan":
                try:
                    return self._json(json.loads((DATA / "scan" / "room.json").read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    return self._json(None)
            if path == "/scan.glb":
                return self._file(DATA / "scan", "scan.glb")
            if path in ("/room", "/room/"):
                return self._file(STATIC, "cloud_room.html")
            if path.startswith("/room/"):
                return self._file(STATIC, path[len("/room/"):])
            if path in ("/", "/index.html"):
                if not (WEB / "index.html").is_file():
                    return self._send(503, "text/plain; charset=utf-8",
                                      b"Website not built: run  npm run build  in web/ (FRONTEND.cmd does it).")
                return self._file(WEB, "index.html")
            if path.startswith("/static/"):
                return self._file(WEB, path[len("/static/"):])
            return self._json({"error": "not found"}, 404)

    return Handler


def main():
    ap = argparse.ArgumentParser(description="Website + 3D room fed from the Vultr cloud (frontend laptop).")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--last-known", action="store_true",
                    help=f"debug: show the last known positions even when Vultr is older than {STALE_S:.0f} s")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    try:
        cfg = json.loads(CFG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit(f"need {CFG} with url and query_token (copy it from the backend laptop)")
    if not cfg.get("url") or not cfg.get("query_token"):
        raise SystemExit(f"{CFG} needs url and query_token")
    cloud = Cloud(cfg, args.last_known)
    threading.Thread(target=cloud.run, daemon=True).start()
    try:
        server = ThreadingHTTPServer((args.host, args.port), make_handler(cloud))
    except OSError as exc:
        raise SystemExit(f"cannot open port {args.port} ({exc}). Is the viewer already running?")
    server.daemon_threads = True
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Actias frontend: {url}  (room from {cloud.url}{', showing last known positions' if args.last_known else ''})",
          flush=True)
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
