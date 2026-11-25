#!/usr/bin/env python3
"""Travel over open air in sliced G-code: where a PLA print strings (the CAD layer; GOH.CAD).

A string is ooze the nozzle drags across a gap while it travels without extruding. Over the part the
next layer buries it; over open air (a bezel's screen opening, the gap between two walls) it is left
as a hair, which is what the ZSUS bezel came off the plate with (2026-11-19). The measure is the
travel more than NEAR mm from anything that layer extrudes, read from the G-code the printer runs.

    air_travel(gcode_text) -> {"total_mm", "longest_mm", "spans", "layers"}
    cad_travel.py plate.gcode ...      prints the numbers for each file

Bambu Studio G-code: absolute XY (G90), relative E (M83), a layer starts at "; CHANGE_LAYER", arcs
are G2/G3 with I/J. A move in XY with E > 0 is extrusion; a wipe (E < 0) and a plain move are
travel. Each layer's extrusion is drawn on a CELL mm raster; a travel sample is over air when no
drawn cell lies within NEAR mm of its own cell (cell-centre distance, the exact Euclidean distance
transform's). Standard library only; ported from ZSUS CAD/gcode_travel.py (numpy + scipy), same
numbers.

Travel more than 2 mm from anything its layer extrudes, in sliced G-code: where PLA strings. A
stdlib port of ZSUS's numpy original, identical on its five plates.
"""

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import math
import re
import sys

CELL = 0.5  # mm, the raster the layer's extrusion is drawn on
NEAR = 2.0  # mm: travel nearer than this to the layer's own extrusion is over the part (or infill)
STEP = 0.25  # mm between samples along a move
_WORD = re.compile(r"([XYZEIJ])(-?\d*\.?\d+)")


def _linspace(n: int) -> list[float]:
    """numpy.linspace(0, 1, n) to the bit: i * (1 / (n - 1)), the last exactly 1."""
    step = 1.0 / (n - 1)
    return [i * step for i in range(n - 1)] + [1.0]


def _arc(x0, y0, x1, y1, i, j, cw):
    cx, cy = x0 + i, y0 + j
    a0, a1 = math.atan2(y0 - cy, x0 - cx), math.atan2(y1 - cy, x1 - cx)
    r = math.hypot(x0 - cx, y0 - cy)
    sweep = a1 - a0
    if cw and sweep >= 0:
        sweep -= 2 * math.pi
    if not cw and sweep <= 0:
        sweep += 2 * math.pi
    n = max(2, int(abs(sweep) * r / STEP) + 1)
    return [
        (cx + r * math.cos(a0 + sweep * t), cy + r * math.sin(a0 + sweep * t)) for t in _linspace(n)
    ]


def _line(x0, y0, x1, y1):
    n = max(2, int(math.hypot(x1 - x0, y1 - y0) / STEP) + 1)
    return [(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t) for t in _linspace(n)]


def layers(gcode: str):
    """Per layer: (extruded sample points, [travel polylines]) in bed mm."""
    out, ext, trav = [], [], []
    x = y = 0.0
    started = False
    for raw in gcode.splitlines():
        if raw.startswith(
            ("; MACHINE_END_GCODE_START", "; EXECUTABLE_BLOCK_END")
        ):  # park and present
            break
        if raw.startswith("; CHANGE_LAYER"):
            if started:
                out.append((ext, trav))
            ext, trav, started = [], [], True
            continue
        if not raw.startswith(("G0", "G1", "G2", "G3")):
            continue
        code = raw.split(None, 1)[0]
        w = dict(_WORD.findall(raw.split(";", 1)[0]))
        if "X" not in w and "Y" not in w:
            continue
        nx, ny = float(w.get("X", x)), float(w.get("Y", y))
        if (
            not started
        ):  # the start G-code (purge line, bed level): only where the head ends up counts
            x, y = nx, ny
            continue
        pts = (
            _arc(x, y, nx, ny, float(w.get("I", 0)), float(w.get("J", 0)), code == "G2")
            if code in ("G2", "G3")
            else _line(x, y, nx, ny)
        )
        if float(w.get("E", 0)) > 0:
            ext.extend(pts)
        else:
            trav.append(pts)
        x, y = nx, ny
    if started:
        out.append((ext, trav))
    return out


def _disc(near: float) -> list[tuple[int, int]]:
    r = near / CELL
    k = math.ceil(r)
    return [
        (i, j)
        for i in range(-k, k + 1)
        for j in range(-k, k + 1)
        if math.hypot(i, j) * CELL <= near
    ]


def air_travel(gcode: str, near: float = NEAR) -> dict:
    tot, longest, spans, n_layers = 0.0, 0.0, 0, 0
    disc = _disc(near)
    for ext, trav in layers(gcode):
        if not ext or not trav:
            continue
        n_layers += 1
        lo_x = min(*(p[0] for p in ext), *(p[0] for t in trav for p in t)) - 2 * near
        lo_y = min(*(p[1] for p in ext), *(p[1] for t in trav for p in t)) - 2 * near
        drawn = {(math.floor((px - lo_x) / CELL), math.floor((py - lo_y) / CELL)) for px, py in ext}
        memo: dict[tuple[int, int], bool] = {}

        def over_air(cell, drawn=drawn, memo=memo):
            hit = memo.get(cell)
            if hit is None:
                ci, cj = cell
                hit = memo[cell] = not any((ci + di, cj + dj) in drawn for di, dj in disc)
            return hit

        for pts in trav:
            air = [
                over_air((math.floor((px - lo_x) / CELL), math.floor((py - lo_y) / CELL)))
                for px, py in pts
            ]
            run = 0.0
            for k in range(len(pts) - 1):
                s = math.hypot(pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1])
                if air[k] and air[k + 1]:
                    tot += s
                    run += s
                else:
                    if run > 0:
                        spans += 1
                        longest = max(longest, run)
                    run = 0.0
            if run > 0:
                spans += 1
                longest = max(longest, run)
    return {
        "total_mm": round(tot, 1),
        "longest_mm": round(longest, 1),
        "spans": spans,
        "layers": n_layers,
    }


if __name__ == "__main__":
    for path in sys.argv[1:]:
        with open(path, errors="ignore") as fh:
            print(path, air_travel(fh.read()))
