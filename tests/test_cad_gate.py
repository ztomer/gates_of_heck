"""gates/cad_gate.sh: the CAD layer over one layout (GOH.CAD).

The config refusals and the mesh step run everywhere. The slicer half needs Bambu Studio: the
end-to-end cases build an L-shaped part whose arm is a 17 mm beam starting 4.5 mm up in mid-air,
the ZSUS bezel's snap arm in miniature, and prove the gate red on its "floating cantilever" and
green once a support region carries the arm's tip.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from cad_fixtures import box, write_stl
from conftest import run_gate

GATE = "gates/cad_gate.sh"
BAMBU = os.environ.get("GOH_CAD_BAMBU", "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio")


def _layout(repo: Path, objects, **extra) -> None:
    lay = {
        "machine": "Bambu Lab P1S 0.4 nozzle",
        "process": "0.20mm Standard @BBL X1C",
        "filament": "Generic PLA",
        "expect": {
            "printable_area": ["0x0", "256x0", "256x256", "0x256"],
            "nozzle_diameter": ["0.4"],
        },
        "plates": {"1": "1 test"},
        "objects": objects,
        "out_3mf": "test.3mf",
        "report": "report.json",
        **extra,
    }
    (repo / "cad").mkdir(exist_ok=True)
    (repo / "cad" / "layout.json").write_text(json.dumps(lay))
    (repo / ".gatesrc").write_text("GOH_CAD_LAYOUT=cad/layout.json\n")


def _gate(repo):
    r = run_gate(repo, GATE)
    return r.returncode, r.stdout + r.stderr


def test_an_unconfigured_layer_refuses_by_name(repo):
    code, out = _gate(repo)
    assert code == 2 and "GOH_CAD_LAYOUT is unset" in out, out


def test_a_missing_layout_refuses(repo):
    (repo / ".gatesrc").write_text("GOH_CAD_LAYOUT=cad/nope.json\n")
    code, out = _gate(repo)
    assert code == 2 and "does not exist" in out, out


def test_a_broken_stl_stops_the_gate_before_the_slicer(repo):
    write_stl(repo / "STLs" / "a.stl", box(0, 10, 0, 10, 0, 10)[:-1])
    _layout(
        repo, [{"name": "a", "stl": "STLs/a.stl", "plate": 1, "centre": [128, 128], "shells": 1}]
    )
    code, out = _gate(repo)
    assert code == 1 and "boundary edge" in out, out
    assert "Bambu Studio project" not in out, out


# An L: a 10 x 10 x 14 mm post, and an arm off its side, 17 mm long, from z 4.5 to 8.5.
L_PART = box(0, 10, 0, 10, 0, 14) + box(10, 27, 3, 7, 4.5, 8.5)
ARM_TIP = {"why": "arm tip: 17 mm over open air", "box": [21, 27.3, 2.7, 7.3, 0, 5.0]}
needs_bambu = pytest.mark.skipif(
    not os.path.exists(BAMBU), reason=f"no Bambu Studio CLI at {BAMBU}"
)


def _l_part(repo):
    # the arm overlaps the post's face, not one closed solid: write it as one body built by hand
    write_stl(repo / "STLs" / "l.stl", L_PART)


@pytest.mark.slow
@needs_bambu
def test_a_floating_cantilever_is_red_and_its_support_region_makes_it_green(repo):
    _l_part(repo)
    obj = {"name": "l", "stl": "STLs/l.stl", "plate": 1, "centre": [128, 128]}
    _layout(repo, [obj])
    code, out = _gate(repo)
    assert code == 1 and "floating cantilever" in out, out
    _layout(repo, [dict(obj, support_regions=[ARM_TIP])])
    code, out = _gate(repo)
    assert code == 0, out  # a green step keeps its detail to itself; the next case reads it red


@pytest.mark.slow
@needs_bambu
def test_support_far_from_its_region_is_red(repo):
    _l_part(repo)
    wrong = {"why": "a region on the far side", "box": [-60, -55, 0, 5, 0, 5.0]}
    obj = {
        "name": "l",
        "stl": "STLs/l.stl",
        "plate": 1,
        "centre": [128, 128],
        "support_regions": [ARM_TIP, wrong],
    }
    _layout(repo, [obj], support_reach=15.0)
    code, out = _gate(repo)
    assert code == 1 and "no support under 'a region on the far side'" in out, out
