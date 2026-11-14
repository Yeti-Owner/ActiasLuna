"""Send crowd on/off messages over UDP — a stand-in for the Arduino button while there's no button.

    py tools/send_crowd.py --on 1                 # turn the decoy crowd on
    py tools/send_crowd.py --on 0                 # turn it off
    py tools/send_crowd.py --demo                 # on, wait 5 s, off (for a quick end-to-end check)
    py tools/send_crowd.py --on 1 --host 192.168.4.2 --port 5005
"""

import argparse
import json
import socket
import time


def send(sock, host, port, on):
    sock.sendto(json.dumps({"t": "crowd", "on": int(bool(on))}).encode(), (host, port))
    print(f"sent crowd on={int(bool(on))} to {host}:{port}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5005)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--on", type=int, choices=[0, 1], help="1 = crowd on, 0 = off")
    g.add_argument("--demo", action="store_true", help="on, wait 5 s, then off")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if args.demo:
        send(sock, args.host, args.port, 1)
        time.sleep(5)
        send(sock, args.host, args.port, 0)
    else:
        send(sock, args.host, args.port, args.on)


if __name__ == "__main__":
    main()
