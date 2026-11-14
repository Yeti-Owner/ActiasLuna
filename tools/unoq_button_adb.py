"""Bridge the Uno Q's on-board WHITE button to the Moth crowd — over the USB cable, no hotspot or extra parts.

The white button is the power key on the board's Linux side, so no Arduino sketch can read it. This runs on the
LAPTOP: it reaches the board over adb (USB), streams button presses, and sends a crowd on/off toggle to the Moth
server on each press. The decoys are drawn by the server; nothing is transmitted over Wi-Fi.

    py tools/unoq_button_adb.py                       # server on 127.0.0.1:5005
    py tools/unoq_button_adb.py --server 192.168.4.2  # server elsewhere on the network
    py tools/unoq_button_adb.py --device /dev/input/event1   # which button (default: the white/power key)

Needs the Uno Q plugged in by USB (the same cable you flash with). adb is found automatically from the Arduino
install, or pass --adb <path to adb.exe>.
"""

import argparse
import glob
import json
import os
import shutil
import socket
import subprocess
import sys

BOARD_READER_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unoq_board_button_reader.py")
BOARD_READER_REMOTE = "/tmp/moth_btn_reader.py"


def find_adb(explicit):
    if explicit:
        return explicit
    found = shutil.which("adb")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    patterns = [
        os.path.join(local, "Arduino15", "packages", "arduino", "tools", "adb", "*", "adb.exe"),
        os.path.join(local, "Android", "Sdk", "platform-tools", "adb.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Android", "android-sdk", "platform-tools", "adb.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Android", "android-sdk", "platform-tools", "adb.exe"),
    ]
    for pattern in patterns:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--server", default="127.0.0.1", help="Moth server IP (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=5005, help="server UDP port (default 5005)")
    ap.add_argument("--device", default="/dev/input/event1", help="board input device (default the white/power key)")
    ap.add_argument("--adb", help="path to adb.exe (default: auto-detect from Arduino)")
    args = ap.parse_args()
    adb = find_adb(args.adb)
    if not adb:
        sys.exit("adb not found. Install Android platform-tools (winget install Google.PlatformTools) "
                 "or pass --adb <path to adb.exe>.")

    check = subprocess.run([adb, "get-state"], capture_output=True, text=True)
    if "device" not in check.stdout:
        sys.exit(f"Board not reachable over adb ({check.stdout.strip() or check.stderr.strip()}). "
                 "Plug the Uno Q in by USB.")
    subprocess.run([adb, "push", BOARD_READER_LOCAL, BOARD_READER_REMOTE], check=True,
                   stdout=subprocess.DEVNULL)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    proc = subprocess.Popen([adb, "shell", f"python3 -u {BOARD_READER_REMOTE} {args.device}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    print(f"Ready. Press the white button to toggle the crowd (-> {args.server}:{args.port}). Ctrl+C to stop.", flush=True)
    try:
        for line in proc.stdout:
            if "PRESS" in line:
                sock.sendto(json.dumps({"t": "crowd", "toggle": 1}).encode(), (args.server, args.port))
                print("white button -> crowd toggled", flush=True)
            elif line.strip():
                print(f"board: {line.rstrip()}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
