"""Preview the simulated decoy crowd without the server, the Arduino, or any radios.

Steps crowd.py and prints the decoys' positions as a little text map, so you can see the effect and tune the
numbers before merging. The crowd is a display effect only; nothing is transmitted.

    py tools/sim_crowd.py
    py tools/sim_crowd.py --count 12 --room 6x4.5 --seconds 6
"""

import argparse
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))
from crowd import Crowd  # noqa: E402


def draw(w, d, decoys, center):
    cols, rows = 48, 18
    grid = [[" "] * cols for _ in range(rows)]
    plot = lambda x, y, ch: grid.__setitem__(
        min(int((d - y) / d * (rows - 1)), rows - 1),
        grid[min(int((d - y) / d * (rows - 1)), rows - 1)][:min(int(x / w * (cols - 1)), cols - 1)]
        + [ch] + grid[min(int((d - y) / d * (rows - 1)), rows - 1)][min(int(x / w * (cols - 1)), cols - 1) + 1:])
    for dec in decoys:
        if 0 <= dec["x"] <= w and 0 <= dec["y"] <= d:
            plot(dec["x"], dec["y"], "o")
    if center:
        plot(center[0], center[1], "@")
    top = "+" + "-" * cols + "+"
    return top + "\n" + "\n".join("|" + "".join(r) + "|" for r in grid) + "\n" + top


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--count", type=int, default=8)
    ap.add_argument("--room", default="6x4.5", help="WxD metres (default 6x4.5)")
    ap.add_argument("--seconds", type=float, default=8)
    args = ap.parse_args()
    w, d = (float(v) for v in args.room.lower().split("x"))
    center = (w / 2, d / 2)

    crowd = Crowd(count=args.count)
    crowd.set(True)
    t0 = time.time()
    while time.time() - t0 < args.seconds:
        t = time.time() - t0
        crowd.tick(t, (w, d), center=center)
        os.system("cls" if os.name == "nt" else "clear")
        print(f"Simulated decoy crowd (@ = real person, o = SIMULATED decoy)   t={t:4.1f}s  decoys={args.count}")
        print(draw(w, d, crowd.snapshot(), center))
        print("These decoys are a display effect. Nothing is transmitted; the Wi-Fi estimate is untouched.")
        time.sleep(0.2)


if __name__ == "__main__":
    main()
