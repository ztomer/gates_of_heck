"""A ship's fresh instrumented run takes its tree's own non-binary passes (GOH.SHIPBREACH0137).

The ship of a tip whose land reused the Rust coverage measurement re-ran all ~6400 tests under the
instrumented binary (v0.139.0 1085 s, v0.139.1 658 s, v0.139.3 1096 s; 3311-4602 cpu-s each),
though about 73% of the test time is tests that run no binary and add no coverage. Now the run
(GOH_LAND_VERDICTS_REUSE=@tree, tests/_same_tree.py) skips a node only when a run JUDGED it on this
very tree, it ran no binary, and an instrumented run measured it running none; every binary-running,
carried or unmeasured test runs, instrumented, and the record is the full instrumented one.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import _same_tree
import pytest
from conftest import REPO_ROOT, commit_all, hermetic_env, write
from test_verdict_carry import SESSION_CONFTEST, _gate

sys.path.insert(0, str(REPO_ROOT / "lib"))
import reuse
import ship_full_run

TEST_S = "def test_pass():\n    pass\n"
TEST_T = """import os
from pathlib import Path

def test_ok():
    pass

def test_bin():
    Path(os.environ["LLVM_PROFILE_FILE"].replace("%4m", "1")).write_bytes(b"not a profile")
"""
S, OK, BIN = "test_s.py::test_pass", "test_t.py::test_ok", "test_t.py::test_bin"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl"))
    for k in ("GOH_REUSE_TTL_S", "GOH_REUSE_NOW"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path / "store"


@pytest.fixture
def session(tmp_path: Path, store: Path) -> Path:
    d = tmp_path / "s"
    d.mkdir()
    (d / "conftest.py").write_text(SESSION_CONFTEST)
    (d / "test_s.py").write_text(TEST_S)
    (d / "test_t.py").write_text(TEST_T)
    return d


def _inst(tmp_path: Path) -> dict[str, str]:
    (tmp_path / "prof").mkdir(exist_ok=True)
    return {"GOH_COVERAGE_BIN_DIR": str(tmp_path / "bins"),
            "LLVM_PROFILE_FILE": str(tmp_path / "prof" / "goh-%p.profraw")}  # fmt: skip


def _fresh(d: Path, store: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """The ship's run (lib/ship_full_run.py): the store's record alone, and @tree."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA"],
        cwd=d, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
        env=hermetic_env(drop_git=True, GOH_REUSE_DIR=str(store), GOH_LOOPCTL=str(d / "no-loopctl"),
                         GOH_LAND_VERDICTS_RECORD=os.devnull, TREE="T1",
                         GOH_LAND_VERDICTS_REUSE=ship_full_run.REUSE, **_inst(tmp_path)),
    )  # fmt: skip


def _t1() -> dict:
    rec, why = reuse.lookup_suite(REPO_ROOT, "T1", "TOOLS")
    assert rec is not None, why
    return rec


def test_the_fresh_run_skips_only_this_trees_judged_passes_measured_running_no_binary(
    session: Path, store: Path, tmp_path: Path
) -> None:
    """T1's plain land judged S and BIN on T1 and carried OK from another tree; all three are
    measured running no binary except BIN. The ship's run skips S alone: BIN runs the binary, OK
    was never judged on T1. Its record is the full instrumented run the release kit needs."""
    reuse.record_suite(REPO_ROOT, "T1", "TOOLS", {S: False, OK: False, BIN: True}, set(), False,
                       "land gate", 1, measured={S: False, OK: False},
                       judged={S, BIN})  # fmt: skip
    r = _fresh(session, store, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 judged on this tree, measured running no binary, are reused" in r.stdout, r.stdout
    assert f"PASSED {OK}" in r.stdout and f"PASSED {BIN}" in r.stdout, r.stdout
    assert S not in r.stdout.split("short test summary")[-1], r.stdout  # deselected, not run
    rec = _t1()
    assert rec["full"] and rec["instrumented"] and rec["carried"] == 0, rec
    assert rec["by"] == "full run" and set(rec["judged"]) == {S, OK, BIN}, rec


def test_a_tree_with_no_judged_passes_runs_its_whole_suite(
    session: Path, store: Path, tmp_path: Path
) -> None:
    """A record from before `judged` (or one wholly carried): nothing is skipped."""
    reuse.record_suite(REPO_ROOT, "T1", "TOOLS", {S: False, OK: False, BIN: True}, set(), False,
                       "land gate", 3, measured={S: False, OK: False})  # fmt: skip
    r = _fresh(session, store, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no pass judged on this tree measured running no binary" in r.stdout, r.stdout
    assert all(f"PASSED {n}" in r.stdout for n in (S, OK, BIN)), r.stdout
    assert _t1()["full"] and _t1()["instrumented"]


def test_a_plain_gate_records_what_it_judged_and_not_what_it_carried(
    session: Path, store: Path
) -> None:
    """T0's three passes carry to T1 across a diff choosing test_s.py: T1's record names S (run
    here) as judged, and neither carried node."""
    reuse.record_suite(REPO_ROOT, "T0", "TOOLS", {S: False, OK: False, BIN: False}, set(), True,
                       "gate")  # fmt: skip
    r = _gate(session, store, CHOSEN="test_s.py", TREE="T1")
    assert r.returncode == 0 and "2 deselected, carried from" in r.stdout, r.stdout
    assert _t1()["judged"] == [S], _t1()


def test_only_a_judged_measured_non_binary_pass_is_taken() -> None:
    rec = {"passed": {S: False, OK: False, BIN: True}, "measured": {S: False, OK: False},
           "judged": [S, BIN]}  # fmt: skip
    assert _same_tree.nodes(rec) == {S}
    assert _same_tree.nodes({**rec, "measured": {}}) == set()  # unmeasured: it runs
    assert _same_tree.nodes(None) == set()


def test_judged_is_merged_by_the_store(store: Path) -> None:
    repo = REPO_ROOT
    reuse.record_suite(repo, "T", "TOOLS", {S: False}, set(), False, "pre-land", judged={S})
    reuse.record_suite(repo, "T", "TOOLS", {OK: False}, set(), False, "gate", judged={OK})
    assert reuse.lookup_suite(repo, "T", "TOOLS")[0]["judged"] == [S, OK]
    reuse.record_suite(repo, "T", "TOOLS", {}, {S}, False, "gate")
    assert reuse.lookup_suite(repo, "T", "TOOLS")[0]["judged"] == [OK]


def test_the_ships_run_names_at_tree(repo: Path, tmp_path: Path, store: Path) -> None:
    """lib/ship_full_run.py hands its run GOH_LAND_VERDICTS_REUSE=@tree."""
    write(repo, ".gatesrc", "GOH_SHIP_FULL_SUITE=on\n")
    commit_all(repo, "chore: a repo whose ship stands on a full run")
    seen = tmp_path / "seen"
    dump = "import os, sys; open(sys.argv[1], 'w').write(os.environ['GOH_LAND_VERDICTS_REUSE'])"
    argv = [sys.executable, "-c", dump, str(seen)]
    ship_full_run.run(repo, tmp_path / "wt", argv)
    assert seen.read_text() == "@tree"
