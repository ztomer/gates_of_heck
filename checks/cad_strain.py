#!/usr/bin/env python3
"""Peak bending strain in the leaves of a printed snap-in socket (the CAD layer; GOH.CAD).

    cad_strain.py SPEC.json [SPEC.json ...]        exit 1 when a socket is over its limit
    cad_strain.py SPEC.json --set n_slots=6 mouth_r=7.8 z_slot=1.3
    cad_strain.py --layout LAYOUT.json [--root DIR]  the specs its "strain" list names

A printed snap works by bending, and FDM PLA tears near 1.5 % strain bent ACROSS its layer lines,
which is how a vertical leaf bends. Each leaf is a cantilever from the slot root (`z_slot`) up to
the load, where the ball's widest band meets the mouth; its section at height z is the annular
sector between the seat (`r_in`) and the outer core (`r_out`), one leaf wide less half a slot each
side, bent radially. The ball imposes a tip deflection d = ball_d / 2 - mouth_r, so

    strain(z) = d * arm(z) * c(z) / I(z) / integral(arm^2 / I dz),   arm = z_load - z

A model, not a test: it explains the ZSUS break (6 leaves, 0.7 mm snap, slots to z 1.3: 3.07 %) and
gates the class (8 leaves, 0.3 mm snap, slots to z -1.0: 0.99 %). Calibrate a new socket red on the
geometry that broke. The generator writes the spec from its own constants, so it cannot drift.

Spec keys (mm; % for the limit): ball_d, cup_r, ball_c (seat sphere centre height), mouth_r,
lead_in, n_slots, slot_w, z_slot, r_core (outer radius up to z_core_top), z_core_top, z_tip, r_tip
(the outer radius tapers linearly to r_tip at z_tip), load_below_tip (the load sits at z_tip -
lead_in - this), strain_max. Or "r_in" / "r_out" as [[z, r], ...] tables (piecewise linear). Exit 2:
unreadable spec. Standard library only; ported from ZSUS CAD/build_part_a_v21.py check_leaf_strain
(numpy), same numbers.
"""

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import itertools
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tui.lib import err, ok, warn


def _linspace(a: float, b: float, n: int) -> list[float]:
    step = (b - a) / (n - 1)
    return [a + i * step for i in range(n - 1)] + [b]


def _interp(table, z: float) -> float:
    pts = sorted((float(a), float(b)) for a, b in table)
    if z <= pts[0][0]:
        return pts[0][1]
    for (z0, r0), (z1, r1) in itertools.pairwise(pts):
        if z <= z1:
            return r0 + (r1 - r0) * (z - z0) / (z1 - z0)
    return pts[-1][1]


def profiles(p: dict):
    """(r_in(z), r_out(z)): tables if given, else a seat sphere in a tapered core (ZSUS's)."""
    if "r_in" in p:

        def r_in(z):
            return _interp(p["r_in"], z)
    else:

        def r_in(z):
            s = p["cup_r"] ** 2 - (z - p["ball_c"]) ** 2
            r = math.sqrt(s) if s > 0 else 0.0
            return max(r, p["mouth_r"]) if z > p["ball_c"] else r

    if "r_out" in p:

        def r_out(z):
            return _interp(p["r_out"], z)
    else:

        def r_out(z):
            if z <= p["z_core_top"]:
                return p["r_core"]
            return p["r_core"] + (p["r_tip"] - p["r_core"]) * (z - p["z_core_top"]) / (
                p["z_tip"] - p["z_core_top"]
            )

    return r_in, r_out


def leaf_strain(p: dict, n_z: int = 200):
    """(peak strain %, its height, the snap per leaf)."""
    r_in, r_out = profiles(p)
    z_load = p["z_tip"] - p["lead_in"] - p["load_below_tip"]
    zs = _linspace(p["z_slot"], z_load, n_z)
    inertia, reach = [], []
    for z in zs:
        ri, ro = r_in(z), r_out(z)
        half = math.pi / p["n_slots"] - (p["slot_w"] / 2) / ((ri + ro) / 2)
        rs, ts = _linspace(ri, ro, 50), _linspace(-half, half, 100)
        cell = ((ro - ri) / 49) * (2 * half / 99)
        ys = [r * math.cos(t) for t in ts for r in rs]
        das = [r * cell for _t in ts for r in rs]
        yb = sum(y * a for y, a in zip(ys, das, strict=True)) / sum(das)
        inertia.append(sum((y - yb) ** 2 * a for y, a in zip(ys, das, strict=True)))
        reach.append(max(abs(y - yb) for y in ys))
    f = [(z_load - z) ** 2 / i for z, i in zip(zs, inertia, strict=True)]
    integral = sum((zs[k + 1] - zs[k]) * (f[k] + f[k + 1]) / 2 for k in range(len(zs) - 1))
    d = p["ball_d"] / 2 - p["mouth_r"]
    eps = [
        d * (z_load - z) * c / i / integral * 100
        for z, c, i in zip(zs, reach, inertia, strict=True)
    ]
    k = max(range(len(eps)), key=eps.__getitem__)
    return eps[k], zs[k], d


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    sets = argv[argv.index("--set") + 1 :] if "--set" in argv else []
    specs = argv[: argv.index("--set")] if "--set" in argv else list(argv)
    if specs[:1] == ["--layout"] and len(specs) >= 2:
        layout = specs[1]
        root = (
            specs[3]
            if specs[2:3] == ["--root"] and len(specs) >= 4
            else os.path.dirname(os.path.abspath(layout))
        )
        try:
            named = json.loads(Path(layout).read_text()).get("strain", [])
        except (OSError, ValueError) as e:
            err(f"cad_strain: {layout} is not a CAD layout: {e}")
            return 2
        if not named:
            warn(f'cad_strain: {layout} names no snap to gate (layout key "strain": [spec, ...])')
            return 0
        specs = [os.path.relpath(os.path.join(root, s)) for s in named]
    over = 0
    for path in specs:
        try:
            p = json.loads(Path(path).read_text())
            for kv in sets:
                k, v = kv.split("=", 1)
                p[k] = int(v) if k == "n_slots" else float(v)
            eps, z, d = leaf_strain(p)
        except (OSError, ValueError, KeyError, ZeroDivisionError) as e:
            err(f"{path}: not a socket spec: {e}")
            return 2
        line = (f"{path}: {p['n_slots']} leaves, {d:.2f} mm snap per leaf, peak strain {eps:.2f} % "
                f"at z {z:.1f} (limit {p['strain_max']:g} %)")  # fmt: skip
        if eps > p["strain_max"]:
            over += 1
            err(line + " -- a leaf this stiff tears snapping the ball in")
        else:
            ok(line)
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
