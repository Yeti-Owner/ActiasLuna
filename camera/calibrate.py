"""Calibrate the laptop camera: teach it which image point is which spot on the floor.

    python camera/calibrate.py
    python camera/calibrate.py --out data/camera/calibration.json --detector hog

Mark 4 or more spots on the floor at known positions in the room's layout coordinates (metres), spread out
across the area the camera sees, and not all in a line. Then, in the window:

    space        stand on a mark and press space: records your detected feet AND head top, so the camera can place
                 people by their head when a desk hides their feet. Stand up straight; your head height becomes
                 the reference everyone is compared with.
    click        a floor mark in the image, then type its x y in the terminal (feet mapping only)
    u            undo the last mark
    s            save (needs 4+ feet points; 4+ head points add the head mapping) and quit
    q / Esc      quit without saving

Only point coordinates are saved, never images.
"""

import argparse
import json
from pathlib import Path

import homography
import luna_camera

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "camera" / "calibration.json"   # data/ is git-ignored
WINDOW = "luna camera calibration (local only)"


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


def pairs(marks, key):
    """(image points, floor points) of the marks that have an image point under key ("feet" or "head")."""
    got = [(m[key], m["xy"]) for m in marks if m.get(key)]
    return [g[0] for g in got], [g[1] for g in got]


def fit_report(name, img, flo):
    """One line on how well a mapping fits, and its H (None when it can't be fitted yet)."""
    if len(img) < homography.MIN_POINTS:
        return f"{name}: {len(img)}/{homography.MIN_POINTS} points", None
    try:
        H = homography.fit(img, flo)
    except ValueError as e:
        return f"{name}: {e}", None
    loo = homography.leave_one_out_error(img, flo)
    line = f"{name}: {len(img)} points, fit error {homography.rms_error(H, img, flo):.2f} m"
    return line + (f", leave-one-out {loo:.2f} m" if loo is not None else " (a 5th gives an honest error)"), H


def report(marks):
    for name, key in (("feet", "feet"), ("head", "head")):
        print("  " + fit_report(name, *pairs(marks, key))[0])


def save(marks, size, out: Path) -> bool:
    feet_img, feet_flo = pairs(marks, "feet")
    _, H = fit_report("feet", feet_img, feet_flo)
    if H is None:
        print(f"  can't save: need {homography.MIN_POINTS}+ marks with the feet in view (not in a line)")
        return False
    record = homography.to_dict(H, feet_img, feet_flo, size)
    head_img, head_flo = pairs(marks, "head")
    _, Hh = fit_report("head", head_img, head_flo)
    if Hh is not None:
        record["head"] = homography.to_dict(Hh, head_img, head_flo)
    else:
        print("  saving without the head mapping (needs 4+ marks taken with space)")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2))
    print(f"saved {out}")
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--detector", choices=list(luna_camera.DETECTORS), default="hybrid")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    import cv2

    cap = luna_camera.open_camera(cv2, args.camera)
    detector = luna_camera.DETECTORS[args.detector](cv2)
    marks = []      # {"xy": floor (x, y), "feet": image (u, v) or None, "head": image (u, v) or None}
    clicked = []
    size = None

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN and size:
            clicked.append((x / size[0], y / size[1]))

    cv2.namedWindow(WINDOW)
    cv2.setMouseCallback(WINDOW, on_mouse)
    print(__doc__.split("\n\n", 2)[2])
    try:
        while True:
            frame = luna_camera.read_frame(cv2, cap)
            if frame is None:
                continue
            size = (frame.shape[1], frame.shape[0])
            dets = luna_camera.detect(detector, frame)
            luna_camera.draw(cv2, frame, dets)
            for m in marks:
                label = f"{m['xy'][0]:g},{m['xy'][1]:g}"
                for key, colour in (("feet", (0, 0, 255)), ("head", (255, 120, 0))):
                    if m.get(key):
                        p = (int(m[key][0] * size[0]), int(m[key][1] * size[1]))
                        cv2.drawMarker(frame, p, colour, cv2.MARKER_CROSS, 14, 2)
                        cv2.putText(frame, label, (p[0] + 6, p[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1)
            cv2.imshow(WINDOW, frame)
            key = cv2.waitKey(1) & 0xFF

            mark = None
            if clicked:
                mark = {"feet": clicked.pop(), "head": None}
                clicked.clear()
            elif key == ord(" "):
                if len(dets) != 1:
                    print(f"  need exactly one person in view, saw {len(dets)}")
                else:
                    d = dets[0]
                    mark = {"feet": (d["u"], d["v"]) if "u" in d else None,
                            "head": (d["hu"], d["hv"]) if "hu" in d else None}
                    if not mark["feet"]:
                        print("  feet not in view: this mark only trains the head mapping")
            if mark:
                seen = " + ".join(k for k in ("feet", "head") if mark[k])
                xy = ask_xy(f"mark {len(marks) + 1} ({seen}); floor x y in metres: ")
                if xy:
                    mark["xy"] = xy
                    marks.append(mark)
                    report(marks)
            elif key == ord("u") and marks:
                marks.pop()
                report(marks)
            elif key == ord("s"):
                if save(marks, size, args.out):
                    break
            elif key in (ord("q"), 27):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
