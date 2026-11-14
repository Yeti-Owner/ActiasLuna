"""Import a textured room-scan mesh (.glb, or a .zip holding one) as data/scan/scan.glb + data/scan/room.json.

Finds the floor and ceiling heights, the wall directions and the rectangular room outline from the geometry, then
footprints of the furniture standing in the room. The outline is put in the layout frame with its min corner at
(0, 0): scan x -> layout x, scan z -> layout y, after aligning the walls to the axes and an optional --rotate
(quarter turns) so the scan's corner matches the corner your layout measures from.

Furniture labels are guesses from size and height (planner.FURNITURE_WEIGHT keys); fix them in room.json if wrong.

  python tools/import_scan.py scan.zip [--rotate 0..3] [--set-layout]

The dashboard's "Import room scan" calls import_room() through /api/scan/import with the same rules.
"""
import argparse
import io
import json
import math
import struct
import sys
import warnings
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CELL_M = 0.05
WALL_GAP_M = 0.05
MIN_OBJ_H_M = 0.35
MIN_OBJ_AREA_M2 = 0.25
MAX_OBJ_H_M = 2.3
# Only flat raised surfaces become furniture (bed, desk, shelf tops): a cell joins one when its 3x3 neighbourhood
# varies by less than FLAT_M. Clothes and clutter are bumpy and stay out, which is what the planner wants anyway.
FLAT_M = 0.08
CLOSE_CELLS = 3


def glb_of(raw: bytes) -> bytes:
    """The .glb inside raw, which is either a .glb or a .zip holding one."""
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".glb")]
            if not names:
                raise ValueError("no .glb inside the zip (a .gltf with side files isn't supported)")
            return z.read(names[0])
    return raw


def mesh_of(glb: bytes):
    """All triangles in scene coordinates. Node transforms are assumed identity (true for phone-scanner exports)."""
    if glb[:4] != b"glTF":
        raise ValueError("not a binary glTF (.glb) file")
    n = struct.unpack("<I", glb[12:16])[0]
    j = json.loads(glb[20:20 + n])
    binary = glb[20 + n + 8:]
    for node in j.get("nodes", []):
        if any(k in node for k in ("matrix", "rotation", "scale", "translation")):
            raise ValueError("node transforms aren't supported by this importer")
    if "KHR_draco_mesh_compression" in j.get("extensionsUsed", []):
        raise ValueError("Draco-compressed meshes aren't supported; export uncompressed")

    def acc(i):
        a = j["accessors"][i]
        bv = j["bufferViews"][a["bufferView"]]
        dtype = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}[a["componentType"]]
        k = {"VEC3": 3, "SCALAR": 1}[a["type"]]
        arr = np.frombuffer(binary, dtype, a["count"] * k, bv.get("byteOffset", 0) + a.get("byteOffset", 0))
        return arr.reshape(-1, k) if k > 1 else arr

    verts, faces, base = [], [], 0
    for m in j["meshes"]:
        for p in m["primitives"]:
            if p.get("mode", 4) != 4:
                continue
            v = acc(p["attributes"]["POSITION"]).astype(float)
            f = acc(p["indices"]).astype(np.int64).reshape(-1, 3) if "indices" in p else np.arange(len(v)).reshape(-1, 3)
            verts.append(v)
            faces.append(f + base)
            base += len(v)
    v, f = np.vstack(verts), np.vstack(faces)
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    cross = np.cross(b - a, c - a)
    area = np.linalg.norm(cross, axis=1) / 2
    normal = cross / np.maximum(2 * area[:, None], 1e-12)
    return (a + b + c) / 3, normal, area


def peak(values, weights, lo, hi, step=0.02):
    h, e = np.histogram(values, bins=np.arange(lo, hi + step, step), weights=weights)
    i = int(np.argmax(h))
    sel = (values >= e[max(0, i - 2)]) & (values < e[min(len(e) - 1, i + 3)])
    return float(np.average(values[sel], weights=weights[sel]))


def wall_yaw(normal, area):
    """Rotation about +y that turns the dominant wall normals onto the x/z axes."""
    vert = np.abs(normal[:, 1]) < 0.2
    # Angles folded mod 90 deg onto a circle (x4) so 89 deg and 1 deg average to 0.
    ang = 4 * np.arctan2(normal[vert, 2], normal[vert, 0])
    w = area[vert]
    return -math.atan2(np.sum(w * np.sin(ang)), np.sum(w * np.cos(ang))) / 4


def rot_y(p, yaw):
    """Same convention as three.js Object3D.rotation.y, so view3d can apply `yaw` directly."""
    c, s = math.cos(yaw), math.sin(yaw)
    x, y, z = p[:, 0], p[:, 1], p[:, 2]
    return np.stack([c * x + s * z, y, -s * x + c * z], axis=1)


def wall_line(cen, normal, area, axis, sign, high):
    """Position of the wall whose inward normal is `sign` along `axis`, from wall faces above furniture height."""
    sel = high & (normal[:, axis] * sign > 0.9)
    vals = cen[sel, axis]
    mid = np.median(cen[:, axis])
    side = vals < mid if sign > 0 else vals > mid
    return peak(vals[side], area[sel][side], vals[side].min() - 0.02, vals[side].max() + 0.02)


def components(mask):
    seen = np.zeros_like(mask, bool)
    out = []
    for i, j in zip(*np.nonzero(mask)):
        if seen[i, j]:
            continue
        stack, cells = [(i, j)], []
        seen[i, j] = True
        while stack:
            a, b = stack.pop()
            cells.append((a, b))
            for da in (-1, 0, 1):
                for db in (-1, 0, 1):
                    p, q = a + da, b + db
                    if 0 <= p < mask.shape[0] and 0 <= q < mask.shape[1] and mask[p, q] and not seen[p, q]:
                        seen[p, q] = True
                        stack.append((p, q))
        out.append(np.array(cells))
    return out


def guess_label(w, d, h, at_wall):
    long_, short = max(w, d), min(w, d)
    if h > 1.3:
        return "Bookshelf" if short < 0.5 else "Cabinet"
    if 0.6 < h < 0.95:
        return "Desk" if at_wall else "Table"
    if long_ > 1.7 and short > 0.8 and h < 0.7:
        return "Bed"
    if long_ > 1.4 and h < 0.6:
        return "Sofa"
    return "Dresser" if h > 0.9 else "Chair"


def furniture(cen, normal, area, outline, floor_y):
    x0, y0, x1, y1 = outline
    nx, ny = int(math.ceil((x1 - x0) / CELL_M)), int(math.ceil((y1 - y0) / CELL_M))
    hgt = cen[:, 1] - floor_y
    inner = ((cen[:, 0] > x0 + WALL_GAP_M) & (cen[:, 0] < x1 - WALL_GAP_M)
             & (cen[:, 2] > y0 + WALL_GAP_M) & (cen[:, 2] < y1 - WALL_GAP_M))
    top = inner & (normal[:, 1] > 0.7) & (hgt < MAX_OBJ_H_M)
    ix = np.clip(((cen[top, 0] - x0) / CELL_M).astype(int), 0, nx - 1)
    iy = np.clip(((cen[top, 2] - y0) / CELL_M).astype(int), 0, ny - 1)
    wsum = np.zeros((nx, ny))
    hsum = np.zeros((nx, ny))
    np.add.at(wsum, (ix, iy), area[top])
    np.add.at(hsum, (ix, iy), area[top] * hgt[top])
    seen = wsum > 0
    hmap = np.where(seen, hsum / np.maximum(wsum, 1e-12), np.nan)
    pad = np.pad(hmap, 1, constant_values=np.nan)
    stack = np.stack([pad[1 + da:nx + 1 + da, 1 + db:ny + 1 + db] for da in (-1, 0, 1) for db in (-1, 0, 1)])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        spread = np.nanmax(stack, 0) - np.nanmin(stack, 0)
    mask = seen & (hmap > MIN_OBJ_H_M) & (spread < FLAT_M)
    # Closing (grow then shrink by CLOSE_CELLS) rejoins a surface split by a fold or a texture seam.
    for grow in (True, False):
        for _ in range(CLOSE_CELLS):
            p = np.pad(mask, 1, constant_values=not grow)
            nb = [p[1 + da:nx + 1 + da, 1 + db:ny + 1 + db] for da in (-1, 0, 1) for db in (-1, 0, 1)]
            mask = np.logical_or.reduce(nb) if grow else np.logical_and.reduce(nb)
    objs = []
    for cells in components(mask):
        if len(cells) * CELL_M * CELL_M < MIN_OBJ_AREA_M2:
            continue
        a0, b0 = cells.min(0)
        a1, b1 = cells.max(0) + 1
        hs = hmap[cells[:, 0], cells[:, 1]]
        h = float(np.nanmedian(hs))
        fx0, fx1, fy0, fy1 = a0 * CELL_M, a1 * CELL_M, b0 * CELL_M, b1 * CELL_M
        at_wall = min(fx0, fy0, (x1 - x0) - fx1, (y1 - y0) - fy1) < 0.3
        objs.append({"label": guess_label(fx1 - fx0, fy1 - fy0, h, at_wall), "guessed": True, "height": round(h, 2),
                     "footprint": [[round(fx0, 3), round(fy0, 3)], [round(fx1, 3), round(fy0, 3)],
                                   [round(fx1, 3), round(fy1, 3)], [round(fx0, 3), round(fy1, 3)]]})
    objs.sort(key=lambda o: -(o["footprint"][2][0] - o["footprint"][0][0]) * (o["footprint"][2][1] - o["footprint"][0][1]))
    for i, o in enumerate(objs):
        o["name"] = f"{o['label']}_{i}"
    return objs


def import_room(raw: bytes, source: str, rotate: int = 0, data: Path = DATA) -> dict:
    """Write data/scan/scan.glb + room.json from a .glb or .zip; returns room.json. Raises ValueError on bad input."""
    glb = glb_of(raw)
    cen, normal, area = mesh_of(glb)
    yaw = wall_yaw(normal, area) + rotate * math.pi / 2
    cen, normal = rot_y(cen, yaw), rot_y(normal, yaw)

    floor_y = peak(cen[normal[:, 1] > 0.9, 1], area[normal[:, 1] > 0.9], cen[:, 1].min(), cen[:, 1].max())
    ceil_y = peak(cen[normal[:, 1] < -0.9, 1], area[normal[:, 1] < -0.9], floor_y + 1.5, cen[:, 1].max())
    high = (cen[:, 1] > floor_y + 1.4) & (cen[:, 1] < ceil_y - 0.15)
    x0 = wall_line(cen, normal, area, 0, +1, high)
    x1 = wall_line(cen, normal, area, 0, -1, high)
    z0 = wall_line(cen, normal, area, 2, +1, high)
    z1 = wall_line(cen, normal, area, 2, -1, high)
    w, d = x1 - x0, z1 - z0
    if not (1.0 < w < 50 and 1.0 < d < 50):
        raise ValueError(f"couldn't find the room's walls (got {w:.1f} x {d:.1f} m)")
    objects = furniture(cen, normal, area, (x0, z0, x1, z1), floor_y)

    room = {
        "source": source, "kind": "mesh", "rotate": rotate,
        "polygon": [[0, 0], [round(w, 3), 0], [round(w, 3), round(d, 3)], [0, round(d, 3)]],
        "ceiling_m": round(ceil_y - floor_y, 3),
        # view3d places the mesh with rotation.y = yaw, then position = -(x0, floor_y, z0).
        "transform": {"yaw": round(yaw, 6), "x0": round(x0, 4), "z0": round(z0, 4), "floor_y": round(floor_y, 4)},
        "objects": objects, "openings": [], "keep_nodes": [],
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
    w, d = room["polygon"][2]
    ft = 0.3048
    print(f"room {w:.2f} x {d:.2f} m ({w / ft:.1f} x {d / ft:.1f} ft), ceiling {room['ceiling_m']:.2f} m, "
          f"walls rotated {math.degrees(room['transform']['yaw']):.1f} deg")
    for o in room["objects"]:
        f = o["footprint"]
        print(f"  {o['name']:<14} x {f[0][0]:.2f}-{f[2][0]:.2f}  y {f[0][1]:.2f}-{f[2][1]:.2f}  h {o['height']:.2f}")

    if args.set_layout:
        path = DATA / "layouts" / "current.json"
        layout = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"name": "default", "receivers": {}}
        layout["polygon"] = room["polygon"]
        outside = [k for k, r in layout.get("receivers", {}).items() if not (0 <= r["x"] <= w and 0 <= r["y"] <= d)]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(layout, indent=1), encoding="utf-8")
        print(f"layout polygon updated in {path}" + (f"; boards outside the room: {outside}" if outside else ""))


if __name__ == "__main__":
    main()
