"""No test file churns the disk: each is judged by its worst test, on any selection (GOH.FSEVQUIET).

tests/_fs_ceilings.py holds the rule; planted sessions prove it through the plugin, whole and
partial, and the lists in this tree are held to name only what stands.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import fs_events
import pytest
from _fs_ceilings import by_test, faults, readings, settled, today
from conftest import REPO_ROOT
from test_fs_churn import CHURNY, QUIET, XDIST, _session

TREE_READS = ("tests/",)  # the lists name files there
CEILING = json.loads((REPO_ROOT / "tests" / "fs_events_ceiling.json").read_text())
TODAY = 738  # the names of tests/fs_churn_today.txt, only ever lowered
NAMED = 10  # files with ceilings of their own, only ever lowered (7 to 10, 2026-11-26)
darwin = pytest.mark.skipif(sys.platform != "darwin", reason="FSEvents is macOS only")

LOOSE = {"per_test": 100_000, "per_test_run": 100_000}
SMALL = {"events": 100, "bytes": 1000}
ROOMY = {"events": 100_000, "bytes": 100_000}
WRITES = "def test_writes(tmp_path):\n    (tmp_path / 'f').write_bytes(b'x' * 5000)\n"


def _conf(**own) -> dict:
    return {**LOOSE, "new": SMALL, "today": ROOMY, "named": {}, "files_today": [], **own}


@pytest.mark.load_sensitive
@darwin
@pytest.mark.parametrize("xdist", [(), XDIST], ids=["plain", "xdist"])
def test_a_new_file_over_its_events_is_red_and_its_worst_test_named(
    tmp_path: Path, xdist: tuple[str, ...]
) -> None:
    r, rep = _session(tmp_path, CHURNY + QUIET, _conf(), *xdist)
    assert r.returncode == 1, r.stdout[-1500:]
    assert "test_planted.py::test_churny made" in r.stdout, r.stdout[-1500:]
    assert "events, over test_planted.py's ceiling 100" in r.stdout, r.stdout[-1500:]
    row = rep["files"]["test_planted.py"]
    assert row["events"] >= 100 and row["events_test"] == "test_planted.py::test_churny", row


@pytest.mark.load_sensitive
@darwin
def test_a_partial_run_is_judged_on_what_it_ran(tmp_path: Path) -> None:
    """The whole-run mean is no reading of a land's partial run (GOH.FSCEILINGRED); a file's is."""
    r, rep = _session(tmp_path, CHURNY + QUIET, _conf(), drops=("test_churny",))
    assert r.returncode == 0 and not rep["whole"], r.stdout[-1500:]
    r, rep = _session(tmp_path, CHURNY + QUIET, _conf(), drops=("test_quiet",))
    assert r.returncode == 1 and not rep["whole"], r.stdout[-1500:]


@pytest.mark.load_sensitive
@darwin
def test_bytes_are_judged_and_a_list_or_a_name_holds_a_file_to_its_own(tmp_path: Path) -> None:
    r, _ = _session(tmp_path, WRITES, _conf())
    assert r.returncode == 1 and "made 5000 bytes" in r.stdout, r.stdout[-1500:]
    r, _ = _session(tmp_path, WRITES, _conf(files_today=["test_planted.py"]))
    assert r.returncode == 0, r.stdout[-1500:]
    part = {"files_today": ["test_planted.py"], "bytes_per_test_run": 4999}
    r, _ = _session(tmp_path, WRITES, _conf(**part))
    assert r.returncode == 1 and "bytes left written by a run of 1 (5000" in r.stdout, r.stdout[
        -1500:
    ]
    r, _ = _session(tmp_path, WRITES, _conf(**part), "test_planted.py")  # as a pre-step names it
    assert r.returncode == 0 and "a partial run" in r.stdout, r.stdout[-1500:]  # GOH.FSEVQUIET
    named = {"test_planted.py": {"bytes": 5000}}
    r, _ = _session(tmp_path, WRITES, _conf(named=named))
    assert r.returncode == 0, r.stdout[-1500:]
    r, _ = _session(tmp_path, QUIET, _conf(named=named))
    assert r.returncode == 1 and "remove its name" in r.stdout, r.stdout[-1500:]


def _rows(events: int, size: int) -> dict[str, dict]:
    return {"a.py": {"events": events, "bytes": size}}


def test_an_unmeasured_window_is_no_verdict_on_events_and_bytes_are_judged_all_the_same() -> None:
    conf = _conf()
    assert faults(_rows(10**6, 0), conf, set(), whole=True, measured=False) == []
    assert len(faults(_rows(10**6, 0), conf, set(), whole=True, measured=True)) == 1
    assert len(faults(_rows(0, 10**6), conf, set(), whole=False, measured=False)) == 1


def test_a_name_is_stale_only_by_a_whole_measured_run_well_under_the_default() -> None:
    conf = _conf(named={"a.py": {"events": 900}})
    assert len(faults(_rows(50, 500), conf, set(), whole=True, measured=True)) == 1
    assert faults(_rows(50, 500), conf, set(), whole=False, measured=True) == []
    assert faults(_rows(50, 500), conf, set(), whole=True, measured=False) == []
    assert faults(_rows(51, 0), conf, set(), whole=True, measured=True) == []
    assert faults(_rows(900, 0), conf, set(), whole=True, measured=True) == []
    assert len(faults(_rows(901, 0), conf, set(), whole=True, measured=True)) == 1


def test_a_tests_events_are_its_own_dirs_never_a_nested_sessions() -> None:
    root, run = "/T/goh-pytest.slot03", "/T/goh-pytest.slot03/pytest-of-u/pytest-0"
    own = fs_events.own_dir
    assert own(f"{run}/popen-gw2/test_foo0/a/b", [root]) == "popen-gw2/test_foo0"
    assert own(f"{run}/test_foo12", [root]) == "/test_foo12"
    assert own(f"{run}/popen-gw2/test_a0/pytest-of-u/pytest-0/test_b0/x", [root]) == (
        "popen-gw2/test_a0"
    )
    nested = f"{run}/popen-gw2/nested-pytest/pytest-of-u/pytest-0/test_b0/x"
    assert own(nested, [root]) == "" and own("/elsewhere/pytest-0/test_a0", [root]) == ""
    dirs = {"g/test_a0": "t.py::a[0]", "g/test_a1": "t.py::a[1]"}
    events = by_test({"g/test_a0": 3, "g/test_a1": 4, "g/x0": 9}, dirs)
    assert events == {"t.py::a[0]": 3, "t.py::a[1]": 4}
    assert readings(events, {"t.py::a[0]": 7}, {"t.py::a[0]", "u.py::b"}) == {
        "t.py": {
            "events": 4, "events_test": "t.py::a[1]", "bytes": 7, "bytes_test": "t.py::a[0]",
            "all_events": 7, "all_bytes": 7, "tests": 1,
        },
        "u.py": {"events": 0, "bytes": 0, "all_events": 0, "all_bytes": 0, "tests": 1},
    }  # fmt: skip


def test_the_lists_name_only_files_that_stand_and_only_shrink() -> None:
    before, named = today(), CEILING["named"]
    gone = sorted(f for f in {*before, *named} if not (REPO_ROOT / f).is_file())
    assert gone == [], f"stale names, remove them: {gone}"
    assert len(before) <= TODAY and len(named) <= NAMED
    for kind in ("events", "bytes"):
        assert 0 < CEILING["new"][kind] <= CEILING["today"][kind]
        for f, own in named.items():
            base = CEILING["today" if f in before else "new"]
            assert own.get(kind, base[kind] + 1) > base[kind], (f, "its name buys it nothing")


def test_a_worker_that_went_down_with_its_readings_is_counted_lost() -> None:
    """Its tests read as nothing, which is no ground to call a name stale."""
    import _fs_bytes

    node = types.SimpleNamespace(gateway=types.SimpleNamespace(id="gw9"), workeroutput={})
    on, lost = _fs_bytes._ON[0], list(_fs_bytes.LOST)
    try:
        _fs_bytes._ON[0] = True
        _fs_bytes.pytest_testnodedown(node, None)
        assert [*lost, "gw9"] == _fs_bytes.LOST
    finally:
        _fs_bytes._ON[0], _fs_bytes.LOST[:] = on, lost


def test_only_a_whole_measured_run_settles_a_file_under_half_of_the_new_ceilings() -> None:
    files = {"a.py": {"events": 50, "bytes": 500}, "b.py": {"events": 51, "bytes": 0}}
    rep = {"whole": True, "files": files}
    assert settled(rep, SMALL, {"a.py", "b.py", "never-ran.py"}) == {"a.py"}
    assert settled({**rep, "whole": False}, SMALL, {"a.py"}) == set()
    assert settled({**rep, "unmeasured": "dropped"}, SMALL, {"a.py"}) == set()
