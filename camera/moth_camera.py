"""Laptop camera receiver for Moth.

Finds people in the laptop camera and sends the Moth server only where each person's feet touch the image, as a
`cam` UDP packet every 250 ms. Frames stay in memory for one detection pass and are never saved or sent.

    python camera/moth_camera.py                 # HOG person detector, sends to 127.0.0.1:<server_udp_port>
    python camera/moth_camera.py --preview       # also show a local window with the detections
    python camera/moth_camera.py --detector motion

Packet: {"v": 1, "t": "cam", "id", "role": "cam", "fw", "seq", "up", "w", "h",
         "det": [{"u", "v", "bw", "bh", "s"}]}
u, v is the foot point (bottom centre of the person's box) and bw, bh the box size, all as fractions of the frame;
s is the detector's confidence, 0..1.
"""

import argparse
import json
import math
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FW = "cam-1"
WINDOW_S = 0.25        # one packet per window, like the boards
FRAME_WIDTH = 640      # frames are scaled to this width before detection
EDGE_PX = 3            # boxes touching the bottom edge are dropped: their feet are out of view
MOTION_MIN_AREA = 0.01 # motion blobs smaller than this fraction of the frame are ignored


def foot_point(box, frame_w, frame_h, score):
    """Normalised detection record for box (x, y, w, h) in pixels, or None if the feet are out of view."""
    x, y, w, h = box
    if y + h >= frame_h - EDGE_PX:
        return None
    return {
        "u": round((x + w / 2) / frame_w, 4),
        "v": round((y + h) / frame_h, 4),
        "bw": round(w / frame_w, 4),
        "bh": round(h / frame_h, 4),
        "s": round(max(0.0, min(1.0, score)), 3),
    }


def build_packet(cam_id, seq, up_ms, frame_w, frame_h, dets):
    return {"v": 1, "t": "cam", "id": cam_id, "role": "cam", "fw": FW, "seq": seq, "up": int(up_ms),
            "w": int(frame_w), "h": int(frame_h), "det": dets}


class HogDetector:
    """OpenCV's built-in pedestrian detector. Needs most of the body in view; works on people standing still."""

    def __init__(self, cv2):
        self.cv2 = cv2
        if not hasattr(cv2, "HOGDescriptor"):
            raise SystemExit(f"OpenCV {cv2.__version__} has no HOG detector (removed in 5.0): "
                             'pip install "opencv-python>=4.8,<5", or use --detector motion')
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def __call__(self, frame):
        rects, weights = self.hog.detectMultiScale(frame, winStride=(8, 8), padding=(8, 8), scale=1.05)
        if len(rects) == 0:
            return []
        boxes = [tuple(int(c) for c in r) for r in rects]
        scores = [float(w) for w in (weights.ravel() if hasattr(weights, "ravel") else weights)]
        keep = self.cv2.dnn.NMSBoxes(boxes, scores, 0.0, 0.4)
        keep = [int(i) for i in (keep.ravel() if hasattr(keep, "ravel") else keep)]
        # HOG margins are unbounded; squash them into 0..1.
        return [(boxes[i], 1.0 - math.exp(-max(scores[i], 0.0))) for i in keep]


class MotionDetector:
    """Background subtraction. Cheap and sees partial bodies, but loses people who stand still for a while."""

    def __init__(self, cv2):
        self.cv2 = cv2
        self.bg = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=25, detectShadows=True)
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    def __call__(self, frame):
        cv2 = self.cv2
        mask = self.bg.apply(frame)
        _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)   # shadows are 127; drop them
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self.kernel, iterations=3)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        area = frame.shape[0] * frame.shape[1]
        out = []
        for c in contours:
            a = cv2.contourArea(c)
            if a < MOTION_MIN_AREA * area:
                continue
            x, y, w, h = cv2.boundingRect(c)
            if h < w:            # people are taller than wide; skips doors, lighting changes, pets
                continue
            out.append(((x, y, w, h), min(1.0, a / (0.05 * area))))
        return out


DETECTORS = {"hog": HogDetector, "motion": MotionDetector}


def open_camera(cv2, index):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(index)
    if not cap.isOpened():
        raise SystemExit(f"could not open camera {index}")
    return cap


def read_frame(cv2, cap):
    ok, frame = cap.read()
    if not ok:
        return None
    h, w = frame.shape[:2]
    if w != FRAME_WIDTH:
        frame = cv2.resize(frame, (FRAME_WIDTH, round(h * FRAME_WIDTH / w)))
    return frame


def detect(detector, frame):
    """Foot-point records for one frame."""
    h, w = frame.shape[:2]
    out = []
    for box, score in detector(frame):
        d = foot_point(box, w, h, score)
        if d:
            out.append(d)
    return out


def draw(cv2, frame, dets):
    h, w = frame.shape[:2]
    for d in dets:
        x0, y0 = int((d["u"] - d["bw"] / 2) * w), int((d["v"] - d["bh"]) * h)
        x1, y1 = int((d["u"] + d["bw"] / 2) * w), int(d["v"] * h)
        cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 200, 0), 2)
        cv2.circle(frame, (int(d["u"] * w), y1), 5, (0, 0, 255), -1)
        cv2.putText(frame, f"{d['s']:.2f}", (x0, max(12, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 0), 1)


def server_port():
    try:
        cfg = json.loads((ROOT / "config" / "network.json").read_text())
        return int(cfg["server_udp_port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default="127.0.0.1", help="Moth server address (default: this laptop)")
    ap.add_argument("--port", type=int, default=None, help="server UDP port (default: config/network.json)")
    ap.add_argument("--camera", type=int, default=0, help="camera index")
    ap.add_argument("--detector", choices=sorted(DETECTORS), default="hog")
    ap.add_argument("--id", default="cam:laptop", help="receiver id in packets")
    ap.add_argument("--preview", action="store_true", help="show a local window (never recorded or sent)")
    args = ap.parse_args(argv)

    port = args.port or server_port()
    if not port:
        raise SystemExit("no server port: pass --port or set server_udp_port in config/network.json")

    import cv2   # imported here so the packet helpers can be tested without OpenCV

    cap = open_camera(cv2, args.camera)
    detector = DETECTORS[args.detector](cv2)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    start = time.monotonic()
    next_send = start + WINDOW_S
    seq = 0
    dets, dets_at, size = [], 0.0, (FRAME_WIDTH, 480)
    print(f"camera {args.camera} -> {args.host}:{port} ({args.detector}); Ctrl+C to stop")
    try:
        while True:
            frame = read_frame(cv2, cap)
            now = time.monotonic()
            if frame is not None:
                size = (frame.shape[1], frame.shape[0])
                dets, dets_at = detect(detector, frame), now
                if args.preview:
                    draw(cv2, frame, dets)
                    cv2.imshow("moth camera (local only)", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
                del frame
            if now >= next_send:
                fresh = dets if now - dets_at <= 2 * WINDOW_S else []   # empty packet still acts as heartbeat
                pkt = build_packet(args.id, seq, (now - start) * 1000, size[0], size[1], fresh)
                sock.sendto(json.dumps(pkt, separators=(",", ":")).encode(), (args.host, port))
                seq += 1
                next_send = max(next_send + WINDOW_S, now)
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if args.preview:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
