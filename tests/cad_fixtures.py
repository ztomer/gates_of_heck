"""Tiny meshes and G-code for the CAD layer's tests (checks/cad_*.py, GOH.CAD).

Every fixture is built here from numbers, so a test names the defect it plants: a box with a face
missing, a face turned over, two boxes sharing one edge. Binary STL, little-endian float32, as a
slicer reads it.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

# A box's 12 triangles over its 8 corners (index = x + 2y + 4z), wound outward.
BOX_TRIS = (
    (0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4),
    (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5),
)  # fmt: skip


def box(x0, x1, y0, y1, z0, z1):
    """The 12 outward triangles of an axis-aligned box, as vertex triples."""
    c = [(x, y, z) for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)]
    return [(c[a], c[b], c[d]) for a, b, d in BOX_TRIS]


def flipped(tris):
    return [(a, c, b) for a, b, c in tris]


def _normal(a, b, c):
    u = [b[i] - a[i] for i in range(3)]
    w = [c[i] - a[i] for i in range(3)]
    n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
    length = math.hypot(*n)
    return tuple(x / length for x in n) if length else (0.0, 0.0, 1.0)


def write_stl(path: Path, tris, zero_normals: bool = False) -> Path:
    """Binary STL with each facet's normal from its winding (zero_normals: all (0, 0, 0))."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"fixture".ljust(80, b"\0"))
        fh.write(struct.pack("<I", len(tris)))
        for t in tris:
            fh.write(struct.pack("<3f", *((0.0, 0.0, 0.0) if zero_normals else _normal(*t))))
            fh.writelines(struct.pack("<3f", *v) for v in t)
            fh.write(b"\0\0")
    return path


def gcode(layers):
    """Bambu-shaped G-code: each layer a list of ('e'|'t', x, y) moves from the previous point."""
    out = ["; HEADER_BLOCK_START", "G1 X0 Y0"]
    for z, moves in layers:
        out.append("; CHANGE_LAYER")
        out.append(f"G1 Z{z}")
        for kind, x, y in moves:
            out.append(f"G1 X{x} Y{y} E0.5" if kind == "e" else f"G1 X{x} Y{y}")
    out.append("; EXECUTABLE_BLOCK_END")
    return "\n".join(out) + "\n"
