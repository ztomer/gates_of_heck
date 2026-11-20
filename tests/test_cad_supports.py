"""checks/cad_supports.py: slicer warnings and support where a part names it (GOH.CAD).

The warnings Bambu Studio shows over a plate ("floating cantilever", "floating regions", "large
overhangs") are NOT in the sliced 3MF's slice_info.config; the CLI writes them to result.json and
its log, so a gate reading slice_info passed ZSUS v21.2.0's bezel with a 17 mm snap arm in mid-air.
And a part that needs support names where: each box becomes a support enforcer, and the sliced
G-code must put support under every box and nowhere far from one (tree(auto) filled a 1.6 mm USB-C
slot).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "checks"))
from cad_supports import add_enforcers, gate_regions, slice_warnings, support_points

CANTILEVER = (
    "It seems object Part_B_Upper has floating cantilever. "
    "Please re-orient the object or enable support generation."
)


def test_warnings_come_from_result_json_and_the_log_once_each():
    result = json.dumps(
        {
            "sliced_plates": [
                {"id": 1, "warning_message": ""},
                {"id": 4, "warning_message": CANTILEVER},
            ]
        }
    )
    log = (
        "[warning] [BambuStudio.cpp:1845 run] cli mode\n"
        "[warning] [BambuStudio.cpp:7688 run] plate 4: found NON_CRITICAL slicing warnings: "
        f"{CANTILEVER}\n"
        "[warning] [BambuStudio.cpp:7688 run] plate 2: found CRITICAL slicing warnings: "
        "Floating regions\n"
    )
    w = slice_warnings(result, log)
    assert w == {4: [CANTILEVER], 2: ["Floating regions"]}, w


def test_a_log_level_warning_is_not_a_slicing_warning():
    log = (
        "[warning] [SupportParameters.hpp:204 SupportParameters] "
        "tree support default to organic support\n"
    )
    assert slice_warnings(json.dumps({"sliced_plates": []}), log) == {}


REGIONS = [
    {"why": "arm tip", "box": [0, 2, 0, 6, 0, 4.7]},
    {"why": "lip corner", "box": [20, 24, 0, 5, 16.9, 17.6]},
]


def test_support_under_every_region_and_none_far_passes():
    pts = [(1, 3, 4.6), (22, 2, 17.0), (21, -9, 0.2)]  # the last a trunk's foot, 9 mm off: allowed
    under, farthest, fails = gate_regions(pts, REGIONS, reach=15.0)
    assert fails == [] and under == [1, 1] and farthest == 9.0, (under, farthest, fails)


def test_a_region_without_support_is_named():
    _under, _far, fails = gate_regions([(1, 3, 4.6)], REGIONS, reach=15.0)
    assert fails == ["no support under 'lip corner'"], fails


def test_support_below_the_region_top_does_not_count_as_under_it():
    # a trunk passing under the lip's footprint at 5 mm does not carry a lip at 17 mm
    _under, _far, fails = gate_regions([(1, 3, 4.6), (22, 2, 5.0)], REGIONS, reach=15.0)
    assert fails == ["no support under 'lip corner'"], fails


def test_support_far_from_every_region_fails_with_where():
    _under, far, fails = gate_regions(
        [(1, 3, 4.6), (22, 2, 17.0), (-32, 150, 0.2)], REGIONS, reach=15.0
    )
    assert far > 140 and len(fails) == 1 and "(-32, 150, 0.2)" in fails[0], fails


GCODE = """; CHANGE_LAYER
G1 Z0.2
; FEATURE: Outer wall
G1 X10 Y10 E1
; FEATURE: Support
G1 X11 Y12 E0.2
G1 X13 Y12
G1 X14 Y12 E-0.5
; FEATURE: Support interface
G1 X15 Y16 E0.1
; FEATURE: Inner wall
G1 X30 Y30 E1
"""


def test_support_points_are_the_extruding_moves_of_support_features():
    assert support_points(GCODE) == [(11.0, 12.0, 0.2), (15.0, 16.0, 0.2)]


def _project():
    """The shape Bambu Studio's import writes: a wrapper object, one part, model_settings."""
    model = (
        '<model><resources><object id="2" type="model"><components>'
        '<component p:path="/3D/Objects/object_1.model" objectid="1"'
        ' transform="1 0 0 0 1 0 0 0 1 -5 3 9.3"/>'
        "</components></object></resources></model>"
    )
    obj = (
        '<model><resources>\n  <object id="1" type="model"><mesh/></object>\n </resources>'
        "\n</model>"
    )
    ms = (
        '<config>\n  <object id="2">\n    <part id="1" subtype="normal_part">\n    </part>'
        "\n  </object>\n</config>"
    )
    return {"3D/3dmodel.model": model.encode(), "3D/Objects/object_1.model": obj.encode(),
            "Metadata/model_settings.config": ms.encode()}  # fmt: skip


def test_enforcers_are_parts_of_their_object_in_its_mesh_frame():
    files = _project()
    nxt = add_enforcers(files, "2", REGIONS, 3)
    assert nxt == 5
    model, obj = files["3D/3dmodel.model"].decode(), files["3D/Objects/object_1.model"].decode()
    ms = files["Metadata/model_settings.config"].decode()
    assert model.count("<component ") == 3 and 'objectid="3"' in model and 'objectid="4"' in model
    assert ms.count('subtype="support_enforcer"') == 2 and "support: lip corner" in ms
    assert re.search(r'<object id="3" type="model">', obj)
    # the box's x0 = 0 sits at 0 - (-5) = 5 in the mesh frame the component's transform moves back
    assert '<vertex x="5.000000" y="-3.000000" z="-9.300000"/>' in obj, obj
    assert obj.count("<triangle ") == 24
