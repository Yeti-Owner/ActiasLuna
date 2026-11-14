"""Reads the Moth crowd button over USB serial and forwards its messages to the Moth server as UDP.

The button only toggles the SIMULATED decoy crowd (a display effect); nothing is transmitted over Wi-Fi.

    py tools/unoq_crowd_relay.py --list                      # show serial ports
    py tools/unoq_crowd_relay.py --port COM5                 # relay to 127.0.0.1:5005
    py tools/unoq_crowd_relay.py --port COM5 --host 192.168.4.2 --udp 5005

Needs pyserial:  py -m pip install pyserial
"""

import argparse
import json
import socket
import sys
import time


def list_ports():
    try:
        from serial.tools import list_ports
    except ImportError:
        sys.exit("pyserial not installed. Run: py -m pip install pyserial")
    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found.")
    for p in ports:
        print(f"{p.device}\t{p.description}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    ap.add_argument("--port", help="serial port, e.g. COM5")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--host", default="127.0.0.1", help="Moth server IP (default 127.0.0.1)")
    ap.add_argument("--udp", type=int, default=5005, help="server UDP port (default 5005)")
    args = ap.parse_args()

    if args.list or not args.port:
        list_ports()
        if not args.port:
            return

    try:
        import serial
    except ImportError:
        sys.exit("pyserial not installed. Run: py -m pip install pyserial")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"Relaying {args.port} @ {args.baud} -> {args.host}:{args.udp}. Ctrl+C to stop.")
    while True:
        try:
            with serial.Serial(args.port, args.baud, timeout=1) as ser:
                for raw in ser:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        print("  (ignored non-JSON:", line, ")")
                        continue
                    if msg.get("t") == "crowd":
                        sock.sendto(json.dumps({"t": "crowd", "on": int(bool(msg.get("on")))}).encode(),
                                    (args.host, args.udp))
                        print(f"{time.strftime('%H:%M:%S')} crowd {'ON' if msg.get('on') else 'off'} -> sent")
                    else:
                        print(f"{time.strftime('%H:%M:%S')} {line}")
        except KeyboardInterrupt:
            print("\nStopped.")
            return
        except Exception as e:   # serial unplugged etc: wait and retry
            print(f"Serial error: {e}. Retrying in 2 s…")
            time.sleep(2)


if __name__ == "__main__":
    main()
