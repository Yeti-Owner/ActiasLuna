"""Set up head-size ranging: the camera judges how far away someone is from how big their head looks.

    python camera/head_setup.py --cam 0.2 0.3
    python camera/head_setup.py --cam 0.2 0.3 --out data/camera/calibration.json

--cam is where the laptop camera sits on the floor plan, in metres from the layout's (0, 0) corner. Then stand on 2-3
spots at known positions in view, facing the camera: one left of view, one in the middle, one right, at different
distances. In the window:

    space        on a spot: watches you for a moment (stand still, face the camera), then asks the spot's x y
    u            undo the last spot
    s            save and quit (needs 2+ spots at different left/right positions)
    q / Esc      quit without saving

It works out which way the camera faces and how wide its view is, so neither needs measuring. It is saved under
"head_range" in the camera calibration file, next to any floor calibration from calibrate.py (both can be used:
feet when visible, head size otherwise). Only numbers are saved, never images.
"""

import argparse
import json
import math
from pathlib import Path

import headrange
import luna_camera

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "camera" / "calibration.json"   # data/ is git-ignored
WINDOW = "luna head setup (local only)"
WATCH_FRAMES = 12   # frames averaged per spot, so one jittery box doesn't skew it


def ask_xy(prompt):
    while True:
        raw = input(prompt).replace(",", " ").split()
        if not raw:
            return None
        try:
            x, y = (float(c) for c in raw[:2])
            return x, y
        except ValueError:
            print("  type two numbers in metres, e.g. 1.5 2.0 (blank to cancel)")


def median(values):
    values = sorted(values)
    return values[len(values) // 2]


def frame_obs(detectors, frame):
    """{kind: (size, x_off)} from one frame, using each kind only when exactly one such box was found."""
    h, w = frame.shape[:2]
    out = {}
    for det in detectors:
        found = {}
        for box, score, kind in det(frame):
            rec = luna_camera.person_record(box, w, h, score, kind)
            obs = headrange.observe(rec, w, h) if rec else None
            if obs:
                found.setdefault(obs[0], []).append(obs[1:])
        for kind, items in found.items():
            if len(items) == 1:
                out[kind] = items[0]
    return out


def report(cam, samples):
    if len(samples) < 2:
        print(f"  {len(samples)} spot(s); need 2+ (left and right of view)")
        return None
    try:
        cal = headrange.solve(cam, samples)
    except ValueError as e:
        print(f"  {e}")
        return None
    print(f"  camera faces {math.degrees(cal['yaw']):.0f} deg, view about {cal['hfov_deg']:.0f} deg wide, "
          f"direction error {cal['rms_deg']:.1f} deg; sizes learned for: {', '.join(sorted(cal['k']))}")
    for s in samples:   # how far off each spot would be placed
        errs = []
        for kind, (size, x_off) in s["obs"].items():
            got = headrange.locate(cal, kind, size, x_off)
            if got:
                errs.append(math.hypot(got[0][0] - s["xy"][0], got[0][1] - s["xy"][1]))
        if errs:
            print(f"    spot ({s['xy'][0]:g}, {s['xy'][1]:g}): placed within {max(errs):.2f} m")
    return cal


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cam", type=float, nargs=2, required=True, metavar=("X", "Y"),
                    help="camera position on the floor plan, metres from the layout's (0, 0) corner")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    import cv2

    cap = luna_camera.open_camera(cv2, args.camera)
    # Each detector separately, so every kind the live camera may report gets its own size constant.
    detectors = [luna_camera.HogDetector(cv2), luna_camera.UpperDetector(cv2)]
    show = luna_camera.HybridDetector(cv2)
    samples, watching = [], None
    cv2.namedWindow(WINDOW)
    print(__doc__.split("\n\n", 2)[2])
    try:
        while True:
            frame = luna_camera.read_frame(cv2, cap)
            if frame is None:
                continue
            if watching is not None:
                obs = frame_obs(detectors, frame)
                if obs:
                    watching.append(obs)
            dets = luna_camera.detect(show, frame)
            luna_camera.draw(cv2, frame, dets)
            if watching is not None:
                cv2.putText(frame, f"hold still... {len(watching)}/{WATCH_FRAMES}", (10, 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
            cv2.imshow(WINDOW, frame)
            key = cv2.waitKey(1) & 0xFF

            if watching is not None and len(watching) >= WATCH_FRAMES:
                kinds = {k for o in watching for k in o}
                obs = {k: (median([o[k][0] for o in watching if k in o]), median([o[k][1] for o in watching if k in o]))
                       for k in kinds if sum(k in o for o in watching) >= WATCH_FRAMES // 2}
                watching = None
                if not obs:
                    print("  couldn't see you steadily: face the camera, alone in view, and try again")
                    continue
                xy = ask_xy(f"spot {len(samples) + 1} (seen as: {', '.join(sorted(obs))}); its x y in metres: ")
                if xy:
                    samples.append({"xy": xy, "obs": obs})
                    report(args.cam, samples)
            elif key == ord(" ") and watching is None:
                watching = []
            elif key == ord("u") and samples:
                samples.pop()
                report(args.cam, samples)
            elif key == ord("s"):
                cal = report(args.cam, samples)
                if cal is None:
                    print("  can't save yet")
                    continue
                cal["samples"] = [{"xy": list(s["xy"]), "obs": {k: list(v) for k, v in s["obs"].items()}}
                                  for s in samples]
                try:
                    record = json.loads(args.out.read_text())
                except (OSError, ValueError):
                    record = {"version": 1}
                record["head_range"] = cal
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps(record, indent=2))
                print(f"saved {args.out}" + ("" if record.get("points") else
                      " (head size only; add calibrate.py's floor marks too if you want feet-level precision)"))
                break
            elif key in (ord("q"), 27):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
