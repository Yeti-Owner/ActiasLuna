"""Import a room-scanner .glb (or a .zip holding one) as data/scan/scan.glb + data/scan/room.json.

The scan is a parametric room (Polycam room mode / Apple RoomPlan): every part is its own named node, so nothing is
guessed from geometry. Wall_* / Joint_* are walls, Door_* / Window_* / Opening_* openings, Floor_* the floor,
Ceiling_* the ceiling, and anything under Objects is furniture named "<category>_<subtype>_<n>" (e.g. television_0,
chair_swivel_star_back_no_3, table_dining_rect_0). Furniture categories map to planner.FURNITURE_WEIGHT labels.

The floor outline is put in the layout frame with its bbox min corner at (0, 0): scan x -> layout x, scan z ->
layout y, after aligning the walls to the axes and an optional --rotate (quarter turns) so the scan's corner matches
the corner your layout measures from. A scan with several rooms keeps the largest floor and what stands on it.

  python tools/import_scan.py scan.zip [--rotate 0..3] [--set-layout]

The dashboard's "Import room scan" calls import_room() through /api/scan/import with the same rules.
"""
import argparse
import io
import json
import math
import re
import struct
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
# A footprint stays axis-aligned unless a turned rectangle is under this fraction of its area: round things (swivel
# chairs, lamps) have no real orientation, and axis-aligned boxes keep room.py's conservative bbox exact.
TURNED_AREA = 0.85
# Doors and windows sit in the wall, so their middle can be up to half a wall thickness outside the floor outline.
OPENING_GAP_M = 0.2
# Scanner category (first word of the node name) -> planner label. Unknown categories keep their capitalised word.
LABELS = {"television": "TV", "tv": "TV", "table": "Table", "desk": "Desk", "chair": "Chair", "sofa": "Sofa",
          "bed": "Bed", "storage": "Cabinet", "cabinet": "Cabinet", "shelf": "Bookshelf", "bookshelf": "Bookshelf",
          "refrigerator": "Fridge", "fridge": "Fridge", "stove": "Stove", "oven": "Oven", "dresser": "Dresser"}
ROLE = (("wall", re.compile(r"^(Wall|Joint)_")), ("opening", re.compile(r"^(Door|Window|Opening)_")),
        ("floor", re.compile(r"^Floor_")), ("ceiling", re.compile(r"^Ceiling_")))


def glb_of(raw: bytes) -> bytes:
    """The .glb inside raw, which is either a .glb or a .zip holding one."""
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".glb")]
            if not names:
                raise ValueError("no .glb inside the zip (a .gltf with side files isn't supported)")
            return z.read(names[0])
    return raw


def parts_of(glb: bytes) -> list:
    """[(node name, role, under_objects, triangles (n, 3, 3))] for every mesh node in the default scene."""
    if glb[:4] != b"glTF":
        raise ValueError("not a binary glTF (.glb) file")
    n = struct.unpack("<I", glb[12:16])[0]
    j = json.loads(glb[20:20 + n])
    binary = glb[20 + n + 8:]
    if "KHR_draco_mesh_compression" in j.get("extensionsUsed", []):
        raise ValueError("Draco-compressed meshes aren't supported; export uncompressed")

    def acc(i):
        a = j["accessors"][i]
        bv = j["bufferViews"][a["bufferView"]]
        dtype = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}[a["componentType"]]
        k = {"VEC3": 3, "SCALAR": 1}[a["type"]]
        arr = np.frombuffer(binary, dtype, a["count"] * k, bv.get("byteOffset", 0) + a.get("byteOffset", 0))
        return arr.reshape(-1, k) if k > 1 else arr

    nodes, out = j.get("nodes", []), []

    def walk(i, under_objects):
        node = nodes[i]
        # view3d places the whole scene with one transform, so per-node transforms would put parts in the wrong spot.
        if any(k in node for k in ("matrix", "rotation", "scale", "translation")):
            raise ValueError("node transforms aren't supported by this importer")
        name = node.get("name", "")
        under_objects = under_objects or name == "Objects"
        if "mesh" in node:
            tris = []
            for p in j["meshes"][node["mesh"]]["primitives"]:
                if p.get("mode", 4) != 4:
                    continue
                v = acc(p["attributes"]["POSITION"]).astype(float)
                f = acc(p["indices"]).astype(np.int64).reshape(-1, 3) if "indices" in p else np.arange(len(v)).reshape(-1, 3)
                tris.append(v[f])
            if tris:
                role = next((r for r, rx in ROLE if rx.match(name)), "object" if under_objects else None)
                out.append((name, role, np.concatenate(tris)))
        for c in node.get("children", []):
            walk(c, under_objects)

    scene = j.get("scenes", [{}])[j.get("scene", 0)] if j.get("scenes") else {"nodes": range(len(nodes))}
    for i in scene.get("nodes", []):
        walk(i, False)
    if not any(role == "floor" for _, role, _ in out):
        raise ValueError("no Floor_ node: this isn't a room-mode scan (export the room, not a textured mesh)")
    return out


def rot_y(p, yaw):
    """Same convention as three.js Object3D.rotation.y, so view3d can apply `yaw` directly. p is (..., 3)."""
    c, s = math.cos(yaw), math.sin(yaw)
    x, y, z = p[..., 0], p[..., 1], p[..., 2]
    return np.stack([c * x + s * z, y, -s * x + c * z], axis=-1)


def wall_yaw(tris):
    """Rotation about +y that turns the dominant wall normals onto the x/z axes."""
    cross = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    w = np.linalg.norm(cross, axis=1)
    vert = (w > 1e-9) & (np.abs(cross[:, 1]) < 0.2 * np.maximum(w, 1e-12))
    # Angles folded mod 90 deg onto a circle (x4) so 89 deg and 1 deg average to 0.
    ang = 4 * np.arctan2(cross[vert, 2], cross[vert, 0])
    if not vert.any():
        return 0.0
    return -math.atan2(np.sum(w[vert] * np.sin(ang)), np.sum(w[vert] * np.cos(ang))) / 4


def hull(pts):
    pts = sorted(set(map(tuple, np.round(pts, 4))))
    if len(pts) < 3:
        return pts

    def half(seq):
        h = []
        for p in seq:
            while len(h) >= 2 and (h[-1][0] - h[-2][0]) * (p[1] - h[-2][1]) - (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0]) <= 0:
                h.pop()
            h.append(p)
        return h[:-1]

    return half(pts) + half(pts[::-1])


def rect_of(xz):
    """Smallest-area rectangle around 2D points as 4 corners, axis-aligned unless turning it saves enough area."""
    h = np.array(hull(xz))
    best = None
    for a in [0.0] + [math.atan2(e[1], e[0]) % (math.pi / 2) for e in np.roll(h, -1, 0) - h]:
        c, s = math.cos(a), math.sin(a)
        r = h @ np.array([[c, -s], [s, c]])
        lo, hi = r.min(0), r.max(0)
        area = (hi - lo).prod()
        if best is None or area < (best[0] * TURNED_AREA if best[1] == 0.0 else best[0]):
            best = (area, a, lo, hi)
    _, a, lo, hi = best
    c, s = math.cos(a), math.sin(a)
    corners = np.array([[lo[0], lo[1]], [hi[0], lo[1]], [hi[0], hi[1]], [lo[0], hi[1]]])
    return corners @ np.array([[c, s], [-s, c]])


def floor_outline(tris):
    """Outline of a floor mesh's top face: its boundary edges chained into a loop, collinear points dropped."""
    top_y = tris[..., 1].max()
    cross = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    top = tris[(np.abs(cross[:, 1]) > 0.9 * np.linalg.norm(cross, axis=1)) & (np.abs(tris[..., 1] - top_y) < 0.01).all(1)]
    count = {}
    for t in np.round(top[..., [0, 2]], 3):
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            k = tuple(sorted((tuple(a), tuple(b))))
            count[k] = count.get(k, 0) + 1
    nxt = {}
    for a, b in (k for k, c in count.items() if c == 1):
        nxt.setdefault(a, []).append(b)
        nxt.setdefault(b, []).append(a)
    if not nxt or any(len(v) != 2 for v in nxt.values()):
        return np.array(hull(top.reshape(-1, 3)[:, [0, 2]]))
    start = next(iter(nxt))
    loop, prev, cur = [start], None, start
    while True:
        cur, prev = (nxt[cur][1] if nxt[cur][0] == prev else nxt[cur][0]), cur
        if cur == start:
            break
        loop.append(cur)
    if len(loop) != len(nxt):   # several separate outlines (a hole): the hull is the best single polygon
        return np.array(hull(np.array(list(nxt))))
    p = np.array(loop)
    p = p[[i for i in range(len(p)) if abs(cross2(p[i] - p[i - 1], p[(i + 1) % len(p)] - p[i])) > 1e-4]]
    # Same order as a hand-drawn layout: from the corner nearest the min corner, (0, 0) -> (w, 0) first.
    if np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1)) < 0:
        p = p[::-1]
    return np.roll(p, -int(np.argmin(np.linalg.norm(p - p.min(0), axis=1))), 0)


def cross2(a, b):
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


def poly_area(p):
    x, y = p[:, 0], p[:, 1]
    return abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2


def inside(x, y, poly):
    hit = False
    for (ax, ay), (bx, by) in zip(poly, np.roll(poly, -1, 0)):
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            hit = not hit
    return hit


def edge_dist(p, poly):
    best = math.inf
    for a, b in zip(poly, np.roll(poly, -1, 0)):
        ab = b - a
        t = np.clip(np.dot(p - a, ab) / max(np.dot(ab, ab), 1e-12), 0, 1)
        best = min(best, float(np.linalg.norm(p - a - t * ab)))
    return best


def label_of(name: str) -> str:
    words = name.lower().split("_")
    if "desk" in words:
        return "Desk"
    return LABELS.get(words[0], words[0].capitalize())


def import_room(raw: bytes, source: str, rotate: int = 0, data: Path = DATA) -> dict:
    """Write data/scan/scan.glb + room.json from a .glb or .zip; returns room.json. Raises ValueError on bad input."""
    glb = glb_of(raw)
    parts = parts_of(glb)
    walls = [t for _, role, t in parts if role == "wall"]
    yaw = (wall_yaw(np.concatenate(walls)) if walls else 0.0) + rotate * math.pi / 2
    parts = [(name, role, rot_y(t, yaw)) for name, role, t in parts]

    floors = [(name, t, floor_outline(t)) for name, role, t in parts if role == "floor"]
    floor_name, floor_tris, outline = max(floors, key=lambda f: poly_area(f[2]))
    floor_y = float(floor_tris[..., 1].max())
    x0, z0 = outline.min(0)
    local = outline - [x0, z0]
    w, d = local.max(0)
    if not (1.0 < w < 50 and 1.0 < d < 50):
        raise ValueError(f"couldn't find the room's floor (got {w:.1f} x {d:.1f} m)")
    ceils = [t[..., 1].min() for _, role, t in parts if role == "ceiling"]
    tops = [t[..., 1].max() for _, role, t in parts if role == "wall"]
    ceiling = (max(ceils) if ceils else max(tops) if tops else floor_y + 2.4) - floor_y

    def here(t):
        c = t.reshape(-1, 3).mean(0)
        return inside(c[0] - x0, c[2] - z0, local)

    objects, openings, keep = [], [], [floor_name]
    for name, role, t in parts:
        pts = t.reshape(-1, 3)
        xz = pts[:, [0, 2]] - [x0, z0]
        if role == "object" and here(t):
            fp = rect_of(xz)
            objects.append({"label": label_of(name), "name": name, "height": round(float(pts[:, 1].max() - floor_y), 2),
                            "footprint": [[round(float(a), 3), round(float(b), 3)] for a, b in fp]})
            keep.append(name)
        elif role == "opening":
            fp = rect_of(xz)
            # An opening is a flat panel in a wall: its footprint is the segment along the wall through its middle.
            if np.linalg.norm(fp[1] - fp[0]) >= np.linalg.norm(fp[3] - fp[0]):
                a, b = (fp[0] + fp[3]) / 2, (fp[1] + fp[2]) / 2
            else:
                a, b = (fp[0] + fp[1]) / 2, (fp[3] + fp[2]) / 2
            mid = (a + b) / 2
            if not inside(mid[0], mid[1], local) and edge_dist(mid, local) > OPENING_GAP_M:
                continue   # in another room's wall
            openings.append({"kind": name.split("_")[0].lower(), "name": name,
                             "bottom": round(float(pts[:, 1].min() - floor_y), 2) + 0.0,
                             "top": round(float(pts[:, 1].max() - floor_y), 2),
                             "footprint": [[round(float(a[0]), 3), round(float(a[1]), 3)],
                                           [round(float(b[0]), 3), round(float(b[1]), 3)]]})
            keep.append(name)
    objects.sort(key=lambda o: -poly_area(np.array(o["footprint"])))

    room = {
        "source": source, "kind": "roomplan", "rotate": rotate,
        "polygon": [[round(float(a), 3), round(float(b), 3)] for a, b in local],
        "ceiling_m": round(float(ceiling), 3),
        # view3d places the scene with rotation.y = yaw, then position = -(x0, floor_y, z0).
        "transform": {"yaw": round(yaw, 6), "x0": round(float(x0), 4), "z0": round(float(z0), 4),
                      "floor_y": round(floor_y, 4)},
        "objects": objects, "openings": openings, "keep_nodes": keep,
    }
    out = data / "scan"
    out.mkdir(parents=True, exist_ok=True)
    (out / "scan.glb").write_bytes(glb)
    (out / "room.json").write_text(json.dumps(room, indent=1), encoding="utf-8")
    return room


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scan", type=Path)
    ap.add_argument("--rotate", type=int, default=0, choices=range(4), help="extra quarter turns about the vertical")
    ap.add_argument("--set-layout", action="store_true",
                    help="also set data/layouts/current.json's polygon to the scanned outline (boards are kept)")
    args = ap.parse_args()

    try:
        room = import_room(args.scan.read_bytes(), args.scan.name, args.rotate)
    except ValueError as exc:
        sys.exit(str(exc))
    xs, ys = [p[0] for p in room["polygon"]], [p[1] for p in room["polygon"]]
    w, d = max(xs), max(ys)
    ft = 0.3048
    print(f"room {w:.2f} x {d:.2f} m ({w / ft:.1f} x {d / ft:.1f} ft), {len(xs)} corners, ceiling "
          f"{room['ceiling_m']:.2f} m, walls rotated {math.degrees(room['transform']['yaw']):.1f} deg")
    for o in room["objects"]:
        f = np.array(o["footprint"])
        print(f"  {o['label']:<10} {o['name']:<32} x {f[:, 0].min():.2f}-{f[:, 0].max():.2f}  "
              f"y {f[:, 1].min():.2f}-{f[:, 1].max():.2f}  h {o['height']:.2f}")
    for o in room["openings"]:
        (ax, ay), (bx, by) = o["footprint"]
        print(f"  {o['kind']:<10} {o['name']:<32} ({ax:.2f}, {ay:.2f}) - ({bx:.2f}, {by:.2f})")

    if args.set_layout:
        path = DATA / "layouts" / "current.json"
        layout = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"name": "default", "receivers": {}}
        layout["polygon"] = room["polygon"]
        poly = np.array(room["polygon"])
        outside = [k for k, r in layout.get("receivers", {}).items() if not inside(r["x"], r["y"], poly)]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(layout, indent=1), encoding="utf-8")
        print(f"layout polygon updated in {path}" + (f"; boards outside the room: {outside}" if outside else ""))


if __name__ == "__main__":
    main()
