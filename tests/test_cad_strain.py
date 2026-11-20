"""checks/cad_strain.py: a printed snap's leaves are gated by bending strain (GOH.CAD).

Calibrated on the socket that broke: ZSUS v21's first print, six leaves, a 0.7 mm snap and slots
rooted at z 1.3, read 3.07 % and snapped; eight leaves, a 0.3 mm snap and slots 2.3 mm deeper read
0.99 % and held. PLA tears near 1.5 % bent across its layers.
"""

from __future__ import annotations

import json

from conftest import run_check

STRAIN = "checks/cad_strain.py"
SOCKET = {  # ZSUS CAD/build_part_a_v21.py at 8a610a6
    "ball_d": 17.0, "cup_r": 8.6, "ball_c": 6.2, "mouth_r": 8.2, "lead_in": 0.5, "n_slots": 8,
    "slot_w": 1.4, "z_slot": -1.0, "r_core": 9.975, "z_core_top": 9.3, "z_tip": 11.8, "r_tip": 8.95,
    "load_below_tip": 0.7, "strain_max": 1.5,
}  # fmt: skip


def _run(repo, spec, *args):
    (repo / "socket.json").write_text(json.dumps(spec))
    r = run_check(repo, STRAIN, "socket.json", *args)
    return r.returncode, r.stdout + r.stderr


def test_the_socket_that_holds_reads_under_the_limit(repo):
    code, out = _run(repo, SOCKET)
    assert code == 0, out
    assert "peak strain 0.99 %" in out, out


def test_the_socket_that_broke_reads_over_it(repo):
    code, out = _run(repo, SOCKET, "--set", "n_slots=6", "slot_w=1.6", "mouth_r=7.8", "z_slot=1.3")
    assert code == 1, out
    assert "peak strain 3.07 %" in out and "tears" in out, out


def test_tables_give_the_same_profile(repo):
    # r_out as a table that IS the tapered core: the same number as the closed form
    spec = dict(SOCKET, r_out=[[-5, 9.975], [9.3, 9.975], [11.8, 8.95]])
    code, out = _run(repo, spec)
    assert code == 0 and "peak strain 0.99 %" in out, out


def test_a_spec_missing_a_key_is_a_usage_error(repo):
    spec = dict(SOCKET)
    del spec["ball_d"]
    code, out = _run(repo, spec)
    assert code == 2, out


def test_a_layout_names_its_snaps(repo):
    (repo / "socket.json").write_text(
        json.dumps(dict(SOCKET, n_slots=6, slot_w=1.6, mouth_r=7.8, z_slot=1.3))
    )
    (repo / "layout.json").write_text(json.dumps({"objects": [], "strain": ["socket.json"]}))
    r = run_check(repo, STRAIN, "--layout", "layout.json")
    assert r.returncode == 1 and "3.07 %" in r.stdout + r.stderr, r.stdout + r.stderr


def test_a_layout_without_snaps_says_so(repo):
    (repo / "layout.json").write_text(json.dumps({"objects": []}))
    r = run_check(repo, STRAIN, "--layout", "layout.json")
    assert r.returncode == 0 and "names no snap" in r.stdout + r.stderr, r.stdout + r.stderr
