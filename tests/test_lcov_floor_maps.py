"""GOH.CRATEFLOOR: a floors file lowers ONE crate or ONE file below the uniform floor.

`crate_floors` / `file_floors` of `gates/lcov_merge.py`'s floors file (GOH_COV_FLOORS_JSON): each
entry is {"floor": N, "reason": "..."}; the rest stay at `--floor` / `file_floor`. An entry that
lowers nothing, names nothing, or says nothing is refused (a ratchet's stale entry is a rug).
"""

import json
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT

LCOV_MERGE = REPO_ROOT / "gates" / "lcov_merge.py"


def _repo(tmp_path: Path, a_hit: int = 3, b_hit: int = 4, c_hit: int = 4) -> Path:
    """crates/a (a_hit of 4 lines), crates/b (b_hit of 4), crates/c (c_hit of 4), in one lcov."""
    proj = tmp_path / "proj"
    rows = []
    for name, hit in (("a", a_hit), ("b", b_hit), ("c", c_hit)):
        src = proj / "crates" / name / "src"
        src.mkdir(parents=True)
        (proj / "crates" / name / "Cargo.toml").write_text(
            f'[package]\nname = "{name}-rs"\nversion = "0.1.0"\n'
        )
        (src / "lib.rs").write_text("fn x() {}\n")
        rows += [f"SF:{src / 'lib.rs'}"]
        rows += [f"DA:{n},{1 if n <= hit else 0}" for n in range(1, 5)]
        rows += ["end_of_record"]
    parts = proj / "parts"
    parts.mkdir()
    (parts / "part-w.info").write_text("\n".join(rows) + "\n")
    return proj


def _run(proj: Path, floors: dict | None, *args: str):
    if floors is not None:
        (proj / "floors.json").write_text(json.dumps(floors))
    cmd = [sys.executable, str(LCOV_MERGE), *args]
    if floors is not None:
        cmd += ["--floors-json", "floors.json"]
    return subprocess.run(
        [*cmd, "parts"],
        cwd=proj,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _all(r) -> str:
    return r.stdout + r.stderr


def _entry(floor: float, reason: str = "the driver needs a real browser") -> dict:
    return {"floor": floor, "reason": reason}


# ── crate_floors ────────────────────────────────────────────────────────────────────────────────


def test_one_crate_below_the_uniform_floor_passes_when_named(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=3)  # a: 75%, b and c: 100%
    r = _run(proj, {"crate_floors": {"a": _entry(70)}}, "--floor", "95")
    assert r.returncode == 0, _all(r)
    assert "crate a: 75.0%" in _all(r), _all(r)


def test_the_same_crate_unnamed_fails_the_uniform_floor(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=3)
    # No map: the aggregate is 11/12 = 91.67% < 95: the instrument can say red.
    assert _run(proj, None, "--floor", "95").returncode == 1


def test_the_crates_not_named_stay_at_the_uniform_floor(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=3, b_hit=3)  # b is 75% too, and is not named
    r = _run(proj, {"crate_floors": {"a": _entry(70)}}, "--floor", "95")
    assert r.returncode == 1, _all(r)
    assert "87.5%" in _all(r) and "floor 95%" in _all(r), _all(r)  # b and c: 7/8


def test_a_named_crate_below_its_own_floor_fails_naming_it(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=2)  # 50%
    r = _run(proj, {"crate_floors": {"a": _entry(70)}}, "--floor", "95")
    assert r.returncode == 1, _all(r)
    assert "crate a is at 50.0%, below its floor 70%" in _all(r), _all(r)


def test_a_crate_may_be_named_by_its_package(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=3)
    assert _run(proj, {"crate_floors": {"a-rs": _entry(70)}}, "--floor", "95").returncode == 0


def test_an_entry_at_or_above_the_uniform_floor_fails(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=3)
    r = _run(proj, {"crate_floors": {"a": _entry(95)}}, "--floor", "95")
    assert r.returncode == 1, _all(r)
    assert "at or above the uniform floor" in _all(r), _all(r)


def test_an_entry_naming_no_crate_of_the_repo_fails_as_stale(tmp_path: Path) -> None:
    proj = _repo(tmp_path)
    r = _run(proj, {"crate_floors": {"gone": _entry(50)}}, "--floor", "95")
    assert r.returncode == 1, _all(r)
    assert "crate_floors['gone'] names no crate of this repo" in _all(r), _all(r)


def test_an_entry_for_a_crate_this_run_did_not_measure_is_not_stale(tmp_path: Path) -> None:
    """A repo gated crate by crate runs this once per crate with one shared map."""
    proj = _repo(tmp_path)
    parts = proj / "parts" / "part-w.info"
    keep = parts.read_text().split("end_of_record\n")[1] + "end_of_record\n"  # crate b alone
    parts.write_text(keep)
    r = _run(proj, {"crate_floors": {"a": _entry(70)}}, "--floor", "95")
    assert r.returncode == 0, _all(r)


def test_an_entry_without_a_reason_is_refused(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=3)
    r = _run(proj, {"crate_floors": {"a": {"floor": 70}}}, "--floor", "95")
    assert r.returncode == 2, _all(r)
    assert "needs a `reason`" in _all(r), _all(r)


def test_a_bare_number_entry_is_refused(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=3)
    r = _run(proj, {"crate_floors": {"a": 70}}, "--floor", "95")
    assert r.returncode == 2, _all(r)


def test_crate_floors_without_a_uniform_floor_fail(tmp_path: Path) -> None:
    proj = _repo(tmp_path)
    r = _run(proj, {"crate_floors": {"a": _entry(70)}})
    assert r.returncode == 1, _all(r)
    assert "none is set" in _all(r), _all(r)


# ── a floor within the measured spread (GOH.COVNONDET) ────────────────────────────────────────
# One tree measured more than once moved: monitor's ratchet (2026-11-18) read agent 90.35 to 90.50,
# multitop 89.67 to 89.82 and vault 90.52 to 90.76 over runs of ONE tree, and a floor set from one
# run (multitop 89.74) went red on the push gate's clean-tree run (89.67). An entry may record its
# runs (`measured`); a floor above the lowest of them is refused, never left to red at random.
MONITOR_SPREAD = {  # monitor's tools/coverage_floors.json and its reruns, 2026-11-18
    "agent": ([90.50, 90.48, 90.35], 90.3),
    "multitop": ([89.74, 89.67, 89.82], 89.6),
    "vault": ([90.68, 90.52, 90.76], 90.5),
}


def _spread(floor: float, runs: list) -> dict:
    return {**_entry(floor), "measured": runs}


def test_a_floor_inside_its_measured_spread_is_refused(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=4)
    for name, (runs, _floor) in MONITOR_SPREAD.items():
        inside = round((min(runs) + max(runs)) / 2, 2)  # multitop: the 89.74 that went red
        r = _run(proj, {"crate_floors": {"a": _spread(inside, runs)}}, "--floor", "95")
        assert r.returncode == 2, (name, _all(r))
        lo, hi = min(runs), max(runs)
        assert (
            f"crate_floors['a'] floor {inside:g}% is above the lowest of its measured runs "
            f"({lo:g}..{hi:g}%)" in _all(r)
        ), (name, _all(r))


def test_a_floor_above_every_measured_run_is_refused(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=4)
    r = _run(proj, {"crate_floors": {"a": _spread(91, [90.50, 90.48, 90.35])}}, "--floor", "95")
    assert r.returncode == 2, _all(r)
    assert "above the lowest of its measured runs" in _all(r), _all(r)


def test_monitors_floors_at_the_lowest_run_rounded_down_are_kept(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=4)
    for name, (runs, floor) in MONITOR_SPREAD.items():
        r = _run(proj, {"crate_floors": {"a": _spread(floor, runs)}}, "--floor", "95")
        assert r.returncode == 0, (name, _all(r))
    r = _run(proj, {"crate_floors": {"a": _spread(90.35, [90.50, 90.35])}}, "--floor", "95")
    assert r.returncode == 0, _all(r)  # at the lowest run: that run passes


def test_a_spread_of_one_run_is_refused(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=4)
    r = _run(proj, {"crate_floors": {"a": _spread(89.6, [89.74])}}, "--floor", "95")
    assert r.returncode == 2, _all(r)
    assert "`measured` needs two or more runs of one tree" in _all(r), _all(r)


def test_a_spread_that_is_not_numbers_is_refused(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=4)
    for bad in ("89.67..89.82", [89.67, "89.82"], [89.67, True], [89.67, 101]):
        r = _run(proj, {"crate_floors": {"a": _spread(89.6, bad)}}, "--floor", "95")
        assert r.returncode == 2, (bad, _all(r))
        assert "`measured` needs two or more runs of one tree" in _all(r), (bad, _all(r))


def test_a_file_floor_inside_its_measured_spread_is_refused(tmp_path: Path) -> None:
    proj = _repo(tmp_path)
    f = "crates/a/src/lib.rs"
    floors = {"file_floor": 95, "file_floors": {f: _spread(89.74, [89.74, 89.67, 89.82])}}
    r = _run(proj, floors, "--floor", "95")
    assert r.returncode == 2, _all(r)
    assert f"file_floors[{f!r}] floor 89.74% is above the lowest" in _all(r), _all(r)


# ── file_floors ─────────────────────────────────────────────────────────────────────────────────


def test_one_file_below_the_per_file_floor_passes_when_named(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=3, b_hit=4, c_hit=4)  # a/lib.rs is 75%
    floors = {"file_floor": 90, "file_floors": {"crates/a/src/lib.rs": _entry(70)}}
    r = _run(proj, floors, "--floor", "0")
    assert r.returncode == 0, _all(r)


def test_the_same_file_unnamed_fails_the_per_file_floor(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=3)
    r = _run(proj, {"file_floor": 90}, "--floor", "0")
    assert r.returncode == 1, _all(r)
    assert "below per-file floor 90%" in _all(r), _all(r)


def test_files_not_named_stay_at_the_per_file_floor(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=3, b_hit=3)
    floors = {"file_floor": 90, "file_floors": {"crates/a/src/lib.rs": _entry(70)}}
    r = _run(proj, floors, "--floor", "0")
    assert r.returncode == 1, _all(r)
    assert "1 file below per-file floor 90%" in _all(r), _all(r)
    assert "crates/b/src/lib.rs" in _all(r), _all(r)


def test_a_file_below_its_own_floor_fails(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=2)  # 50%
    floors = {"file_floor": 90, "file_floors": {"crates/a/src/lib.rs": _entry(70)}}
    assert _run(proj, floors, "--floor", "0").returncode == 1


def test_a_file_entry_at_or_above_the_per_file_floor_fails(tmp_path: Path) -> None:
    proj = _repo(tmp_path, a_hit=3)
    floors = {"file_floor": 70, "file_floors": {"crates/a/src/lib.rs": _entry(70)}}
    r = _run(proj, floors, "--floor", "0")
    assert r.returncode == 1, _all(r)
    assert "at or above the uniform floor" in _all(r), _all(r)


def test_a_file_entry_naming_no_file_fails_as_stale(tmp_path: Path) -> None:
    proj = _repo(tmp_path)
    floors = {"file_floor": 90, "file_floors": {"crates/a/src/gone.rs": _entry(50)}}
    r = _run(proj, floors, "--floor", "0")
    assert r.returncode == 1, _all(r)
    assert "crates/a/src/gone.rs" in _all(r) and "stale" in _all(r), _all(r)


def test_file_floors_without_file_floor_fail(tmp_path: Path) -> None:
    proj = _repo(tmp_path)
    r = _run(proj, {"file_floors": {"crates/a/src/lib.rs": _entry(50)}}, "--floor", "0")
    assert r.returncode == 1, _all(r)
    assert "none is set" in _all(r), _all(r)


def test_a_file_entry_without_a_reason_is_refused(tmp_path: Path) -> None:
    proj = _repo(tmp_path)
    floors = {"file_floor": 90, "file_floors": {"crates/a/src/lib.rs": {"floor": 50}}}
    assert _run(proj, floors, "--floor", "0").returncode == 2
