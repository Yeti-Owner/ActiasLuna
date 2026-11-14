"""Run the Moth cloud API on THIS laptop (stand-in until the Vultr VM exists). Same code as cloud/moth_cloud.py.

    py tools/local_cloud.py            # http://127.0.0.1:8000, history in data/moth_local.db

First run creates config/cloud.json with fresh tokens (git-ignored). The uploader (tools/occupancy_uploader.py)
and the Chert agent (.env.local MOTH_API_URL / MOTH_QUERY_TOKEN) point at it. When Vultr is ready, deploy
cloud/deploy.sh there and change the URL + tokens; nothing else changes.
"""
import json
import os
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config", "cloud.json")
PORT = 8000


def load_or_create():
    if os.path.exists(CFG):
        with open(CFG, encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = {}
    changed = False
    for key in ("ingest_token", "query_token"):
        if not cfg.get(key) or "paste" in cfg[key]:
            cfg[key] = secrets.token_urlsafe(24)
            changed = True
    if not cfg.get("url") or "YOUR-" in cfg["url"]:
        cfg["url"] = f"http://127.0.0.1:{PORT}"
        changed = True
    cfg.setdefault("room", "demo-room")
    if changed:
        with open(CFG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    return cfg


def main():
    cfg = load_or_create()
    if not cfg["url"].startswith("http://127.0.0.1"):
        sys.exit(f"config/cloud.json points at {cfg['url']} (not local). Edit it to use the local server.")
    os.environ["MOTH_INGEST_TOKEN"] = cfg["ingest_token"]
    os.environ["MOTH_QUERY_TOKEN"] = cfg["query_token"]
    sys.path.insert(0, os.path.join(ROOT, "cloud"))
    import moth_cloud
    sys.argv = [sys.argv[0], "--host", "127.0.0.1", "--port", str(PORT),
                "--db", os.path.join(ROOT, "data", "moth_local.db"), "--tz", "America/New_York",
                "--room", cfg["room"]]
    moth_cloud.main()


if __name__ == "__main__":
    main()
