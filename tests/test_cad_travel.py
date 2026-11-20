"""checks/cad_travel.py: travel more than 2 mm from a layer's extrusion is over open air (GOH.CAD).

Pinned against the numpy original (ZSUS CAD/gcode_travel.py) on the five plates of the ZSUS v21.2.1
project: identical totals, longest hops, spans and layers. These cases plant the shapes by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path

from cad_fixtures import gcode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "checks"))
from cad_travel import air_travel

SQUARE = [
    ("e", 10, 0),
    ("e", 10, 10),
    ("e", 0, 10),
    ("e", 0, 0),
]  # a 10 mm perimeter from the origin


def test_a_hop_across_a_gap_is_one_span_over_air():
    g = gcode([(0.2, [("t", 0, 0), *SQUARE, ("t", 50, 0)])])
    r = air_travel(g)
    assert r["spans"] == 1 and r["layers"] == 1, r
    assert 37.0 <= r["longest_mm"] <= 38.5, (
        r
    )  # 40 mm out from the square's edge, less the 2 mm kept near it


def test_travel_along_the_part_is_not_air():
    g = gcode(
        [(0.2, [*SQUARE, ("t", 1, 1), ("t", 9, 1), ("t", 9, 9)])]
    )  # within 1 mm of the perimeter
    assert air_travel(g)["spans"] == 0


def test_the_middle_of_a_hollow_part_is_air():
    # 5 mm from every wall: the hole a bezel's screen opening is, where its strings were
    g = gcode([(0.2, [*SQUARE, ("t", 5, 5)])])
    assert air_travel(g)["spans"] == 1


def test_a_hop_between_two_islands_counts_only_the_gap():
    far = [("t", 40, 0), ("e", 50, 0), ("e", 50, 10), ("e", 40, 10), ("e", 40, 0)]
    g = gcode([(0.2, [*SQUARE, *far, ("t", 10, 0)])])  # out to the far island and back
    r = air_travel(g)
    assert r["spans"] == 2, r
    assert 50.0 <= r["total_mm"] <= 53.0, (
        r
    )  # 2 x (30 mm between the islands less 2 mm kept at each end)


def test_each_layer_judges_its_own_extrusion():
    # layer 2 extrudes nothing near the hop that layer 1's square covered
    g = gcode([(0.2, [*SQUARE, ("t", 1, 1)]), (0.4, [("t", 60, 60), ("e", 70, 60), ("t", 1, 1)])])
    r = air_travel(g)
    # layer 1's hop to (1, 1) is beside its wall; both of layer 2's hops are far from its one line
    assert r["layers"] == 2 and r["spans"] == 2, r


def test_the_start_gcode_and_the_end_are_not_counted():
    g = "G1 X0 Y0\nG1 X200 Y200\n" + gcode([(0.2, [*SQUARE])]) + "G1 X250 Y250\n"
    assert air_travel(g)["spans"] == 0
