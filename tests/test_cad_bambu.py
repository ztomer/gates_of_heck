"""checks/cad_bambu.py: a filament override the slice did not use fails the build (GOH.CADOVERRIDE).

The layout's filament_overrides are written into the project's filament settings; the G-code header
lists every setting the slicer used, so an override the CLI dropped (it has dropped a per-object
`enable_support` and a system profile's bed before) shows there as the profile's own value.
ZSUS v21.3.0 compensates PLA shrinkage with `filament_shrink` 99.4%: dropped, its bezel prints 2 mm
short.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "checks"))
from cad_bambu import override_mismatches

HEADER = "; filament_shrink = 99.4%\n; additional_cooling_fan_speed = 10\n; nozzle_diameter = 0.4\n"


def test_overrides_the_header_carries_pass():
    over = {"filament_shrink": ["99.4%"], "additional_cooling_fan_speed": ["10"]}
    assert override_mismatches(HEADER, over) == []


def test_an_override_the_slice_did_not_use_is_named_with_both_values():
    fails = override_mismatches(HEADER.replace("99.4%", "100%"), {"filament_shrink": ["99.4%"]})
    assert fails == ["filament override filament_shrink = 99.4% but the G-code used 100%"], fails


def test_an_override_missing_from_the_header_is_named():
    fails = override_mismatches(HEADER, {"filament_flow_ratio": ["0.98"]})
    want = "filament override filament_flow_ratio = 0.98 is not in the G-code header"
    assert fails == [want], fails


def test_a_multi_filament_value_is_compared_as_the_header_joins_it():
    header = "; filament_shrink = 99.4%,100%\n"
    assert override_mismatches(header, {"filament_shrink": ["99.4%", "100%"]}) == []
