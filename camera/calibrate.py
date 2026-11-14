"""Calibrate the laptop camera: teach it which image point is which spot on the floor.

    python camera/calibrate.py
    python camera/calibrate.py --out data/camera/calibration.json --detector hog

Mark 4 or more spots on the floor at known positions in the room's layout coordinates (metres), spread out
across the area the camera sees, and not all in a line. Then, in the window:

    click        a floor mark in the image, then type its x y in the terminal
    space        stand on a mark and press space: uses your detected foot point (matches how tracking measures)
    u            undo the last point
    s            save (needs 4+ points) and quit
    q / Esc      quit without saving

Only point coordinates are saved, never images.
"""

import argparse
import json
from pathlib import Path

import homography
import moth_camera

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "camera" / "calibration.json"   # data/ is git-ignored
WINDOW = "moth camera calibration (local only)"


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


def report(img, flo):
    if len(img) < homography.MIN_POINTS:
        print(f"  {len(img)} point(s); need {homography.MIN_POINTS}")
        return
    try:
        H = homography.fit(img, flo)
    except ValueError as e:
        print(f"  {e}")
        return
    loo = homography.leave_one_out_error(img, flo)
    msg = f"  {len(img)} points, fit error {homography.rms_error(H, img, flo):.2f} m"
    print(msg + (f", leave-one-out {loo:.2f} m" if loo is not None else " (add a 5th for an honest error figure)"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--detector", choices=sorted(moth_camera.DETECTORS), default="hog")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    import cv2

    cap = moth_camera.open_camera(cv2, args.camera)
    detector = moth_camera.DETECTORS[args.detector](cv2)
    img, flo = [], []
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
            frame = moth_camera.read_frame(cv2, cap)
            if frame is None:
                continue
            size = (frame.shape[1], frame.shape[0])
            dets = moth_camera.detect(detector, frame)
            moth_camera.draw(cv2, frame, dets)
            for i, (u, v) in enumerate(img):
                p = (int(u * size[0]), int(v * size[1]))
                cv2.drawMarker(frame, p, (255, 120, 0), cv2.MARKER_CROSS, 14, 2)
                cv2.putText(frame, f"{flo[i][0]:g},{flo[i][1]:g}", (p[0] + 6, p[1] - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 120, 0), 1)
            cv2.imshow(WINDOW, frame)
            key = cv2.waitKey(1) & 0xFF

            point = None
            if clicked:
                point = clicked.pop()
                clicked.clear()
            elif key == ord(" "):
                if len(dets) != 1:
                    print(f"  need exactly one person in view, saw {len(dets)}")
                else:
                    point = (dets[0]["u"], dets[0]["v"])
            if point:
                xy = ask_xy(f"point {len(img) + 1} at image ({point[0]:.3f}, {point[1]:.3f}); floor x y in metres: ")
                if xy:
                    img.append(point)
                    flo.append(xy)
                    report(img, flo)
            elif key == ord("u") and img:
                img.pop()
                flo.pop()
                report(img, flo)
            elif key == ord("s"):
                try:
                    H = homography.fit(img, flo)
                except ValueError as e:
                    print(f"  can't save: {e}")
                    continue
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps(homography.to_dict(H, img, flo, size), indent=2))
                print(f"saved {args.out}")
                break
            elif key in (ord("q"), 27):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
