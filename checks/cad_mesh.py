#!/usr/bin/env python3
"""Judge STL files as a slicer's reader rebuilds them (the CAD layer, gates/cad_gate.sh; GOH.CAD).

An STL is a triangle soup: every reader re-merges its corners by position, and admesh (Bambu
Studio, PrusaSlicer, OrcaSlicer) joins them by EXACT float32 equality. So a part that was watertight
in the generator's memory can ship with edges shared by four faces, and the slicer prints it
without a word (ZSUS v21.0.0: 171 / 336 / 1084 pinched edges in three parts). Each file must be:

  * closed: every edge used by exactly two faces (one is a hole, three or more a pinch);
  * consistently wound: the two faces of an edge walk it in opposite directions;
  * outward: every body's signed volume positive (an inside-out boolean result is negative);
  * free of zero-area triangles (corners that float32 rounded into one point);
  * free of zero stored normals in a binary file: Bambu Studio 02.09 refuses a box with 4 of its 12
    normals zero ("Loading of a model file failed", rc 250) and loads one with 2; its rule is not
    published, so the only safe count is none (measured 2026-11-19);
  * the expected number of bodies (`--shells NAME=N`), counted by vertex connectivity.

It also reports the area facing the plate steeper than 45 deg (an overhang, the bed layer left
out), and `--overhang-max A` caps it.

    cad_mesh.py [--shells FILE=N ...] [--overhang-max MM2] FILE.stl ...
    cad_mesh.py --layout LAYOUT.json [--root DIR]     every object's "stl", held to its "shells"

Exit codes: 0 every file passes · 1 a file fails (named, with each defect) · 2 usage: no file,
a missing or unreadable file. Standard library only, so it runs wherever goh does.
"""

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import json
import math
import os
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tui.lib import counted, err, ok  # the count meets its noun (GOH.HEDGEOWN)

DOWN = (
    -math.cos(math.radians(45.0)) + 1e-9
)  # a face whose normal points further down than this overhangs
BED = 0.05  # mm: faces lying within this of the lowest point are the bed layer, not an overhang


def _f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def read_stl(
    path: str, zero_normals: list[int] | None = None
) -> list[tuple[tuple[float, float, float], ...]]:
    """Triangles of a binary or ASCII STL, every coordinate as the float32 a reader holds.

    `zero_normals`, when given, gets the count of binary facets whose stored normal is (0, 0, 0)."""
    data = Path(path).read_bytes()
    if len(data) >= 84:
        (n,) = struct.unpack_from("<I", data, 80)
        if len(data) == 84 + 50 * n:
            tris, zeros = [], 0
            for i in range(n):
                v = struct.unpack_from("<12f", data, 84 + 50 * i)
                zeros += v[0:3] == (0.0, 0.0, 0.0)
                tris.append((v[3:6], v[6:9], v[9:12]))
            if zero_normals is not None:
                zero_normals.append(zeros)
            return tris
    words = data.decode("ascii", errors="replace").split()
    corners = [
        (_f32(float(words[i + 1])), _f32(float(words[i + 2])), _f32(float(words[i + 3])))
        for i, w in enumerate(words)
        if w == "vertex"
    ]
    if not corners or len(corners) % 3:
        raise ValueError("neither a binary STL nor an ASCII one with whole triangles")
    return [tuple(corners[i : i + 3]) for i in range(0, len(corners), 3)]


def _bodies(faces: list[tuple[int, int, int]], n_verts: int) -> list[list[int]]:
    """Face indices grouped by vertex connectivity (union-find over each face's corners)."""
    parent = list(range(n_verts))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b, c in faces:
        ra, rb, rc = find(a), find(b), find(c)
        parent[rb] = ra
        parent[find(rc)] = ra
    groups: dict[int, list[int]] = {}
    for i, f in enumerate(faces):
        groups.setdefault(find(f[0]), []).append(i)
    return list(groups.values())


def judge(tris) -> dict:
    """The facts a slicer's reader would find in these triangles."""
    index: dict[tuple, int] = {}
    verts: list[tuple] = []
    faces: list[tuple[int, int, int]] = []
    slivers = 0
    for t in tris:
        ids = []
        for v in t:
            if v not in index:
                index[v] = len(verts)
                verts.append(v)
            ids.append(index[v])
        if len(set(ids)) < 3:
            slivers += 1
        faces.append(tuple(ids))
    directed = Counter()
    for (
        a,
        b,
        c,
    ) in (
        faces
    ):  # a corner joined to itself is a sliver's, counted above, not an edge a reader pairs
        directed.update(e for e in ((a, b), (b, c), (c, a)) if e[0] != e[1])
    boundary = pinched = miswound = 0
    for (a, b), n in directed.items():
        if a > b and (b, a) in directed:
            continue  # counted from the other direction
        total = n + directed.get((b, a), 0)
        if total == 1:
            boundary += 1
        elif total > 2:
            pinched += 1
        elif n != 1:
            miswound += 1  # both faces walk the edge the same way
    zmin = min((v[2] for v in verts), default=0.0)
    overhang = 0.0
    vols = []
    for group in _bodies(faces, len(verts)):
        vol = 0.0
        for fi in group:
            a, b, c = (verts[i] for i in faces[fi])
            vol += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                    + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0  # fmt: skip
            u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
            w = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
            nrm = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
            norm = math.hypot(*nrm)
            if norm and nrm[2] / norm < DOWN and max(a[2], b[2], c[2]) >= zmin + BED:
                overhang += norm / 2.0
        vols.append(vol)
    return {
        "triangles": len(faces),
        "bodies": len(vols),
        "volume": sum(vols),
        "inside_out": sum(1 for v in vols if v <= 0),
        "boundary": boundary,
        "pinched": pinched,
        "miswound": miswound,
        "slivers": slivers,
        "overhang": overhang,
    }


def defects(facts: dict, shells: int | None, overhang_max: float | None) -> list[str]:
    out = []
    if facts.get("zero_normals"):
        out.append(
            f"{counted(facts['zero_normals'], 'facet')} with a zero stored normal "
            "(Bambu Studio refuses such files)"
        )
    if facts["boundary"]:
        out.append(f"{counted(facts['boundary'], 'boundary edge')} (a hole)")
    if facts["pinched"]:
        out.append(
            f"{counted(facts['pinched'], 'pinched edge')} (3+ faces on one edge: pieces touching)"
        )
    if facts["miswound"]:
        out.append(
            f"{counted(facts['miswound'], 'edge')} with inconsistent winding (a face turned over)"
        )
    if facts["inside_out"]:
        out.append(f"{counted(facts['inside_out'], 'body', 'bodies')} inside out (volume <= 0)")
    if facts["slivers"]:
        out.append(
            f"{counted(facts['slivers'], 'zero-area triangle')} (corners float32 made one point)"
        )
    if shells is not None and facts["bodies"] != shells:
        out.append(f"{counted(facts['bodies'], 'body', 'bodies')}, expected {shells}")
    if overhang_max is not None and facts["overhang"] > overhang_max:
        out.append(f"overhang {facts['overhang']:.1f} mm2 > {overhang_max:g}")
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="cad_mesh.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--shells", action="append", default=[], metavar="FILE=N")
    ap.add_argument("--overhang-max", type=float, default=None, metavar="MM2")
    ap.add_argument(
        "--layout", help="a CAD layout (checks/cad_bambu.py): its objects' STLs and shells"
    )
    ap.add_argument(
        "--root", help="where the layout's stl paths resolve (default: the layout's directory)"
    )
    a = ap.parse_args(argv)
    if a.layout:
        try:
            lay = json.loads(Path(a.layout).read_text())
            root = a.root or os.path.dirname(os.path.abspath(a.layout))
            for o in lay["objects"]:
                path = os.path.relpath(os.path.join(root, o["stl"]))
                a.files.append(path)
                if "shells" in o:
                    a.shells.append(f"{path}={int(o['shells'])}")
        except (OSError, ValueError, KeyError, TypeError) as e:
            err(f"cad_mesh: {a.layout} is not a CAD layout: {e}")
            return 2
    if not a.files:
        err("cad_mesh: no STL named")
        return 2
    shells = {}
    for spec in a.shells:
        name, _, n = spec.rpartition("=")
        if not name or not n.isdigit():
            err(f"cad_mesh: --shells takes FILE=N, not {spec!r}")
            return 2
        shells[os.path.normpath(name)] = int(n)
    failed = 0
    for path in a.files:
        try:
            zeros: list[int] = []
            facts = judge(read_stl(path, zeros))
            facts["zero_normals"] = sum(zeros)
        except (OSError, ValueError, struct.error) as e:
            err(f"{path}: cannot be read as an STL: {e}")
            return 2
        bad = defects(facts, shells.get(os.path.normpath(path)), a.overhang_max)
        summary = (
            f"{path}: {counted(facts['bodies'], 'body', 'bodies')}, {facts['volume']:.1f} mm3, "
            f"{counted(facts['triangles'], 'triangle')}, overhang {facts['overhang']:.1f} mm2"
        )
        if bad:
            failed += 1
            err(f"{summary} -- {'; '.join(bad)}")
        else:
            ok(summary)
    if failed:
        err(f"cad_mesh: {counted(failed, 'file')} of {len(a.files)} a slicer would read wrong")
        return 1
    ok(f"cad_mesh: {counted(len(a.files), 'file')} closed, wound outward, as a slicer reads them")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
