"""checks/cad_mesh.py: every STL judged as the slicer's reader rebuilds it (GOH.CAD).

An STL is a triangle soup; a slicer re-merges it by EXACT float32 position (admesh), so a mesh
that was watertight in memory can ship as edges shared by four faces. Each defect below is one a
generator shipped (ZSUS v21.0.0: 171 / 336 / 1084 pinched edges in three parts that sliced without
a warning) or one a boolean makes silently: a hole, a turned-over face, an inside-out part, a
loose second body.
"""

from __future__ import annotations

from cad_fixtures import box, flipped, write_stl
from conftest import run_check

MESH = "checks/cad_mesh.py"


def _run(repo, *args):
    r = run_check(repo, MESH, *args)
    return r.returncode, r.stdout + r.stderr


def test_a_closed_box_passes(repo):
    write_stl(repo / "a.stl", box(0, 10, 0, 10, 0, 10))
    code, out = _run(repo, "a.stl")
    assert code == 0, out
    assert "a.stl" in out and "1 body" in out, out


def test_a_missing_face_is_a_hole(repo):
    write_stl(repo / "a.stl", box(0, 10, 0, 10, 0, 10)[:-1])
    code, out = _run(repo, "a.stl")
    assert code == 1, out
    assert "a.stl" in out and "boundary edge" in out, out


def test_two_bodies_sharing_one_edge_are_pinched(repo):
    write_stl(repo / "a.stl", box(0, 10, 0, 10, 0, 10) + box(10, 20, 10, 20, 0, 10))
    code, out = _run(repo, "a.stl")
    assert code == 1, out
    assert "pinched edge" in out, out


def test_a_turned_over_face_breaks_the_winding(repo):
    tris = box(0, 10, 0, 10, 0, 10)
    tris[3] = flipped([tris[3]])[0]
    write_stl(repo / "a.stl", tris)
    code, out = _run(repo, "a.stl")
    assert code == 1, out
    assert "winding" in out, out


def test_an_inside_out_part_has_negative_volume(repo):
    write_stl(repo / "a.stl", flipped(box(0, 10, 0, 10, 0, 10)))
    code, out = _run(repo, "a.stl")
    assert code == 1, out
    assert "inside out" in out, out


def test_the_body_count_is_held_to_the_expected(repo):
    write_stl(repo / "a.stl", box(0, 10, 0, 10, 0, 10) + box(20, 30, 0, 10, 0, 10))
    code, out = _run(repo, "a.stl")
    assert code == 0, out  # two closed bodies are a valid file
    code, out = _run(repo, "--shells", "a.stl=1", "a.stl")
    assert code == 1, out
    assert "2 bodies, expected 1" in out, out
    code, out = _run(repo, "--shells", "a.stl=2", "a.stl")
    assert code == 0, out


def test_positions_merge_only_when_equal_as_float32(repo):
    # Two boxes 3e-9 mm apart near zero: distinct float32 values, so admesh reads two closed bodies.
    # A reader that rounds to 8 decimals (trimesh's default merge) welds them into one pinched body.
    write_stl(repo / "a.stl", box(-10, 0, 0, 10, 0, 10) + box(3e-9, 10, 0, 10, 0, 10))
    code, out = _run(repo, "--shells", "a.stl=2", "a.stl")
    assert code == 0, out


def test_flat_overhang_area_is_reported_and_can_be_capped(repo):
    # a 10 x 10 slab 5 mm over another: its 100 mm2 underside faces the plate; the lower one's
    # is the bed layer
    write_stl(repo / "b.stl", box(0, 10, 0, 10, 5, 6) + box(0, 10, 0, 10, 0, 1))
    code, out = _run(repo, "--overhang-max", "150", "b.stl")
    assert code == 0, out
    assert "overhang 100.0 mm2" in out, out
    code, out = _run(repo, "--overhang-max", "50", "b.stl")
    assert code == 1, out
    assert "overhang 100.0 mm2 > 50" in out, out


def test_a_triangle_float32_collapsed_is_a_sliver(repo):
    write_stl(repo / "a.stl", [*box(0, 10, 0, 10, 0, 10), ((5, 5, 5), (5, 5, 5), (5, 5, 5 + 1e-9))])
    code, out = _run(repo, "a.stl")
    assert code == 1, out
    assert "1 zero-area triangle" in out, out
    assert "pinched" not in out and "boundary" not in out, (
        out
    )  # its self-edges are not edges a reader pairs


def test_a_missing_face_is_counted_per_edge(repo):
    write_stl(repo / "a.stl", box(0, 10, 0, 10, 0, 10)[:-1])
    _code, out = _run(repo, "a.stl")
    assert "3 boundary edges" in out, out


def test_no_file_is_a_usage_error(repo):
    code, out = _run(repo)
    assert code == 2, out
    code, out = _run(repo, "missing.stl")
    assert code == 2, out


def test_a_layout_names_the_files_and_their_bodies(repo):
    import json

    write_stl(repo / "STLs" / "a.stl", box(0, 10, 0, 10, 0, 10))
    write_stl(repo / "STLs" / "b.stl", box(0, 10, 0, 10, 0, 10) + box(20, 30, 0, 10, 0, 10))
    lay = {
        "objects": [
            {"name": "a", "stl": "STLs/a.stl", "shells": 1},
            {"name": "b", "stl": "STLs/b.stl", "shells": 1},
        ]
    }
    (repo / "cad").mkdir()
    (repo / "cad" / "layout.json").write_text(json.dumps(lay))
    code, out = _run(repo, "--layout", "cad/layout.json", "--root", ".")
    assert code == 1 and "b.stl" in out and "2 bodies, expected 1" in out, out
    lay["objects"][1]["shells"] = 2
    (repo / "cad" / "layout.json").write_text(json.dumps(lay))
    code, out = _run(repo, "--layout", "cad/layout.json", "--root", ".")
    assert code == 0, out


def test_zero_stored_normals_are_a_file_the_slicer_refuses(repo):
    write_stl(repo / "a.stl", box(0, 10, 0, 10, 0, 10), zero_normals=True)
    code, out = _run(repo, "a.stl")
    assert code == 1 and "12 facets with a zero stored normal" in out, out
