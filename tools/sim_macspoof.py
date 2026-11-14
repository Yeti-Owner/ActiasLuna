"""Show how fake and randomized MAC addresses defeat Wi-Fi tracking — a terminal demo, no hardware, no radios.

Everything here is simulated and labelled; nothing is transmitted. It makes the privacy point behind Moth:
a tracker needs a stable address, and decoys + MAC randomization take that away.

    py tools/sim_macspoof.py                      # decoys + target randomizing its MAC every 4 s
    py tools/sim_macspoof.py --rotate 0           # randomization OFF, to compare
    py tools/sim_macspoof.py --decoys 15 --rotate 3 --seconds 20
"""

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))
from macsim import MacSim  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--decoys", type=int, default=8)
    ap.add_argument("--rotate", type=float, default=4.0, help="seconds between target MAC changes, 0 = off")
    ap.add_argument("--room", default="6x4.5", help="WxD metres")
    ap.add_argument("--seconds", type=float, default=16)
    args = ap.parse_args()
    w, d = (float(v) for v in args.room.lower().split("x"))

    sim = MacSim(decoys=args.decoys, rotate_s=args.rotate)
    seen = set()
    last_mac = None
    snap = None
    t0 = time.time()
    print(f"Simulated target + {args.decoys} decoys. "
          + ("Target MAC rotates every %gs." % args.rotate if args.rotate else "Randomization OFF.") + "\n")
    while time.time() - t0 < args.seconds:
        t = time.time() - t0
        sim.tick(t, (w, d))
        snap = sim.snapshot()
        seen.add(snap["target"]["mac"])
        if snap["target"]["mac"] != last_mac:
            last_mac = snap["target"]["mac"]
            if len(seen) > 1:
                print(f"  t={t:4.1f}s  target address changed -> {last_mac}   (trail reset; {len(seen)} identities so far)")
        line = (f"\r  target={last_mac}  among {snap['candidates']} addresses  "
                f"distinct-target-identities={len(seen)}   ")
        print(line, end="", flush=True)
        time.sleep(0.25)
    print("\n\n" + sim.explain())
    print(f"A tracker saw {len(seen)} different 'devices' for one person over {args.seconds:g}s "
          f"and {snap['candidates']} addresses at once. That is why continuous tracking fails.")


if __name__ == "__main__":
    main()
