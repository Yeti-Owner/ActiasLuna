"""Checks the demo setup end to end: boards on the demo Wi-Fi, internet (Vultr, Chert, Gemini) over Ethernet, live data
reaching Vultr, and the Chert agent reading it back. Makes no Chert call, so it costs no Chert allowance.

    py tools/preflight.py              # everything (run luna_server first for the live-data checks)
    py tools/preflight.py --quick      # skip the Node agent checks

Exit code 0 when nothing FAILed. CHECK.cmd in the repo root runs this.
"""
import argparse
import email.utils
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
AGENT = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "chert-moth" / "moth-room"
NODE24 = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "node24"
SELFTEST_ROOM = "selftest"
FRESH_S = 60.0               # Vultr's newest report for the live run must be this recent

results = []


def report(status, name, detail=""):
    results.append(status)
    mark = {"OK": "  OK  ", "WARN": " WARN ", "FAIL": " FAIL ", "SKIP": " SKIP "}[status]
    print(f"[{mark}] {name}" + (f"\n         {detail}" if detail else ""), flush=True)


def http(url, token=None, body=None, timeout=15):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), headers=headers,
                                 method="GET" if body is None else "POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode()), r.headers


def local_ip_toward(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((host, port))
        return s.getsockname()[0]


def check_network(net, cloud):
    ap = net.get("ap_ip", "192.168.4.1")
    prefix = ap.rsplit(".", 1)[0] + "."
    try:
        wifi_ip = local_ip_toward(ap, 9)
    except OSError:
        wifi_ip = None
    if wifi_ip and wifi_ip.startswith(prefix):
        report("OK", f"Laptop is on the demo Wi-Fi '{net.get('ssid')}' ({wifi_ip})")
    else:
        report("FAIL", f"Laptop is not on the demo Wi-Fi '{net.get('ssid')}'",
               "Connect Wi-Fi to it (the boards' AP must be powered). Data from the boards only arrives that way.")
    host = urlparse(cloud["url"]).hostname
    t0 = time.time()
    try:
        vultr_ip = socket.gethostbyname(host)
        dns_s = time.time() - t0
        report("OK" if dns_s < 2 else "WARN", f"DNS finds {host} ({dns_s:.1f} s)",
               "" if dns_s < 2 else "Slow DNS usually means Windows is asking the demo Wi-Fi first; see the metric fix below.")
    except OSError as exc:
        report("FAIL", f"DNS can't find {host} ({exc})",
               "No internet. Plug in Ethernet (or USB tethering). If it is plugged in, see the metric fix below.")
        return None
    out_ip = local_ip_toward(vultr_ip, 443)
    if out_ip.startswith(prefix):
        report("FAIL", f"Internet traffic is going out over the demo Wi-Fi ({out_ip}), which has no internet",
               "Plug in Ethernet. If it is plugged in, make Windows prefer it (Administrator PowerShell):\n"
               "           Set-NetIPInterface -InterfaceAlias 'Wi-Fi' -InterfaceMetric 100")
    else:
        report("OK", f"Internet goes out over another adapter ({out_ip}), not the demo Wi-Fi")
    return vultr_ip


def check_vultr(cloud):
    url = cloud["url"].rstrip("/")
    try:
        _, health, headers = http(url + "/health")
    except (urllib.error.URLError, OSError) as exc:
        report("FAIL", f"Vultr unreachable at {url} ({getattr(exc, 'reason', exc)})")
        return False
    if health.get("version", 1) >= 3:
        report("OK", f"Vultr is up and running the current server (version {health['version']})")
    else:
        report("FAIL", "Vultr runs an old moth_cloud.py", "Redeploy: cloud/README.md, 'Set up or update the VM'.")
    date = headers.get("Date")
    if date:
        skew = time.time() - email.utils.parsedate_to_datetime(date).timestamp()
        if abs(skew) > 2:
            report("WARN", f"Laptop clock is {skew:+.0f} s off Vultr's",
                   "Chert refuses calls when the clock is fast. Fix: chert/README.md, 'Windows clock'.")
        else:
            report("OK", "Laptop clock agrees with Vultr (within 2 s)")
    try:
        _, out, _ = http(url + "/ingest", cloud["ingest_token"], {"room": cloud["room"], "samples": []})
        report("OK" if "positions_stored" in out else "FAIL", "INGEST token accepted (laptop can write)")
    except urllib.error.HTTPError as exc:
        report("FAIL", f"INGEST token rejected ({exc.code})", "Fix ingest_token in config/cloud.json.")
        return False
    try:
        http(f"{url}/api/runs?room={cloud['room']}", cloud.get("query_token"))
        report("OK", "QUERY token accepted (Chert can read)")
    except urllib.error.HTTPError as exc:
        report("FAIL", f"QUERY token rejected ({exc.code})", "Fix query_token in config/cloud.json, re-run chert\\setup.ps1.")
        return False
    return True


def selftest_story(cloud):
    """Writes a made-up 20 s visit to the 'selftest' room (its own run, so the real room never sees it) and reads the
    story back. Proves the whole write -> store -> narrate -> read path on the real Vultr."""
    url, now = cloud["url"].rstrip("/"), time.time()
    run = time.strftime("selftest-%Y%m%d-%H%M%S")
    ev = [("enter", None, 25), ("arrive", "A", 23), ("depart", "A", 13), ("arrive", "B", 10), ("depart", "B", 6),
          ("leave", None, 4)]
    events = [{"t": now - ago, "pid": "person-1", "kind": k, **({"point": p} if p else {})} for k, p, ago in ev]
    boards = json.loads((ROOT / "config" / "boards.json").read_text(encoding="utf-8")).get("boards", [])
    names = {b["label"]: b["name"] for b in boards if b.get("label") and b.get("name")}
    body = {"room": SELFTEST_ROOM, "run": run, "events": events, "samples": [{"t": now - 1, "in": 0}]}
    if names:
        body["point_names"] = names
    http(url + "/ingest", cloud["ingest_token"], body)
    _, out, _ = http(f"{url}/api/recent?room={SELFTEST_ROOM}&run={run}&minutes=5", cloud["query_token"])
    said = {p: f"the {names[p]}" if p in names else f"point {p}" for p in ("A", "B")}    # moth_cloud._spot
    want = (f"Someone entered 25 seconds ago, stood by {said['A']} for 10 seconds, then wandered to {said['B']} and "
            "stood there for 4 seconds, then left just now.")
    if out.get("answer") == want:
        report("OK", "Vultr round trip: wrote a test visit, read back the right story", f'"{out["answer"]}"')
    else:
        report("FAIL", "Vultr round trip gave the wrong story", f'got: "{out.get("answer")}"')


def check_live(cloud, dashboard):
    try:
        _, st, _ = http(dashboard.rstrip("/") + "/api/state", timeout=5)
    except (urllib.error.URLError, OSError):
        report("FAIL", f"Moth server not running at {dashboard}", "Start it: double-click START_BACKEND.cmd.")
        return
    online = [n["label"] for n in st.get("nodes", []) if n.get("status") == "online"]
    report("OK" if len(online) >= 3 else ("WARN" if online else "FAIL"),
           f"Boards online: {', '.join(online) or 'none'}",
           "" if len(online) >= 3 else "Power the boards and wait ~10 s; all three must be on the demo Wi-Fi.")
    people = [p for p in st.get("phones", []) if p.get("est") and p["est"].get("kind") != "stale"]
    body = st.get("body")
    report("OK" if people or body else "WARN", f"Tracking: {len(people)} phone(s)" + (", plus a body" if body else ""),
           "" if people or body else "Nobody tracked yet: join a phone to the demo Wi-Fi or walk in the room.")
    c = st.get("cloud") or {}
    if c.get("state") == "off" or not c:
        report("FAIL", "Moth server is not uploading (cloud off)", "Check config/cloud.json, restart START_BACKEND.cmd.")
        return
    run = c.get("run")
    status = "OK" if c.get("state") == "ok" else "FAIL"
    report(status, f"Moth server uploading as {run}: {c.get('state')}, {c.get('spooled', 0)} waiting",
           "" if status == "OK" else "It can't reach Vultr: fix the network checks above.")
    try:
        _, runs, _ = http(f"{cloud['url'].rstrip('/')}/api/runs?room={cloud['room']}", cloud["query_token"])
    except (urllib.error.URLError, OSError):
        return
    latest = (runs.get("runs") or [{}])[0]
    age = time.time() - latest.get("to", 0) if latest else None
    if latest.get("run") == run and age is not None and age < FRESH_S:
        report("OK", f"Vultr has this run's data, newest {age:.0f} s old (Chert will answer from it)")
    elif latest.get("run") == run:
        report("WARN", f"Vultr has this run, but the newest data is {age:.0f} s old",
               "Normal if the boards are off: nothing is sent while nothing is watched.")
    else:
        report("WARN", "Vultr hasn't received anything from this run yet",
               "It starts once the boards are online. Wait 20 s and run this again.")


def node_cmd(script):
    node = NODE24 / "node.exe" if (NODE24 / "node.exe").exists() else Path("node")
    return [str(node), "--env-file=.env.local", script]


def check_agent():
    if not (AGENT / "chert-rooms.mjs").exists():
        report("FAIL", f"No Chert agent in {AGENT}", "Run: powershell -ExecutionPolicy Bypass -File chert\\setup.ps1")
        return
    try:
        keys = subprocess.run(node_cmd("check-keys.mjs"), cwd=AGENT, capture_output=True, text=True, timeout=60)
        for line in keys.stdout.strip().splitlines():
            name, _, rest = line.partition(":")
            report("OK" if rest.strip().startswith("OK") else "FAIL", f"{name} key: {rest.strip()[:110]}")
        tools = subprocess.run(node_cmd("test-tools.mjs"), cwd=AGENT, capture_output=True, text=True, timeout=60)
        out = json.loads(tools.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        report("FAIL", f"Agent check didn't run ({exc})")
        return
    broken = [k for k in ("recent", "where", "now") if "couldn't reach" in out[k] or "isn't connected" in out[k]
              or "returned an error" in out[k]]
    report("FAIL" if broken else "OK", "Chert agent reads Vultr with its own token. It would say right now:",
           "\n         ".join(f'{k}: "{out[k]}"' for k in ("recent", "where", "now")))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quick", action="store_true", help="skip the Node agent checks")
    ap.add_argument("--dashboard", default="http://127.0.0.1:8080")
    args = ap.parse_args()
    net = json.loads((ROOT / "config" / "network.json").read_text(encoding="utf-8"))
    try:
        cloud = json.loads((ROOT / "config" / "cloud.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report("FAIL", "No config/cloud.json", "Copy config/cloud.example.json to config/cloud.json and fill it in.")
        return 1
    print("== network")
    reachable = check_network(net, cloud)
    print("== Vultr")
    if reachable and check_vultr(cloud):
        selftest_story(cloud)
    print("== live data (laptop -> Vultr)")
    check_live(cloud, args.dashboard)
    if not args.quick:
        print("== Chert agent (no call is made)")
        check_agent()
    fails, warns = results.count("FAIL"), results.count("WARN")
    print(f"\n{'READY' if not fails else 'NOT READY'}: {fails} failed, {warns} warnings", flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
