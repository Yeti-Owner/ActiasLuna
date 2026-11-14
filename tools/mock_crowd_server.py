"""A stand-in Moth server for testing just the crowd part, end to end, with no team server.

It uses server/crowd.py exactly the way the real tracker will: it listens on the server UDP port, feeds every
packet through Crowd.handle_packet, ticks the crowd on a timer, and prints the snapshot. Point the Arduino relay
(or tools/send_crowd.py) at it and watch the decoys turn on and off.

    py tools/mock_crowd_server.py                       # listen on 0.0.0.0:5005
    py tools/mock_crowd_server.py --port 5005 --count 10 --room 6x4.5

Nothing here transmits over Wi-Fi; the decoys are a display effect, the same as in the real dashboard.
"""

import argparse
import json
import pathlib
import socket
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))
from crowd import Crowd  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=5005)
    ap.add_argument("--room", default="6x4.5", help="room size WxD metres (default 6x4.5)")
    ap.add_argument("--count", type=int, default=8)
    ap.add_argument("--rate", type=float, default=4.0, help="crowd ticks per second (default 4)")
    args = ap.parse_args()
    w, d = (float(v) for v in args.room.lower().split("x"))
    center = (w / 2, d / 2)

    crowd = Crowd(count=args.count)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", args.port))
    sock.settimeout(1.0 / max(args.rate, 1))

    print(f"Mock crowd server on UDP {args.port}, room {w}x{d} m, {args.count} decoys. Ctrl+C to stop.")
    print("Waiting for crowd messages (e.g. from tools/send_crowd.py or the Arduino relay)…")
    last_state = None
    start = time.monotonic()
    try:
        while True:
            try:
                data, (ip, _) = sock.recvfrom(65535)
                try:
                    p = json.loads(data)
                except ValueError:
                    continue
                if crowd.handle_packet(p):
                    print(f"  {time.strftime('%H:%M:%S')} from {ip}: crowd -> {p.get('on')}")
                # A real server would also route tel/sta/etc here; we only care about crowd.
            except socket.timeout:
                pass

            now = time.monotonic() - start
            crowd.tick(now, (w, d), center=center)
            decoys = crowd.snapshot()
            if crowd.state.value != last_state:
                last_state = crowd.state.value
                print(f"  >> crowd is now {last_state.upper()} ({len(decoys)} decoys)")
            if decoys:
                preview = "  ".join(f"({x['x']:.1f},{x['y']:.1f})" for x in decoys[:4])
                print(f"\r  decoys={len(decoys)}  {preview} ...   ", end="", flush=True)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
