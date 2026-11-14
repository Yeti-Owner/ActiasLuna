"""Preview the decoy crowd / MAC phones placed inside a real scanned room — no hardware, no server.

Loads a room file (the one the 3D view uses) and runs the crowd in it, so you can check the decoys land inside
the room and off the furniture, in the same coordinates as the ESP32 markers.

    py tools/preview_room.py data/scan/sample/room.json
    py tools/preview_room.py path/to/your/room.json --count 12 --seconds 8

If the room file has a different shape than expected, this prints what it understood (size + furniture); paste me
that and your file and I'll match it exactly.
"""

import argparse
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))
from crowd import Crowd  # noqa: E402
from room import Room  # noqa: E402


def draw(room, decoys):
    cols, rows = 56, 20
    grid = [[" "] * cols for _ in range(rows)]
    gx = lambda x: min(max(int(x / room.w * (cols - 1)), 0), cols - 1)
    gy = lambda y: min(max(int((room.d - y) / room.d * (rows - 1)), 0), rows - 1)
    for o in room.objects:          # furniture footprint
        for xx in range(gx(o["x"] - o["w"] / 2), gx(o["x"] + o["w"] / 2) + 1):
            for yy in range(gy(o["y"] + o["d"] / 2), gy(o["y"] - o["d"] / 2) + 1):
                grid[yy][xx] = "."
    for dec in decoys:
        grid[gy(dec["y"])][gx(dec["x"])] = "o"
    border = "+" + "-" * cols + "+"
    return border + "\n" + "\n".join("|" + "".join(r) + "|" for r in grid) + "\n" + border


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("roomfile", help="path to room.json")
    ap.add_argument("--count", type=int, default=8)
    ap.add_argument("--seconds", type=float, default=8)
    args = ap.parse_args()

    try:
        room = Room.load(args.roomfile)
    except Exception as e:
        print(f"Could not read {args.roomfile} as a room: {e}")
        print("Paste me the file and I'll adjust the loader.")
        return 1

    print(f"Room '{room.name}': {room.w:.1f} x {room.d:.1f} m, {len(room.objects)} furniture items "
          + ", ".join(sorted({o['kind'] for o in room.objects})))
    crowd = Crowd(count=args.count)
    crowd.set(True)
    t0 = time.time()
    while time.time() - t0 < args.seconds:
        t = time.time() - t0
        crowd.tick(t, room, center=(room.w / 2, room.d / 2))
        os.system("cls" if os.name == "nt" else "clear")
        print(f"Decoys in '{room.name}'  (o = simulated decoy, . = furniture)   t={t:4.1f}s  decoys={args.count}")
        print(draw(room, crowd.snapshot()))
        print("Decoys stay inside the walls and off the furniture. Nothing is transmitted.")
        time.sleep(0.25)
    return 0


if __name__ == "__main__":
    sys.exit(main())
