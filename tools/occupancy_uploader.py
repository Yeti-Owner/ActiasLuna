"""Send room occupancy (counts only -- no MACs, no names) from the Moth dashboard to the Moth cloud on Vultr.

Reads the running dashboard's /api/state (luna_server.py, unchanged) every few seconds and turns it into tiny samples:
    {"t": <epoch>, "in": <people in the room>, "nearby": <devices heard but outside the room>, "zones": {"B": 1}}
"zones" = which board each person was closest to. That is all that ever leaves the laptop.

Honesty rules baked in:
  - simulated crowd decoys are never counted (they live in state["decoys"], which we ignore);
  - while the crowd's jammer moves people's DISPLAYED dots, we use the true estimate (est["raw"]), not the jammed one;
  - if the dashboard isn't reachable we send nothing, so the cloud reports "not watching" instead of "empty".

The demo Wi-Fi has no internet, so samples are spooled to data/occupancy_spool.jsonl and uploaded whenever the cloud
is reachable (e.g. Ethernet / phone USB tethering alongside the Wi-Fi, or later when you switch networks).

    py tools/occupancy_uploader.py                 # uses config/cloud.json
    py tools/occupancy_uploader.py --dry-run       # print samples, send nothing (test without Vultr)
"""

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEARBY_S = 30.0          # a phone heard this recently but not placed inside the room counts as "nearby"
DEDUPE_BODY_M = 1.0      # a phone-free body estimate this close to a phone is the same person
SPOOL_MAX = 200_000      # keep at most this many unsent samples on disk
BATCH = 500


def point_in_poly(x, y, poly):
    inside = False
    for i in range(len(poly)):
        (ax, ay), (bx, by) = poly[i][:2], poly[(i + 1) % len(poly)][:2]
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / ((by - ay) or 1e-12) + ax:
            inside = not inside
    return inside


def people_from_state(state):
    """-> (in_room_points, nearby_count). Real people only, true (un-jammed) positions."""
    poly = (state.get("layout") or {}).get("polygon") or []
    pts, nearby = [], 0
    for ph in state.get("phones") or []:
        if ph.get("sim"):
            continue
        est = ph.get("est") or {}
        if est and est.get("kind") != "stale":
            raw = est.get("raw")
            x, y = (raw[0], raw[1]) if isinstance(raw, (list, tuple)) and len(raw) >= 2 else (est.get("x"), est.get("y"))
            if x is not None and y is not None and (len(poly) < 3 or point_in_poly(x, y, poly)):
                pts.append((float(x), float(y)))
                continue
        age = ph.get("age")
        if age is not None and age <= NEARBY_S:
            nearby += 1
    body = state.get("body")
    if body and body.get("x") is not None:
        bx, by = float(body["x"]), float(body["y"])
        if all(math.hypot(bx - x, by - y) > DEDUPE_BODY_M for x, y in pts):
            pts.append((bx, by))
    return pts, nearby


def nearest_boards(state, pts):
    receivers = (state.get("layout") or {}).get("receivers") or {}
    labels = {n.get("id"): (n.get("label") or n.get("id")) for n in state.get("nodes") or []}
    zones = {}
    if not receivers:
        return zones
    for x, y in pts:
        bid = min(receivers, key=lambda k: math.hypot(receivers[k]["x"] - x, receivers[k]["y"] - y))
        label = str(labels.get(bid) or bid)
        zones[label] = zones.get(label, 0) + 1
    return zones


def make_sample(state, t=None):
    pts, nearby = people_from_state(state)
    return {"t": round(time.time() if t is None else t, 3), "in": len(pts), "nearby": nearby,
            "zones": nearest_boards(state, pts)}


class Spool:
    """Unsent samples, kept on disk so nothing is lost while the laptop is on the offline demo Wi-Fi."""

    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.items = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.items = [json.loads(line) for line in f if line.strip()]

    def add(self, sample):
        self.items.append(sample)
        if len(self.items) > SPOOL_MAX:
            self.items = self.items[-SPOOL_MAX:]
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(sample) + "\n")

    def drop(self, n):
        self.items = self.items[n:]
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(s) + "\n" for s in self.items)
        os.replace(tmp, self.path)


def post(url, token, room, samples, timeout=8):
    body = json.dumps({"room": room, "samples": samples}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/ingest", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get_state(dashboard, timeout=3):
    with urllib.request.urlopen(dashboard.rstrip("/") + "/api/state", timeout=timeout) as r:
        return json.loads(r.read().decode())


def main():
    ap = argparse.ArgumentParser(description="Upload room occupancy to the Moth cloud (counts only).")
    ap.add_argument("--config", default=os.path.join(ROOT, "config", "cloud.json"))
    ap.add_argument("--dashboard", default="http://127.0.0.1:8080")
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between looks at the dashboard")
    ap.add_argument("--heartbeat", type=float, default=30.0, help="send a sample at least this often")
    ap.add_argument("--spool", default=os.path.join(ROOT, "data", "occupancy_spool.jsonl"))
    ap.add_argument("--dry-run", action="store_true", help="print samples instead of uploading")
    args = ap.parse_args()

    cfg = {}
    if not args.dry_run:
        try:
            with open(args.config, encoding="utf-8") as f:
                cfg = json.load(f)
        except OSError:
            sys.exit(f"No {args.config}. Copy config/cloud.example.json to config/cloud.json and fill it in "
                     "(or use --dry-run).")
        for key in ("url", "ingest_token", "room"):
            if not cfg.get(key):
                sys.exit(f"config/cloud.json is missing '{key}'")
    spool = None if args.dry_run else Spool(args.spool)
    last_sent, last_key, next_flush, backoff = 0.0, None, 0.0, 5.0
    print(f"Watching {args.dashboard} -> " + ("(dry run)" if args.dry_run else f"{cfg['url']} room={cfg['room']}"),
          flush=True)
    while True:
        try:
            sample = make_sample(get_state(args.dashboard))
        except (urllib.error.URLError, OSError, ValueError):
            sample = None   # dashboard down: send nothing, so the cloud says "not reporting", never "empty"
        if sample:
            key = (sample["in"], sample["nearby"], tuple(sorted(sample["zones"].items())))
            if key != last_key or sample["t"] - last_sent >= args.heartbeat:
                last_key, last_sent = key, sample["t"]
                if args.dry_run:
                    print(json.dumps(sample), flush=True)
                else:
                    spool.add(sample)
        if spool and spool.items and time.time() >= next_flush:
            batch = spool.items[:BATCH]
            try:
                post(cfg["url"], cfg["ingest_token"], cfg["room"], batch)
                spool.drop(len(batch))
                backoff = 5.0
                print(f"uploaded {len(batch)} sample(s), {len(spool.items)} waiting", flush=True)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                next_flush = time.time() + backoff
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 401:
                    why = "INGEST token rejected - put the real one in config/cloud.json, then restart"
                else:
                    why = f"cloud unreachable ({getattr(exc, 'reason', exc)})"
                print(f"{why}; {len(spool.items)} samples saved for later, retry in {int(backoff)}s", flush=True)
                backoff = min(backoff * 2, 300.0)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
