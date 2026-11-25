"""Slicer warnings and support where a part names it, for checks/cad_bambu.py (the CAD layer;
GOH.CAD).

* slice_warnings: the warnings Bambu Studio shows over a plate ("floating cantilever", "floating
  regions", "large overhangs") are NOT in the sliced 3MF's slice_info.config; the CLI writes them to
  result.json (`warning_message` per plate) and its log ("plate N: found ... slicing warnings:
  ..."). ZSUS v21.2.0's bezel passed a slice_info-only gate with one. Bambu runs the cantilever
  check only with supports OFF, and a per-object enable_support in a 3MF overrides the GUI's global
  switch.
* add_enforcers: each support region (an STL-frame box) becomes a support_enforcer volume of its
  object, in the object's mesh frame, so tree(manual) supports go there and nowhere else (tree(auto)
  filled a 1.6 mm USB-C slot). A box must reach ~0.5 mm into the solid above its overhang: a face on
  a slice plane first prints one layer up, and a box that stops at it gets no support.
* support_points / gate_regions: the sliced G-code must put support under every region and none
  more than `reach` mm (in XY) from one.

Standard library only.

Imported by `cad_bambu.py`: slicing warnings from result.json and the log, support enforcer volumes
in an object's mesh frame, support moves read from G-code, and the region gate (support under each
region, none beyond the reach).
"""

from __future__ import annotations

import json
import math
import re

BOX_TRIS = (
    (0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4),
    (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5),
)  # fmt: skip
_LOG_WARNING = re.compile(r"plate (\d+): found \w+ slicing warnings: (.*)")
_WORD = re.compile(r"([XYZE])(-?\d*\.?\d+)")


def slice_warnings(result_json: str, log: str) -> dict[int, list[str]]:
    """{plate: [message, ...]} from the CLI's result.json and its log, each message once."""
    out: dict[int, list[str]] = {}
    for sp in json.loads(result_json).get("sliced_plates", []):
        msg = (sp.get("warning_message") or "").strip()
        if msg:
            out.setdefault(int(sp["id"]), []).append(msg)
    for m in _LOG_WARNING.finditer(log):
        w = out.setdefault(int(m.group(1)), [])
        if m.group(2).strip() not in w:
            w.append(m.group(2).strip())
    return out


def _box_object(oid: int, box, offset) -> str:
    x0, x1, y0, y1, z0, z1 = box
    corners = [(x, y, z) for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)]
    vs = "".join(
        f'\n     <vertex x="{x - offset[0]:.6f}" y="{y - offset[1]:.6f}" z="{z - offset[2]:.6f}"/>'
        for x, y, z in corners
    )
    ts = "".join(f'\n     <triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in BOX_TRIS)
    return (
        f'  <object id="{oid}" type="model">\n   <mesh>\n    <vertices>{vs}\n    </vertices>'
        f"\n    <triangles>{ts}\n    </triangles>\n   </mesh>\n  </object>\n"
    )


def add_enforcers(files: dict[str, bytes], oid: str, regions, next_id: int) -> int:
    """One support_enforcer part per region on wrapper object `oid`; returns the next free id."""
    model = files["3D/3dmodel.model"].decode()
    m = re.search(
        rf'<object id="{oid}"[^>]*>\s*<components>\s*'
        r'(<component p:path="([^"]+)" objectid="\d+"[^>]*transform="([^"]+)"/>)',
        model,
    )
    if not m:
        raise ValueError(f"object {oid} has no component to add support enforcers beside")
    comp, path, tf = m.groups()
    offset = [float(v) for v in tf.split()[9:12]]
    obj_file = path.lstrip("/")
    sub = files[obj_file].decode()
    ms = files["Metadata/model_settings.config"].decode()
    parts = ""
    for r in regions:
        eid, next_id = next_id, next_id + 1
        sub = sub.replace(" </resources>", _box_object(eid, r["box"], offset) + " </resources>", 1)
        model = model.replace(
            comp, comp + f'\n    <component p:path="{path}" objectid="{eid}" transform="{tf}"/>', 1
        )
        mat = f"1 0 0 {offset[0]} 0 1 0 {offset[1]} 0 0 1 {offset[2]} 0 0 0 1"
        parts += (
            f'\n    <part id="{eid}" subtype="support_enforcer">'
            f'\n      <metadata key="name" value="support: {r.get("why", "")}"/>'
            f'\n      <metadata key="matrix" value="{mat}"/>\n    </part>'
        )
    ms, n = re.subn(
        rf'(<object id="{oid}">.*?</part>)',
        lambda q: q.group(1) + parts,
        ms,
        count=1,
        flags=re.DOTALL,
    )
    if not n:
        raise ValueError(f"object {oid} has no part in model_settings.config")
    files["3D/3dmodel.model"], files[obj_file] = model.encode(), sub.encode()
    files["Metadata/model_settings.config"] = ms.encode()
    return next_id


def support_points(gcode: str) -> list[tuple[float, float, float]]:
    """(x, y, z) of every extruding move in a ; FEATURE: Support* section (plate coordinates)."""
    feat, x, y, z, pts = "", 0.0, 0.0, 0.0, []
    for line in gcode.splitlines():
        if line.startswith("; FEATURE:"):
            feat = line[10:].strip()
        elif line.startswith(("G0 ", "G1 ", "G2 ", "G3 ")):
            w = {k: float(v) for k, v in _WORD.findall(line.split(";", 1)[0])}
            z = w.get("Z", z)
            nx, ny = w.get("X", x), w.get("Y", y)
            if feat.startswith("Support") and w.get("E", 0) > 0 and ("X" in w or "Y" in w):
                pts.append((nx, ny, z))
            x, y = nx, ny
    return pts


def gate_regions(points, regions, reach: float):
    """(moves under each region, farthest move from any region in XY, failures).

    A move is under a region when it lies within 0.5 mm of the region's footprint at or above 1 mm
    below the region's top (the support layers that carry its overhang, not a trunk passing by).
    """
    under = [0] * len(regions)
    farthest = 0.0
    worst = None
    for px, py, pz in points:
        near = math.inf
        for i, r in enumerate(regions):
            x0, x1, y0, y1, _z0, z1 = r["box"]
            dx, dy = max(x0 - px, px - x1, 0.0), max(y0 - py, py - y1, 0.0)
            near = min(near, math.hypot(dx, dy))
            if x0 - 0.5 <= px <= x1 + 0.5 and y0 - 0.5 <= py <= y1 + 0.5 and pz >= z1 - 1.0:
                under[i] += 1
        if near > farthest:
            farthest, worst = near, (px, py, pz)
    fails = [
        f"no support under '{r.get('why', r['box'])}'"
        for n, r in zip(under, regions, strict=True)
        if not n
    ]
    if farthest > reach:
        where = ", ".join(f"{v:g}" for v in worst)
        fails.append(f"support {farthest:.1f} mm from every region (max {reach:g}), at ({where})")
    return under, round(farthest, 1), fails
