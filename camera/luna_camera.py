"""Laptop camera receiver for ActiasLuna.

Finds people (and anything that moves) in the laptop camera and sends the ActiasLuna server only where each one's
bottom touches the image and where its top is, as a `cam` UDP packet every 250 ms. Frames are never saved and never
leave this laptop: the only picture is the annotated preview (Preview), kept in memory and served on 127.0.0.1 for the
server's /camera page, and only encoded while that page is asking for it.

    python camera/luna_camera.py                    # people detectors + anything moving, to 127.0.0.1:<server_udp_port>
    python camera/luna_camera.py --preview          # also show a local window with the detections
    python camera/luna_camera.py --detector hybrid  # people detectors only

Packet: {"v": 1, "t": "cam", "id", "role": "cam", "fw", "seq", "up", "w", "h",
         "det": [{"u", "v", "hu", "hv", "bx", "by", "bw", "bh", "s", "k"}]}
All positions are fractions of the frame. u, v is the foot point (bottom centre of the box), present only when the
feet are in view; hu, hv is the head top, present only when the head is in view. bx, by, bw, bh is the box; s the
detector's confidence, 0..1; k what was found: "body" (whole person), "upper" (head and shoulders), "face" or
"moving" (something that moved; the server decides from its height whether it can be a person).
"""

import argparse
import json
import math
import socket
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FW = "cam-2"
WINDOW_S = 0.25        # one packet per window, like the boards
FRAME_WIDTH = 640      # frames are scaled to this width before detection
EDGE_PX = 3            # a box touching the bottom edge has its feet out of view; touching the top, its head
MOTION_MIN_AREA = 0.01 # motion blobs smaller than this fraction of the frame are ignored
# Packets go out 4 times a second, so detecting more often is wasted CPU, and uncapped detection takes every core
# and slows the server running on the same laptop. Frames in between are grabbed and dropped (keeps them fresh).
DETECT_FPS = 8.0
CV_THREADS = 2
FACE_CROWN = 0.25      # a face box starts at the brow; the head top is about this many face heights above it
UPPER_SCORE = 0.5      # Haar cascades give no usable confidence; these fixed scores rank them below a whole body
FACE_SCORE = 0.6
# One person often fires several detectors at once (whole body, head and shoulders, face, a moving blob). Boxes
# sharing at least this much of the smaller one's area are one person, sent as the kind ranked first here. From a
# laptop on a table the feet are almost never in view, so head and shoulders lead: only "body" and "moving" boxes
# claim feet, and their bottom edge is usually the table, not the floor.
MERGE_OVERLAP = 0.4
KIND_RANK = {"upper": 0, "face": 1, "body": 2, "moving": 3}
# Haar cascades and HOG fire on door frames, wall texture and posters. Stricter settings, plus minimum sizes as a
# fraction of the frame height (anyone in the room looks at least this big from the laptop).
UPPER_NEIGHBORS = 6
UPPER_MIN_H = 0.12
FACE_NEIGHBORS = 6
FACE_MIN_H = 0.05
HOG_MIN_WEIGHT = 0.5
BODY_MIN_H = 0.25
# A person is sent only after CONFIRM_HITS of the last CONFIRM_FRAMES detections found them (one-frame flukes never
# count), and only while trusted: a face, a strong whole body (score >= BODY_TRUST_SCORE), or head and shoulders that
# are also moving vouched for them in the last TRUST_S. Moving blobs and lone head-and-shoulders boxes only carry a
# person someone vouched for (turned away, side-on), so a door swinging or a wall someone walked past is dropped.
CONFIRM_HITS = 2
CONFIRM_FRAMES = 3
TRUST_S = 10.0
BODY_TRUST_SCORE = 0.6
# Preview for the /camera page. luna_server.CAMERA_PREVIEW_PORT passes the same port; 127.0.0.1 only.
PREVIEW_PORT = 8093
PREVIEW_IDLE_S = 3.0   # stop encoding frames this long after the page last asked
PREVIEW_QUALITY = 70


def person_record(box, frame_w, frame_h, score, kind="body"):
    """Normalised detection record for box (x, y, w, h) in pixels, or None if neither feet nor head is in view.

    kind "body" (a person detector) and "moving" (anything that moved) boxes span top to bottom; "upper" and "face"
    boxes never show the feet. Whether a "moving" thing is a person is decided by the server from its height.
    """
    x, y, w, h = box
    rec = {"bx": round(x / frame_w, 4), "by": round(y / frame_h, 4), "bw": round(w / frame_w, 4),
           "bh": round(h / frame_h, 4), "s": round(max(0.0, min(1.0, score)), 3), "k": kind}
    cx = round((x + w / 2) / frame_w, 4)
    if kind in ("body", "moving") and y + h < frame_h - EDGE_PX:
        rec["u"], rec["v"] = cx, round((y + h) / frame_h, 4)
    head_y = y - FACE_CROWN * h if kind == "face" else y
    if head_y > EDGE_PX:
        rec["hu"], rec["hv"] = cx, round(head_y / frame_h, 4)
    return rec if "u" in rec or "hu" in rec else None


def _overlap(a, b):
    """Shared area of boxes a, b (x, y, w, h) as a fraction of the smaller one's."""
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    small = min(a[2] * a[3], b[2] * b[3])
    return w * h / small if w > 0 and h > 0 and small > 0 else 0.0


def merge_overlaps(found):
    """[(box, score, kind)] with each person kept once: overlapping boxes collapse to the most complete kind."""
    kept = []
    for f in sorted(found, key=lambda f: (KIND_RANK.get(f[2], 9), -f[1])):
        if all(_overlap(f[0], k[0]) < MERGE_OVERLAP and not _inside(f[0], k[0], 0.0) and not _inside(k[0], f[0], 0.0)
               for k in kept):
            kept.append(f)
    return kept


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
        keep = self.cv2.dnn.NMSBoxes(boxes, scores, HOG_MIN_WEIGHT, 0.4)
        keep = [int(i) for i in (keep.ravel() if hasattr(keep, "ravel") else keep)]
        min_h = BODY_MIN_H * frame.shape[0]
        # HOG margins are unbounded; squash them into 0..1.
        return [(boxes[i], 1.0 - math.exp(-max(scores[i], 0.0)), "body") for i in keep if boxes[i][3] >= min_h]


def _inside(box, outer, margin=0.15):
    """True when box's centre lies inside outer, grown by margin of its size on each side."""
    x, y, w, h = box
    ox, oy, ow, oh = outer
    cx, cy = x + w / 2, y + h / 2
    return ox - margin * ow <= cx <= ox + ow * (1 + margin) and oy - margin * oh <= cy <= oy + oh * (1 + margin)


class UpperDetector:
    """OpenCV's bundled Haar cascades for head-and-shoulders and frontal faces: finds people whose legs are hidden
    (behind a desk, below the frame). Gives a head top, never feet."""

    def __init__(self, cv2):
        self.cv2 = cv2
        base = Path(getattr(getattr(cv2, "data", None), "haarcascades", ""))
        self.upper = cv2.CascadeClassifier(str(base / "haarcascade_upperbody.xml"))
        self.face = cv2.CascadeClassifier(str(base / "haarcascade_frontalface_default.xml"))
        # Side-on faces; the cascade finds faces turned one way, so the mirrored frame finds the other.
        self.profile = cv2.CascadeClassifier(str(base / "haarcascade_profileface.xml"))
        if self.upper.empty() or self.face.empty():
            raise SystemExit("this OpenCV build has no bundled Haar cascades (cv2.data); use --detector hog")

    def _faces(self, gray, size):
        opts = {"scaleFactor": 1.1, "minNeighbors": FACE_NEIGHBORS, "minSize": (size, size)}
        found = [tuple(int(c) for c in b) for b in self.face.detectMultiScale(gray, **opts)]
        if not self.profile.empty():
            w = gray.shape[1]
            found += [tuple(int(c) for c in b) for b in self.profile.detectMultiScale(gray, **opts)]
            found += [(w - int(x) - int(bw), int(y), int(bw), int(bh))
                      for x, y, bw, bh in self.profile.detectMultiScale(self.cv2.flip(gray, 1), **opts)]
        return [f for i, f in enumerate(found) if not any(_overlap(f, g) >= MERGE_OVERLAP for g in found[:i])]

    def __call__(self, frame):
        cv2 = self.cv2
        gray = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        h = frame.shape[0]
        up = max(24, int(UPPER_MIN_H * h))
        uppers = [tuple(int(c) for c in b) for b in
                  self.upper.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=UPPER_NEIGHBORS, minSize=(up, up))]
        faces = self._faces(gray, max(20, int(FACE_MIN_H * h)))
        # A face inside a head-and-shoulders box is the same person; both are kept, because the face is what vouches
        # for it (PersonTracks), and merge_overlaps sends it once.
        return [(b, UPPER_SCORE, "upper") for b in uppers] + [(f, FACE_SCORE, "face") for f in faces]


class HybridDetector:
    """Whole bodies (HOG) first, then upper bodies and faces for anyone whose legs are hidden. Upper-body finds
    inside a whole-body box are the same person and are dropped, so each person is reported once."""

    def __init__(self, cv2):
        self.body = HogDetector(cv2)
        self.upper = UpperDetector(cv2)

    def __call__(self, frame):
        bodies = self.body(frame)
        rest = [d for d in self.upper(frame) if not any(_inside(d[0], b[0]) for b in bodies)]
        return bodies + rest


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
            out.append(((x, y, w, h), min(1.0, a / (0.05 * area)), "body"))
        return out


# Moving objects ("moving" / "combined" detectors): anything that moves is a candidate person.
MOVE_MIN_FRAMES = 3      # seen in this many detections in a row...
MOVE_MIN_SHIFT = 0.02    # ...and moved this fraction of the frame width in total, before it counts (flicker never does)
MOVE_HOLD_S = 20.0       # a mover whose blob vanishes in place (stood still, faded into the background) is kept this long
MOVE_SCORE = 0.7
MOVE_HELD_SCORE = 0.5
MOVE_BG_LEARN = 0.003    # background learning rate per detection: slow, so a person who stops fades over ~a minute


class MoverTracks:
    """Turns each frame's moving blobs into confirmed movers (pure Python, no OpenCV, so it is testable).

    A blob is followed from frame to frame by overlap. It becomes a mover once it has been seen MOVE_MIN_FRAMES in a
    row and has moved MOVE_MIN_SHIFT: lighting flicker, a screen or swaying curtains rarely do both. A confirmed mover
    whose blob disappears without leaving the frame most likely stopped and faded into the background model, so it
    is held at its last box for MOVE_HOLD_S.
    """

    def __init__(self):
        self.tracks = []   # {"box", "start", "frames", "seen", "confirmed"}

    @staticmethod
    def _centre(box):
        return box[0] + box[2] / 2, box[1] + box[3] / 2

    def update(self, boxes, t, frame_w):
        """boxes: this frame's blobs (x, y, w, h). Returns [(box, score, held)] for confirmed movers."""
        unmatched = list(boxes)
        for tr in self.tracks:
            hit = next((b for b in unmatched if _inside(b, tr["box"]) or _inside(tr["box"], b)), None)
            tr["hit"] = hit is not None
            if hit is None:
                continue
            unmatched.remove(hit)
            tr["box"], tr["seen"] = hit, t
            tr["frames"] += 1
            cx, cy = self._centre(hit)
            shift = max(abs(cx - tr["start"][0]), abs(cy - tr["start"][1])) / frame_w
            if tr["frames"] >= MOVE_MIN_FRAMES and shift >= MOVE_MIN_SHIFT:
                tr["confirmed"] = True
        kept = []
        for tr in self.tracks:
            x, _, w, _ = tr["box"]
            at_side = x <= EDGE_PX or x + w >= frame_w - EDGE_PX   # it walked out of view rather than stopping
            if tr["hit"] or (tr["confirmed"] and not at_side and t - tr["seen"] <= MOVE_HOLD_S):
                kept.append(tr)
        for b in unmatched:
            kept.append({"box": b, "start": self._centre(b), "frames": 1, "seen": t, "confirmed": False, "hit": True})
        self.tracks = kept
        return [(tr["box"], MOVE_SCORE if tr["hit"] else MOVE_HELD_SCORE, not tr["hit"])
                for tr in self.tracks if tr["confirmed"]]


class MovingDetector:
    """Anything that moves counts as a candidate person, whatever its shape or pose, seen whole or in part. The
    server then keeps only things of a person's height (fusion.MIN_HEIGHT_REL) near a phone."""

    def __init__(self, cv2):
        self.cv2 = cv2
        self.bg = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=25, detectShadows=True)
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self.tracks = MoverTracks()

    def blobs(self, frame):
        cv2 = self.cv2
        mask = self.bg.apply(frame, learningRate=MOVE_BG_LEARN)
        _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)   # shadows are 127; drop them
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self.kernel, iterations=3)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        area = frame.shape[0] * frame.shape[1]
        return [tuple(int(c) for c in cv2.boundingRect(c)) for c in contours
                if cv2.contourArea(c) >= MOTION_MIN_AREA * area]

    def __call__(self, frame):
        movers = self.tracks.update(self.blobs(frame), time.monotonic(), frame.shape[1])
        return [(box, score, "moving") for box, score, _ in movers]


class PersonTracks:
    """Decides which candidate boxes are people (pure Python, testable): see CONFIRM_HITS and TRUST_S.

    Each frame's candidates from every detector are grouped by overlap, one group per person, and followed from frame
    to frame by overlap.
    """

    def __init__(self):
        self.tracks = []   # {"box", "hits": deque of bool, "group": this frame's candidates or None, "trusted": t}

    @staticmethod
    def _trusts(group):
        kinds = {k for _, _, k in group}
        return ("face" in kinds or any(k == "body" and s >= BODY_TRUST_SCORE for _, s, k in group)
                or {"upper", "moving"} <= kinds)

    def update(self, found, t):
        """found: [(box, score, kind)] from all detectors. Returns (people, rejected), each [(box, score, kind)]."""
        groups = []
        for f in sorted(found, key=lambda f: (KIND_RANK.get(f[2], 9), -f[1])):
            g = next((g for g in groups if _overlap(f[0], g[0][0]) >= MERGE_OVERLAP), None)
            if g is None:
                groups.append([f])
            else:
                g.append(f)
        free = list(groups)
        for tr in self.tracks:
            g = max(free, key=lambda g: _overlap(g[0][0], tr["box"]), default=None)
            if g is not None and _overlap(g[0][0], tr["box"]) < MERGE_OVERLAP:
                g = None
            tr["hits"].append(g is not None)
            tr["group"] = g
            if g is not None:
                free.remove(g)
                tr["box"] = g[0][0]
        self.tracks = [tr for tr in self.tracks if any(tr["hits"])]
        for g in free:
            self.tracks.append({"box": g[0][0], "hits": deque([True], maxlen=CONFIRM_FRAMES), "group": g,
                                "trusted": -math.inf})
        people, rejected = [], []
        for tr in self.tracks:
            g = tr["group"]
            if g is None:
                continue
            if self._trusts(g):
                tr["trusted"] = t
            ok = sum(tr["hits"]) >= CONFIRM_HITS and t - tr["trusted"] <= TRUST_S
            (people if ok else rejected).append(g[0])
        return people, rejected


class CombinedDetector:
    """Whole bodies, heads and shoulders, faces and anything moving, filtered by PersonTracks: faces vouch for a
    person; the rest only follow someone already vouched for. `rejected` keeps the last frame's dropped candidates
    for the /camera preview."""

    def __init__(self, cv2):
        self.body = HogDetector(cv2)
        self.upper = UpperDetector(cv2)
        self.moving = MovingDetector(cv2)
        self.people = PersonTracks()
        self.rejected = []

    def __call__(self, frame):
        found = self.body(frame) + self.upper(frame) + self.moving(frame)
        people, self.rejected = self.people.update(found, time.monotonic())
        return people


DETECTORS = {"combined": CombinedDetector, "hybrid": HybridDetector, "moving": MovingDetector, "hog": HogDetector,
             "upper": UpperDetector, "motion": MotionDetector}


READ_FAILS_REOPEN = 10   # this many failed reads in a row means the camera was lost (unplugged, taken by another app)
REOPEN_MAX_S = 10.0      # retries back off from 1 s up to this


def _capture(cv2, index):
    return cv2.VideoCapture(index, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(index)


def open_camera(cv2, index):
    cap = _capture(cv2, index)
    if not cap.isOpened():
        raise SystemExit(f"could not open camera {index} (in use by another app? try --camera 1)")
    return cap


def reopen_camera(cv2, cap, index):
    """Release a lost camera and retry, backing off, until it delivers a frame again. Ctrl+C still stops it."""
    cap.release()
    delay = 1.0
    print(f"camera {index} stopped giving frames (unplugged, or another app took it?); retrying", flush=True)
    while True:
        time.sleep(delay)
        cap = _capture(cv2, index)
        if cap.isOpened() and cap.read()[0]:
            print(f"camera {index} is back", flush=True)
            return cap
        cap.release()
        delay = min(REOPEN_MAX_S, delay * 2)


def read_frame(cv2, cap):
    ok, frame = cap.read()
    if not ok:
        return None
    h, w = frame.shape[:2]
    if w != FRAME_WIDTH:
        frame = cv2.resize(frame, (FRAME_WIDTH, round(h * FRAME_WIDTH / w)))
    return frame


def detect(detector, frame):
    """Detection records (feet and/or head top) for one frame."""
    h, w = frame.shape[:2]
    out = []
    for box, score, kind in merge_overlaps(detector(frame)):
        d = person_record(box, w, h, score, kind)
        if d:
            out.append(d)
    return out


def draw(cv2, frame, dets, rejected=()):
    """Preview: green box, red dot at the feet, blue dot at the head top (the server uses the head only when the
    feet are missing). Rejected candidates (pixel boxes) in thin grey."""
    h, w = frame.shape[:2]
    for (x, y, bw, bh), _, kind in rejected:
        cv2.rectangle(frame, (x, y), (x + bw, y + bh), (140, 140, 140), 1)
        cv2.putText(frame, kind, (x, max(12, y - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (140, 140, 140), 1)
    for d in dets:
        x0, y0 = int(d["bx"] * w), int(d["by"] * h)
        x1, y1 = int((d["bx"] + d["bw"]) * w), int((d["by"] + d["bh"]) * h)
        cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 200, 0), 2)
        if "u" in d:
            cv2.circle(frame, (int(d["u"] * w), int(d["v"] * h)), 5, (0, 0, 255), -1)
        if "hu" in d:
            cv2.circle(frame, (int(d["hu"] * w), int(d["hv"] * h)), 5, (255, 120, 0), -1)
        cv2.putText(frame, f"{d['k']} {d['s']:.2f}", (x0, max(12, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (0, 200, 0), 1)


class Preview:
    """Latest annotated frame as JPEG, for the /camera page only. Bound to 127.0.0.1, so nothing on the network (the
    phones on the demo Wi-Fi included) can reach it; frames are encoded only while the page keeps asking."""

    def __init__(self, port):
        self.jpeg = None
        self.asked = -math.inf
        self.lock = threading.Lock()
        preview = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def do_GET(self):
                preview.asked = time.monotonic()
                with preview.lock:
                    body = preview.jpeg
                if self.path.split("?")[0] != "/preview.jpg" or body is None:
                    self.send_response(404 if body is not None else 503)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def wanted(self):
        if time.monotonic() - self.asked > PREVIEW_IDLE_S:
            with self.lock:
                self.jpeg = None   # page closed: don't keep a picture around
            return False
        return True

    def put(self, cv2, frame, dets, rejected=()):
        shown = frame.copy()
        draw(cv2, shown, dets, rejected)
        cv2.putText(shown, f"{len(dets)} sent", (8, shown.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (255, 255, 255), 2)
        ok, buf = cv2.imencode(".jpg", shown, [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_QUALITY])
        if ok:
            with self.lock:
                self.jpeg = buf.tobytes()


DEFAULT_UDP_PORT = 5007   # same fallback as server/luna_server.py when config/network.json is missing


def server_port():
    try:
        cfg = json.loads((ROOT / "config" / "network.json").read_text())
        return int(cfg.get("server_udp_port", DEFAULT_UDP_PORT))
    except (OSError, ValueError, TypeError, AttributeError):
        return DEFAULT_UDP_PORT


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default="127.0.0.1", help="ActiasLuna server address (default: this laptop)")
    ap.add_argument("--port", type=int, default=None, help="server UDP port (default: config/network.json)")
    ap.add_argument("--camera", type=int, default=0, help="camera index")
    ap.add_argument("--detector", choices=list(DETECTORS), default="combined",
                    help="combined: people detectors plus anything that moves (default); hybrid: whole bodies plus "
                         "upper bodies/faces; moving: anything that moves, followed over time; hog: whole bodies "
                         "only; upper: upper bodies/faces only; motion: moving person-shaped blobs, no memory")
    ap.add_argument("--id", default="cam:laptop", help="receiver id in packets")
    ap.add_argument("--preview", action="store_true", help="show a local window (never recorded or sent)")
    ap.add_argument("--preview-port", type=int, default=PREVIEW_PORT,
                    help=f"serve the annotated picture to the server's /camera page on 127.0.0.1 (default "
                         f"{PREVIEW_PORT}, 0 = off)")
    args = ap.parse_args(argv)

    port = args.port or server_port()

    import cv2   # imported here so the packet helpers can be tested without OpenCV

    cv2.setNumThreads(CV_THREADS)
    cap = open_camera(cv2, args.camera)
    detector = DETECTORS[args.detector](cv2)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    preview = None
    if args.preview_port:
        try:
            preview = Preview(args.preview_port)
        except OSError as exc:
            print(f"no /camera preview: port {args.preview_port} is taken ({exc})", flush=True)
    start = time.monotonic()
    next_send = start + WINDOW_S
    next_detect = start
    seq = 0
    fails = 0
    dets, dets_at, size = [], 0.0, (FRAME_WIDTH, 480)
    print(f"camera {args.camera} -> {args.host}:{port} ({args.detector}); Ctrl+C to stop")
    try:
        while True:
            if time.monotonic() < next_detect:
                ok = cap.grab()       # waits for the camera's next frame (no CPU) and drops it
                frame = None
            else:
                next_detect = time.monotonic() + 1.0 / DETECT_FPS
                frame = read_frame(cv2, cap)
                ok = frame is not None
            if not ok:
                # A lost camera fails instantly, so without this the loop would spin a core at 100%.
                fails += 1
                if fails >= READ_FAILS_REOPEN:
                    cap, fails = reopen_camera(cv2, cap, args.camera), 0
                else:
                    time.sleep(0.05)
                continue
            fails = 0
            now = time.monotonic()
            if frame is not None:
                size = (frame.shape[1], frame.shape[0])
                dets, dets_at = detect(detector, frame), now
                if preview and preview.wanted():
                    preview.put(cv2, frame, dets, getattr(detector, "rejected", ()))
                if args.preview:
                    draw(cv2, frame, dets)
                    cv2.imshow("luna camera (local only)", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
                del frame
            if now >= next_send:
                fresh = dets if now - dets_at <= 2 * WINDOW_S else []   # empty packet still acts as heartbeat
                pkt = build_packet(args.id, seq, (now - start) * 1000, size[0], size[1], fresh)
                try:
                    sock.sendto(json.dumps(pkt, separators=(",", ":")).encode(), (args.host, port))
                except OSError:
                    pass   # server not up yet, or Windows reporting an earlier unreachable port: keep going
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
