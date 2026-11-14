"""One-command Moth demo launcher -- starts everything you need in a single window.

Because the demo Wi-Fi ("DEMO-DONT JOIN") has no internet, set this up and try the --sim run BEFORE you join it;
everything here works offline afterward (the dashboard's 3D view uses bundled three.js, and the white button goes
over the USB cable, not Wi-Fi).

  py tools/demo_run.py --sim             practice run: simulated boards + server, NO hardware needed
  py tools/demo_run.py --sim --button    same, plus the Uno Q white button (board plugged in by USB)
  py tools/demo_run.py                    REAL run: server only, for the actual ESP32 boards on the demo Wi-Fi
  py tools/demo_run.py --button           REAL run with the white button too

Then open   http://127.0.0.1:8080   in your browser.
Press Ctrl+C in this window to stop everything at once.

The decoys are simulated and display-only -- they appear in the view, nothing is transmitted over the air.
"""

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable or "py"
SIM_TARGETS = "127.0.0.1:6101,127.0.0.1:6102,127.0.0.1:6103"


def main():
    ap = argparse.ArgumentParser(description="Start the Moth demo (server + optional simulator + optional button).")
    ap.add_argument("--sim", action="store_true", help="also run the board simulator (no hardware needed)")
    ap.add_argument("--button", action="store_true", help="also bridge the Uno Q white button over USB")
    ap.add_argument("--port", type=int, default=8080, help="dashboard port (default 8080)")
    args = ap.parse_args()

    procs = []   # (name, Popen)

    def start(name, cmd, cwd):
        print(f"  starting {name} ...")
        p = subprocess.Popen(cmd, cwd=cwd)
        procs.append((name, p))
        return p

    print("=" * 68)
    print("  MOTH DEMO")
    print("=" * 68)

    # 1) the board simulator (optional) -- must come up before the server subscribes
    server_cmd = [PY, os.path.join("server", "luna_server.py"), "--port", str(args.port), "--no-record"]
    if args.sim:
        start("board simulator", [PY, os.path.join("tools", "sim_nodes.py")], ROOT)
        server_cmd += ["--targets", SIM_TARGETS]
        time.sleep(1.5)
    else:
        print("  REAL mode: no simulator. The ESP32 boards must be powered and on the demo Wi-Fi,")
        print("  and the laptop must be on that same network, or the map stays empty.")

    # 2) the server (always)
    start("server", server_cmd, ROOT)
    time.sleep(2.0)

    # 3) the white-button bridge (optional) -- reaches the Uno Q over USB/adb
    if args.button:
        start("white-button bridge", [PY, os.path.join("tools", "unoq_button_adb.py")], ROOT)

    print("-" * 68)
    print(f"  Open the dashboard:   http://127.0.0.1:{args.port}/")
    if args.sim:
        print("  You should see 3 boards and a moving SimPhone within a few seconds.")
    if args.button:
        print("  Press the Uno Q white button to show/hide the simulated decoys.")
    else:
        print("  No --button: toggle decoys from the browser by visiting /api/crowd, or add --button.")
    print("  Press Ctrl+C here to stop everything.")
    print("-" * 68)

    try:
        while True:
            time.sleep(0.5)
            for name, p in procs:
                if p.poll() is not None:
                    print(f"  [{name}] exited (code {p.returncode}).")
                    raise KeyboardInterrupt
    except KeyboardInterrupt:
        print("\n  stopping ...")
    finally:
        for name, p in reversed(procs):
            if p.poll() is None:
                p.terminate()
        for name, p in reversed(procs):
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        print("  all stopped.")


if __name__ == "__main__":
    main()
