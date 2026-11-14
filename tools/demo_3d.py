"""Moth 3D decoy demo: your scanned room + simulated decoy phones that toggle on/off with the button.

Loads your real room scan (the assimp2json file, default C:\\Users\\nicho\\Downloads\\11_13_2026.json), runs the
HONEST decoy crowd (server/crowd.py) inside its real footprint, and serves a 3D viewer in the browser. Flip the
crowd on and the SIMULATED phones appear scattered on the floor between the real walls; flip it off and they all
vanish. Nothing is ever transmitted over Wi-Fi -- the decoys are drawn here, in the view, and labelled simulated.

Three ways to flip it, all the same toggle:
  - click the ON / OFF button on the web page (no hardware needed -- good for testing tonight),
  - press the Uno Q's white button  (run tools/unoq_button_adb.py in another window; it sends the UDP toggle here),
  - send a UDP toggle yourself       (py tools/send_crowd.py --on 1   /   --on 0).

Run it:
    py tools/demo_3d.py
    py tools/demo_3d.py --room "C:\\path\\to\\11_13_2026.json"   # a different scan
then open the http://127.0.0.1:8000 link it prints.

Standard library only (plus a browser with internet, for three.js from a CDN).
"""

import argparse
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                     # so room_mesh imports even under `py -I`
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "server"))
from crowd import Crowd                      # noqa: E402  the honest, display-only decoy simulation
from room_mesh import RoomMesh               # noqa: E402
DEFAULT_ROOM = r"C:\Users\nicho\Downloads\11_13_2026.json"


class Demo:
    """Holds the loaded room and the crowd, and keeps the decoys moving. Thread-safe enough for this demo."""

    def __init__(self, room_mesh, count):
        self.mesh = room_mesh
        self.crowd = Crowd(count=count)
        self._lock = threading.Lock()
        self._footprint = room_mesh.footprint          # (w, d) on the floor
        self._room_json = json.dumps(room_mesh.to_json(), separators=(",", ":")).encode()

    def room_bytes(self):
        return self._room_json

    def set(self, on):
        with self._lock:
            self.crowd.set(on)

    def toggle(self):
        with self._lock:
            self.crowd.toggle()
            return self.crowd.state.value == "on"

    def tick(self, t):
        with self._lock:
            self.crowd.tick(t, self._footprint)

    def state(self):
        with self._lock:
            on = self.crowd.state.value == "on"
            decoys = []
            for d in self.crowd.snapshot():
                wx, wy, wz = self.mesh.room_to_world(d["x"], d["y"])
                decoys.append({"mac": d["mac"], "x": round(wx, 3), "y": round(wy, 3), "z": round(wz, 3)})
        return {"on": on, "count": len(decoys), "decoys": decoys, "sim": True}


def ticker(demo, stop):
    while not stop.is_set():
        demo.tick(time.monotonic())
        time.sleep(0.1)


def udp_listener(demo, port, stop):
    """Receive the crowd toggle from the white button (tools/unoq_button_adb.py) or tools/send_crowd.py."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as e:
        print(f"  (UDP button port {port} unavailable: {e}; the web button still works)")
        return
    sock.settimeout(0.5)
    print(f"  listening for the white button on UDP {port}")
    while not stop.is_set():
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
        if demo.crowd.handle_packet(p):
            print(f"  button -> crowd {'ON' if p.get('on') else 'off'}")


def make_handler(demo):
    page_path = os.path.join(HERE, "demo_3d.html")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):            # keep the console clean
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                with open(page_path, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/room.json":
                self._send(200, demo.room_bytes(), "application/json")
            elif path == "/state.json":
                self._send(200, json.dumps(demo.state()).encode(), "application/json")
            elif path == "/toggle":
                on = demo.toggle()
                self._send(200, json.dumps({"on": on}).encode(), "application/json")
            elif path in ("/on", "/off"):
                demo.set(path == "/on")
                self._send(200, json.dumps({"on": path == "/on"}).encode(), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--room", default=DEFAULT_ROOM, help="scanned room file (assimp2json)")
    ap.add_argument("--host", default="127.0.0.1", help="web host (default 127.0.0.1)")
    ap.add_argument("--http-port", type=int, default=8000, help="web page port (default 8000)")
    ap.add_argument("--udp-port", type=int, default=5005, help="button UDP port (default 5005)")
    ap.add_argument("--count", type=int, default=8, help="number of decoy phones when on (default 8)")
    args = ap.parse_args()

    if not os.path.exists(args.room):
        sys.exit(f"Room file not found: {args.room}\nPass --room \"C:\\path\\to\\11_13_2026.json\".")

    print(f"Loading room scan: {args.room}")
    mesh = RoomMesh.load(args.room)
    w, d = mesh.footprint
    print(f"  {len(mesh.positions)//3} verts, {len(mesh.indices)//3} triangles, "
          f"footprint {w:.1f} x {d:.1f} m, floor Y {mesh.floor_y:.2f}")
    demo = Demo(mesh, count=args.count)

    stop = threading.Event()
    threading.Thread(target=ticker, args=(demo, stop), daemon=True).start()
    threading.Thread(target=udp_listener, args=(demo, args.udp_port, stop), daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.http_port), make_handler(demo))
    url = f"http://{args.host}:{args.http_port}"
    print(f"\n  Open this in your browser:  {url}\n")
    print("  Click ON on the page, press the white button, or run send_crowd.py -- decoys appear/disappear.")
    print("  Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.shutdown()


if __name__ == "__main__":
    main()
