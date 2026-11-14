#!/usr/bin/env python3
"""Moth crowd button — reads the Uno Q's on-board WHITE button and tells the server to toggle the decoy crowd.

The white button is wired to the Uno Q's Linux (Qualcomm) side, not the microcontroller, so it can't be read from
an Arduino IDE sketch. This runs on the board's Linux side instead and sends the crowd message straight to the Moth
server over the hotspot — no USB relay needed. The decoys are drawn by the server; nothing is transmitted over Wi-Fi.

WHERE THIS RUNS: on the Uno Q itself (its Debian Linux), via the Arduino App Lab terminal or an SSH/adb shell —
NOT on your Windows laptop and NOT in the Arduino IDE.

FIRST, find the button's input device:
    python3 unoq_button_linux.py --list
Look for a line whose name mentions "button"/"keys"/"gpio". Then run (auto-detect usually just works):
    python3 unoq_button_linux.py --server <laptop-IP-on-hotspot> --port 5005
    python3 unoq_button_linux.py --server 192.168.4.2 --device /dev/input/event3   # if auto-detect picks wrong
If you get "Permission denied" reading the device, run it with sudo.

Standard library only (no evdev needed). The button is read as a Linux input event (the gpio-keys driver).
"""

import argparse
import glob
import json
import socket
import struct
import sys

EV_KEY = 0x01
EVENT_FMT = "@llHHi"                       # input_event: time(2 longs), type u16, code u16, value s32
EVENT_SIZE = struct.calcsize(EVENT_FMT)    # 24 on 64-bit, 16 on 32-bit — auto-adapts
BUTTONISH = ("button", "keys", "gpio", "pwr", "power")


def input_devices():
    """Parse /proc/bus/input/devices -> list of {name, event, keys} (keys=True if it reports key events)."""
    devs, cur = [], {}
    try:
        with open("/proc/bus/input/devices", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return devs
    for line in lines + ["\n"]:
        if line.startswith("N: Name="):
            cur = {"name": line.split("=", 1)[1].strip().strip('"'), "event": None, "keys": False}
        elif line.startswith("H: Handlers=") and cur:
            for h in line.split("=", 1)[1].split():
                if h.startswith("event"):
                    cur["event"] = "/dev/input/" + h
        elif line.startswith("B: EV=") and cur:
            try:
                cur["keys"] = bool(int(line.split("=", 1)[1], 16) & (1 << EV_KEY))
            except ValueError:
                pass
        elif not line.strip() and cur.get("event"):
            devs.append(cur)
            cur = {}
    return devs


def pick_device(devs):
    for d in devs:                                   # prefer an obvious button/keys device
        if d["keys"] and any(k in d["name"].lower() for k in BUTTONISH):
            return d
    for d in devs:                                   # else the first thing that emits key events
        if d["keys"]:
            return d
    return None


def do_list():
    devs = input_devices()
    if not devs:
        print("No input devices found (is this running on the Uno Q's Linux side?).")
    for d in devs:
        mark = "  <- likely the button" if d["keys"] and any(k in d["name"].lower() for k in BUTTONISH) else ""
        print(f"{d['event']:<20} keys={'yes' if d['keys'] else 'no ':<3}  {d['name']}{mark}")
    chips = sorted(glob.glob("/dev/gpiochip*"))
    if chips:
        print("\nGPIO chips (fallback route, needs libgpiod): " + ", ".join(chips))
        print("If no input device works, on the board try:  gpiodetect  and  gpioinfo")


def run(device, host, port):
    try:
        fd = open(device, "rb", buffering=0)
    except PermissionError:
        sys.exit(f"Permission denied opening {device}. Re-run with sudo.")
    except OSError as e:
        sys.exit(f"Could not open {device}: {e}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    crowd_on = False
    print(f"Reading button on {device}; sending crowd toggles to {host}:{port}. Ctrl+C to stop.")
    with fd:
        while True:
            data = fd.read(EVENT_SIZE)
            if not data or len(data) < EVENT_SIZE:
                continue
            _sec, _usec, etype, _code, value = struct.unpack(EVENT_FMT, data)
            if etype == EV_KEY and value == 1:        # key down = a fresh press
                crowd_on = not crowd_on
                sock.sendto(json.dumps({"t": "crowd", "on": int(crowd_on)}).encode(), (host, port))
                print(f"button pressed -> crowd {'ON' if crowd_on else 'off'} sent")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="list input devices and exit")
    ap.add_argument("--server", help="Moth server IP on the hotspot (the laptop)")
    ap.add_argument("--port", type=int, default=5005, help="server UDP port (default 5005)")
    ap.add_argument("--device", help="input device, e.g. /dev/input/event3 (default: auto-detect)")
    args = ap.parse_args()

    if args.list:
        do_list()
        return
    if not args.server:
        sys.exit("Give --server <laptop IP on the hotspot>. Use --list first to check the button device.")

    device = args.device or (pick_device(input_devices()) or {}).get("event")
    if not device:
        sys.exit("Couldn't auto-detect the button. Run --list and pass the right one with --device.")
    run(device, args.server, args.port)


if __name__ == "__main__":
    main()
