"""Load a scanned-room 3D mesh (assimp2json export, e.g. 11_13_2026.json) for the Moth demo.

The teammate's 3D view and the phone scan export the room as an assimp2json file: a list of meshes, each with a
flat `vertices` array [x,y,z, x,y,z, ...] and `faces` as index triples. This merges the meshes into one geometry,
finds the floor and the footprint, and hands back a compact form the browser viewer (tools/demo_3d.html) can draw.

Metres throughout. Y is up in these scans; the floor is the smallest Y, and the footprint is the X-Z rectangle.
That footprint is what the decoy crowd (server/crowd.py) wanders inside, so simulated phones land on the real floor
between the real walls.

Standard library only.
"""

import json


class RoomMesh:
    def __init__(self, positions, indices, bbox):
        self.positions = positions      # flat [x,y,z, ...] in world metres
        self.indices = indices          # flat [i,j,k, ...] triangle vertex indices
        self.bbox = bbox                # {"min":[x,y,z], "max":[x,y,z]}

    # ---- derived geometry ----------------------------------------------------------------------------------------
    @property
    def floor_y(self):
        return self.bbox["min"][1]

    @property
    def footprint(self):
        """(width, depth) of the room on the floor, in metres (X span, Z span)."""
        mn, mx = self.bbox["min"], self.bbox["max"]
        return (mx[0] - mn[0], mx[2] - mn[2])

    def room_to_world(self, x, y):
        """Map a decoy's (x, y) in the crowd's 0..w / 0..d floor frame to world (X, floorY, Z)."""
        mn = self.bbox["min"]
        return (mn[0] + x, self.floor_y, mn[2] + y)

    # ---- loading -------------------------------------------------------------------------------------------------
    @classmethod
    def load(cls, path, round_to=4):
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return cls.from_doc(doc, round_to=round_to)

    @classmethod
    def from_doc(cls, doc, round_to=4):
        meshes = doc.get("meshes") or []
        positions = []
        indices = []
        base = 0
        mn = [float("inf")] * 3
        mx = [float("-inf")] * 3
        for m in meshes:
            verts = m.get("vertices") or []
            nverts = len(verts) // 3
            for i in range(0, nverts * 3, 3):
                x, y, z = verts[i], verts[i + 1], verts[i + 2]
                positions.append(round(x, round_to))
                positions.append(round(y, round_to))
                positions.append(round(z, round_to))
                if x < mn[0]: mn[0] = x
                if y < mn[1]: mn[1] = y
                if z < mn[2]: mn[2] = z
                if x > mx[0]: mx[0] = x
                if y > mx[1]: mx[1] = y
                if z > mx[2]: mx[2] = z
            for face in (m.get("faces") or []):
                if len(face) == 3:
                    indices.append(base + face[0])
                    indices.append(base + face[1])
                    indices.append(base + face[2])
                elif len(face) > 3:            # fan-triangulate any quads/polys, just in case
                    for k in range(1, len(face) - 1):
                        indices.append(base + face[0])
                        indices.append(base + face[k])
                        indices.append(base + face[k + 1])
            base += nverts
        if not positions:
            raise ValueError("no mesh vertices found in the scan file")
        bbox = {"min": [round(v, round_to) for v in mn], "max": [round(v, round_to) for v in mx]}
        return cls(positions, indices, bbox)

    def to_json(self):
        """Compact dict the browser viewer loads once."""
        return {"positions": self.positions, "indices": self.indices, "bbox": self.bbox,
                "floor_y": round(self.floor_y, 4),
                "footprint": [round(self.footprint[0], 4), round(self.footprint[1], 4)]}


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\nicho\Downloads\11_13_2026.json"
    rm = RoomMesh.load(src)
    print("loaded", len(rm.positions) // 3, "verts,", len(rm.indices) // 3, "triangles")
    print("bbox:", rm.bbox)
    print("floor Y:", round(rm.floor_y, 3), " footprint (w x d):",
          round(rm.footprint[0], 2), "x", round(rm.footprint[1], 2), "m")
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", encoding="utf-8") as f:
            json.dump(rm.to_json(), f, separators=(",", ":"))
        print("wrote compact mesh ->", sys.argv[2])
