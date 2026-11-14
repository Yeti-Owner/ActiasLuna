"""Run one phone through the live tracker over a recorded session and print its path (feet).

Uncalibrated map (board-to-board links only, learned by a first pass over the same session), so it shows raw signal
behaviour rather than calibrated accuracy.
"""
import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import radiomap as rm  # noqa: E402
from recording import feed, load_session  # noqa: E402
from tracker import Tracker  # noqa: E402

FT = rm.FT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session")
    parser.add_argument("mac")
    parser.add_argument("layout", nargs="?", default=str(ROOT / "data" / "layouts" / "current.json"))
    args = parser.parse_args()
    mac = args.mac.lower().replace(":", "")
    layout = json.loads(Path(args.layout).read_text(encoding="utf-8"))
    lines = load_session(Path(args.session))[1]
    if not lines:
        sys.exit("nothing recorded in this session")
    t0, t1 = lines[0]["rt"], lines[-1]["rt"]

    learn = Tracker(lambda: {}, [])
    feed(learn, lines, t0, t1)
    radio = rm.RadioMap(layout, [], learn.links_dict())
    print("map:", radio.info())

    ctx = {"map": radio, "layout": layout}
    tr = Tracker(lambda: ctx, [])
    path = []

    def on_tick(t):
        ph = tr.phones.get(mac)
        if ph and ph.raw and (not path or path[-1][1] is not ph.raw):
            path.append((t - t0, ph.raw))

    feed(tr, lines, t0, t1, on_tick)
    if not path:
        sys.exit(f"no estimate for {mac} in this session (not on the AP's station list?)")
    printed = -1e9
    for t, est in path:
        if t - printed >= 5:
            printed = t
            print(f"{t:6.1f}s  x={est['x'] / FT:5.1f} ft  y={est['y'] / FT:5.1f} ft  unc={est['unc'] / FT:4.1f} ft")
    xs = [e["x"] for _, e in path]
    ys = [e["y"] for _, e in path]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    spread = math.sqrt(sum((x - mx) ** 2 + (y - my) ** 2 for x, y in zip(xs, ys)) / len(xs))
    print(f"spread (sd of estimates): {spread / FT:.2f} ft; mean ({mx / FT:.1f}, {my / FT:.1f}) ft")


if __name__ == "__main__":
    main()
