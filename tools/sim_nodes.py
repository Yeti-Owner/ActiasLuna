"""Board SIMULATOR for running the dashboard without hardware. Its numbers say nothing about real accuracy.

Speaks the real board UDP protocol (firmware/luna_node/main/main.c), so protocol changes must be mirrored here.
Run the server with --targets 127.0.0.1:6101,127.0.0.1:6102,127.0.0.1:6103 and steer phones over UDP on 6100:
  GOTO <phone> <x> <y> | PATH <phone> | OFFSET <phone> <dB> | RATE <phone> <frames/s> | CROWD <n>
--camera adds a simulated laptop camera (camera/luna_camera.py's `cam` packet) that sees phones and crowd walkers;
a desk in the far corner hides feet there, so the head-height mapping gets exercised. --camera-cal writes its
calibration (feet and head) so the server has something to use.
"""
import argparse
import json
import math
import random
import socket
import threading
import time
from pathlib import Path

FT = 0.3048
ROOM = {"w": 10 * FT, "h": 11 * FT}
# Receive gains differ per board on real hardware too; these keep the simulated map honest about that.
BOARDS = [
    {"id": "b0b0b0000001", "ifm": "b0b0b0000002", "role": "ap", "port": 6101, "x": 2.9, "y": 3.1, "g": 0.0},
    {"id": "b0b0b0000011", "ifm": "b0b0b0000011", "role": "rx", "port": 6102, "x": 2.95, "y": 0.67, "g": 6.0},
    {"id": "b0b0b0000021", "ifm": "b0b0b0000021", "role": "rx", "port": 6103, "x": 0.1, "y": 3.0, "g": 8.0},
]
PHONES = {
    "p1": {"mac": "5a1111111111", "ip": "192.168.4.2", "nm": "SimPhone", "x": 1.5, "y": 1.5, "off": 0.0, "fps": 45.0,
           "path": False},
}
# Other people crossing the room ("CROWD <n>"). A body within BODY_R_M of the line between a transmitter and a board
# blocks that path by up to BODY_DB.
BODY_R_M = 0.35
BODY_DB = 10.0
WALKER_SPEED = 1.2
walkers = []
crowd_size = [0]
lock = threading.Lock()
rng = random.Random(42)


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy + 1e-12)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def body_loss(ax, ay, bx, by):
    loss = 0.0
    for w in walkers:
        d = _seg_dist(w["x"], w["y"], ax, ay, bx, by)
        if d < BODY_R_M and math.hypot(w["x"] - ax, w["y"] - ay) > 0.3:
            loss += BODY_DB * (1 - d / BODY_R_M) * w["size"]
    return loss


def new_walker():
    w, h = ROOM["w"], ROOM["h"]
    side = rng.randrange(4)
    a, b = rng.uniform(0.1, 0.9), rng.uniform(0.1, 0.9)
    start = [(a * w, 0), (w, a * h), (a * w, h), (0, a * h)][side]
    end = [(b * w, h), (0, b * h), (b * w, 0), (w, b * h)][side]
    length = math.hypot(end[0] - start[0], end[1] - start[1]) or 1.0
    speed = WALKER_SPEED * rng.uniform(0.8, 1.2)
    return {"x": start[0], "y": start[1], "vx": (end[0] - start[0]) / length * speed,
            "vy": (end[1] - start[1]) / length * speed, "left": length / speed, "size": rng.uniform(0.7, 1.2),
            "hgt": rng.uniform(1.55, 1.9)}


def step_walkers(dt):
    for w in walkers:
        w["x"] += w["vx"] * dt
        w["y"] += w["vy"] * dt
        w["left"] -= dt
    walkers[:] = [w for w in walkers if w["left"] > 0]
    while len(walkers) < crowd_size[0]:
        walkers.append(new_walker())


def pathloss(x, y, board):
    d = max(0.3, math.hypot(x - board["x"], y - board["y"]))
    return -40 - 22 * math.log10(d) + board["g"]


def phone_rssi(board, phone):
    # A fixed bumpy field stands in for multipath, so standing still keeps one "fade".
    bumps = 2.5 * math.sin(3.1 * phone["x"] + board["x"]) * math.cos(2.3 * phone["y"] - board["y"])
    return (pathloss(phone["x"], phone["y"], board) + bumps + phone["off"]
            - body_loss(phone["x"], phone["y"], board["x"], board["y"]))


def poisson_ish(lam):
    trials = int(lam * 3) + 3
    return sum(1 for _ in range(trials) if rng.random() < lam / trials)


def phone_obs(board):
    out = []
    for phone in PHONES.values():
        n = poisson_ish(phone["fps"] * 0.25)
        if n == 0:
            continue
        mu = phone_rssi(board, phone)
        vals = [mu + rng.gauss(0, 3) for _ in range(n)]
        mean = sum(vals) / n
        sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / n)
        ordered = sorted(round(v) for v in vals)
        out.append({"m": phone["mac"], "n": n, "r": round(mean, 1), "sd": round(sd, 1),
                    "q3": ordered[(3 * (n - 1) + 3) // 4], "mn": int(min(vals)), "mx": int(max(vals)),
                    "dt": n, "mg": 0, "ct": 0})
    return out


def link_obs(board, sensing):
    out = []
    for other in BOARDS:
        if other is board or rng.random() >= 0.8:
            continue
        r = (pathloss(other["x"], other["y"], board) - body_loss(other["x"], other["y"], board["x"], board["y"])
             + rng.gauss(0, 1.5))
        o = {"m": other["ifm"], "n": 10 if sensing else 1, "r": round(r, 1),
             "sd": round(abs(rng.gauss(1.0, 0.3)), 2) if sensing else 0, "mn": int(r), "mx": int(r),
             "dt": 1, "mg": 0, "ct": 0}
        if sensing:
            o["cs"] = [int(40 + 6 * math.sin(i + other["x"]) + rng.gauss(0, 1)) for i in range(13)]
            o["cm"] = round(abs(rng.gauss(1.8, 0.3)), 2)
        out.append(o)
    return out


def run_board(board):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", board["port"]))
    sock.settimeout(0.01)
    dest, sub_at, seq, sensing = None, 0.0, 0, False
    boot = due = time.time()
    sta_at = 0.0

    def packet(kind, now):
        nonlocal seq
        seq += 1
        return {"v": 1, "t": kind, "role": board["role"], "id": board["id"], "ifm": board["ifm"], "seq": seq,
                "up": int((now - boot) * 1000)}

    while True:
        try:
            data, src = sock.recvfrom(256)
            if data.startswith(b"LUNA_SUB"):
                dest, sub_at, sensing = src, time.time(), b"sense=1" in data
        except (socket.timeout, OSError):
            pass
        now = time.time()
        if now < due:
            continue
        due += 0.25
        if not dest or now - sub_at > 30:
            continue
        with lock:
            obs = phone_obs(board) + link_obs(board, sensing)
        ap = BOARDS[0]
        aprssi = int(pathloss(ap["x"], ap["y"], board) + rng.gauss(0, 1)) if board["role"] == "rx" else 0
        tel = packet("tel", now)
        tel.update({"part": 0, "win": 250, "ch": 6, "aprssi": aprssi, "ovf": 0, "disc": 0, "heap": 200000, "csi": 0,
                    "obs": obs})
        sock.sendto(json.dumps(tel).encode(), dest)
        if board["role"] == "ap" and now - sta_at >= 1:
            sta_at = now
            sta = packet("sta", now)
            with lock:
                sta["sta"] = [{"m": p["mac"], "ip": p["ip"], "r": int(phone_rssi(board, p)), "nm": p["nm"]}
                              for p in PHONES.values()]
            sta["http"] = 0
            sock.sendto(json.dumps(sta).encode(), dest)


def move_phones():
    t = 0.0
    while True:
        time.sleep(0.1)
        t += 0.1
        with lock:
            step_walkers(0.1)
            for phone in PHONES.values():
                if phone["path"]:
                    phone["x"] = ROOM["w"] / 2 + 0.35 * ROOM["w"] * math.cos(t * 0.25)
                    phone["y"] = ROOM["h"] / 2 + 0.35 * ROOM["h"] * math.sin(t * 0.25)


def control_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 6100))
    while True:
        try:
            data, src = sock.recvfrom(256)
        except ConnectionResetError:
            # Windows reports an earlier "ok" reply that bounced (the sender had already closed its socket) on the
            # next read; uncaught, it killed this thread and every simulated board and camera with it.
            continue
        words = data.decode().split()
        try:
            cmd, name = words[0].upper(), words[1]
            with lock:
                if cmd == "CROWD":
                    crowd_size[0] = int(name)
                else:
                    phone = PHONES.setdefault(name, {"mac": "5a22222222%02d" % len(PHONES),
                                                     "ip": "192.168.4.%d" % (2 + len(PHONES)), "nm": name,
                                                     "x": ROOM["w"] / 2, "y": ROOM["h"] / 2, "off": 0.0,
                                                     "fps": 45.0, "path": False})
                    if cmd == "GOTO":
                        phone.update(x=float(words[2]), y=float(words[3]), path=False)
                    elif cmd == "PATH":
                        phone["path"] = True
                    elif cmd == "OFFSET":
                        phone["off"] = float(words[2])
                    elif cmd == "RATE":
                        phone["fps"] = float(words[2])
            sock.sendto(b"ok", src)
        except (IndexError, ValueError):
            sock.sendto(b"err", src)


# Simulated laptop camera (--camera): a pinhole on a table in one corner, looking at the room's centre. It sees every
# simulated person, phone holders and crowd walkers alike, and speaks camera/luna_camera.py's `cam` packet.
CAM_HEIGHT_M = 0.8
CAM_PITCH = math.radians(5)   # laptop lids sit nearly upright
CAM_FOCAL = 0.55          # focal length / image width (about 85 degrees across, a typical laptop webcam)
CAM_ASPECT = 4 / 3
CAM_NOISE = 0.004         # foot / head point noise, fraction of the frame
PERSON_H_M = 1.7          # the calibrating person's height
PHONE_H_M = 1.78          # phone holders are a little taller, so the head estimate is honestly off
# A desk in the far part of the room hides the feet of anyone standing there (fractions of the room).
CAM_DESK = (0.5, 0.55, 1.0, 1.0)


def behind_desk(x, y):
    x0, y0, x1, y1 = CAM_DESK
    return x0 * ROOM["w"] <= x <= x1 * ROOM["w"] and y0 * ROOM["h"] <= y <= y1 * ROOM["h"]


def cam_project(x, y, z=0.0):
    """Normalised image (u, v) of a room point, or None if it is behind the camera or out of frame."""
    cx, cy = 0.05 * ROOM["w"], 0.05 * ROOM["h"]
    yaw = math.atan2(ROOM["h"] / 2 - cy, ROOM["w"] / 2 - cx)
    fwd = (math.cos(yaw) * math.cos(CAM_PITCH), math.sin(yaw) * math.cos(CAM_PITCH), -math.sin(CAM_PITCH))
    right = (math.sin(yaw), -math.cos(yaw), 0.0)
    up = (right[1] * fwd[2] - right[2] * fwd[1], right[2] * fwd[0] - right[0] * fwd[2],
          right[0] * fwd[1] - right[1] * fwd[0])
    d = (x - cx, y - cy, z - CAM_HEIGHT_M)
    depth = sum(a * b for a, b in zip(d, fwd))
    if depth <= 0.1:
        return None
    u = 0.5 + CAM_FOCAL * sum(a * b for a, b in zip(d, right)) / depth
    v = 0.5 - CAM_FOCAL * CAM_ASPECT * sum(a * b for a, b in zip(d, up)) / depth
    return (u, v) if 0.0 < u < 1.0 and 0.0 < v < 1.0 else None


UPPER_M = 0.6            # a head-and-shoulders box spans the top this much of a person


def cam_record(x, y, hgt, feet_hidden=False, noise=None):
    """One detection record like luna_camera.person_record for a person at (x, y) of height hgt, or None.

    Feet in view: a whole-body box ("body", head to feet). Feet hidden or out of frame: a head-and-shoulders box
    ("upper"). Box sizes follow the projection, so head-size ranging (camera/headrange.py) gets realistic input.
    """
    noise = noise or (lambda: 0.0)
    feet, head = cam_project(x, y), cam_project(x, y, hgt)
    if feet_hidden:
        feet = None
    if not feet and not head:
        return None
    if feet:
        top, bottom, kind = (head[1] if head else 0.0), feet[1], "body"
    else:
        # Bigger people have bigger heads and shoulders, so head-size ranging carries an honest size error.
        chest = cam_project(x, y, hgt - UPPER_M * hgt / PERSON_H_M)
        top, bottom, kind = head[1], (chest[1] if chest else 1.0), "upper"
    bh = max(0.02, bottom - top)
    u = (feet or head)[0] + noise()
    d = {"bx": round(u - 0.175 * bh / CAM_ASPECT, 4), "by": round(top, 4), "bw": round(0.35 * bh / CAM_ASPECT, 4),
         "bh": round(bh * (1 + noise() * 5), 4), "s": 0.9, "k": kind}
    if feet:
        d["u"], d["v"] = round(feet[0] + noise(), 4), round(feet[1] + noise(), 4)
    if head:
        d["hu"], d["hv"] = round(head[0] + noise(), 4), round(head[1] + noise(), 4)
    return d


def cam_detections():
    """Detection records like luna_camera.person_record: feet when in frame and not behind the desk, head top when
    in frame. People of different heights, so head-based estimates carry a real error."""
    people = ([(p["x"], p["y"], p.get("hgt", PHONE_H_M)) for p in PHONES.values()]
              + [(w["x"], w["y"], w.get("hgt", PERSON_H_M)) for w in walkers])
    noise = lambda: rng.gauss(0, CAM_NOISE)  # noqa: E731
    out = []
    for x, y, hgt in people:
        d = cam_record(x, y, hgt, feet_hidden=behind_desk(x, y), noise=noise)
        if d:
            out.append(d)
    return out


def run_camera(dest):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    boot = time.time()
    seq = 0
    while True:
        time.sleep(0.25)
        with lock:
            dets = cam_detections()
        seq += 1
        pkt = {"v": 1, "t": "cam", "id": "cam:sim", "role": "cam", "fw": "cam-sim", "seq": seq,
               "up": int((time.time() - boot) * 1000), "w": 640, "h": 480, "det": dets}
        try:
            sock.sendto(json.dumps(pkt).encode(), dest)
        except OSError:
            pass


def head_range_calibration():
    """What camera/head_setup.py would save for the simulated camera: a PERSON_H_M person on three spots (left,
    middle, right of view), seen whole and as head-and-shoulders."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "camera"))
    import headrange
    w, h = ROOM["w"], ROOM["h"]
    cam = (0.05 * w, 0.05 * h)
    samples = []
    for fx, fy in [(0.35, 0.9), (0.7, 0.7), (0.9, 0.35)]:
        x, y = fx * w, fy * h
        obs = {}
        for hidden in (False, True):
            d = cam_record(x, y, PERSON_H_M, feet_hidden=hidden)
            got = headrange.observe(d, 640, 480) if d else None
            if got:
                obs[got[0]] = got[1:]
        samples.append({"xy": (x, y), "obs": obs})
    return headrange.solve(cam, samples)


def write_camera_calibration(path: Path, head=True, floor=True, head_range=True):
    """Calibration as the camera tools make it: calibrate.py's feet (floor) and head-height points from a PERSON_H_M
    person standing on marks, and head_setup.py's head-size ranging. Each part can be left out to test the others."""
    w, h = ROOM["w"], ROOM["h"]
    feet_pts, head_pts = [], []
    for fx, fy in [(0.45, 0.45), (0.95, 0.45), (0.45, 0.95), (0.95, 0.95), (0.7, 0.7), (0.6, 0.85), (0.85, 0.6)]:
        x, y = fx * w, fy * h
        feet, top = cam_project(x, y), cam_project(x, y, PERSON_H_M)
        if feet:
            feet_pts.append({"u": feet[0], "v": feet[1], "x": x, "y": y})
        if top:
            head_pts.append({"u": top[0], "v": top[1], "x": x, "y": y})
    # The camera's floor spot: fusion needs it for bearings (direction only) when the feet are hidden.
    record = {"version": 1, "source": "sim_nodes", "camera_xy": [0.05 * w, 0.05 * h]}
    if floor:
        record["points"] = feet_pts
        if head and len(head_pts) >= 4:
            record["head"] = {"version": 1, "points": head_pts}
    if head_range:
        record["head_range"] = head_range_calibration()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"  wrote simulated camera calibration ({len(record.get('points', []))} feet, "
          f"{len(record.get('head', {}).get('points', []))} head points"
          f"{', head-size ranging' if head_range else ''}) to {path}")


def use_real_layout():
    """Simulate the real boards at their positions in data/layouts/current.json."""
    root = Path(__file__).resolve().parents[1]
    boards = json.loads((root / "config" / "boards.json").read_text(encoding="utf-8"))["boards"]
    layout = json.loads((root / "data" / "layouts" / "current.json").read_text(encoding="utf-8"))
    xs = [p[0] for p in layout["polygon"]]
    ys = [p[1] for p in layout["polygon"]]
    ROOM.update(w=max(xs) - min(xs), h=max(ys) - min(ys))
    gains = {"A": 0.0, "B": 6.0, "C": 8.0}
    BOARDS.clear()
    for i, b in enumerate(boards):
        mac = b["base_mac"].lower()
        pos = layout["receivers"].get(mac)
        if not pos:
            print(f"  board {b['label']} ({mac}) isn't placed in the layout, skipped")
            continue
        is_ap = b["role"].upper() == "AP"
        # The soft-AP transmits from base MAC + 1; receivers use the base MAC.
        air = "%012x" % (int(mac, 16) + 1) if is_ap else mac
        BOARDS.append({"id": mac, "ifm": air, "role": "ap" if is_ap else "rx", "port": 6101 + i,
                       "x": pos["x"], "y": pos["y"], "g": gains.get(b["label"], 0.0)})
    BOARDS.sort(key=lambda b: b["role"] != "ap")
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    PHONES["p1"].update(x=cx, y=cy, nm="FakePerson", path=False)
    print(f"  room {ROOM['w'] / FT:.1f} x {ROOM['h'] / FT:.1f} ft, phone in the middle")


def main():
    parser = argparse.ArgumentParser(description="ActiasLuna board SIMULATOR (UDP 127.0.0.1:6101-6103, control 6100)")
    parser.add_argument("--real", action="store_true", help="simulate config/boards.json at their layout positions")
    parser.add_argument("--camera", nargs="?", const="127.0.0.1:5007", metavar="HOST:PORT",
                        help="also simulate the laptop camera, sending to the server's UDP port (default 127.0.0.1:5007)")
    parser.add_argument("--camera-cal", type=Path, metavar="PATH",
                        help="write the simulated camera's calibration here (e.g. data/camera/calibration.json)")
    args = parser.parse_args()
    if args.real:
        use_real_layout()
    if args.camera_cal:
        write_camera_calibration(args.camera_cal)
    if args.camera:
        host, port = args.camera.rsplit(":", 1)
        threading.Thread(target=run_camera, args=((host, int(port)),), daemon=True).start()
        print(f"  simulated camera -> {host}:{port}")
    for board in BOARDS:
        threading.Thread(target=run_board, args=(board,), daemon=True).start()
    threading.Thread(target=move_phones, daemon=True).start()
    print("SIMULATOR running: boards on 127.0.0.1:6101-6103, control on 127.0.0.1:6100", flush=True)
    control_loop()


if __name__ == "__main__":
    main()
