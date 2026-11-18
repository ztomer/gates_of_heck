"""A binary-running test is carried by its stored profile of the same binary (GOH.COVCARRY3 c).

D-0011 option 4 under the owner's terms: an instrumented land whose diff leaves the binary's bytes
alone skips a binary-running node tools/affected_tests.py does not choose, and the floor still
judges a complete profile -- the node's stored profile of THIS binary is put where the report
reads it (lib/prof_store.py). Pinned on a real instrumented binary: the carried profile counts the
line only that test reaches, the next tree can carry it on, and a changed binary or the kill
switch runs the test instead.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from test_cov_carry import PROG, RUNS_IT, _llvm_bin
from test_verdict_carry import SESSION_CONFTEST, _gate

sys.path.insert(0, str(REPO_ROOT / "lib"))
import prof_store
import reuse

pytestmark = pytest.mark.skipif(_llvm_bin() is None, reason="rustc's llvm-tools not installed")

RUNS = "test_r.py::test_runs_it"


def _build(bins: Path, src: str) -> Path:
    bins.mkdir()
    (bins / "prog.rs").write_text(src)
    subprocess.run(["rustc", "-C", "instrument-coverage", "-o", "prog", "prog.rs"],
                   cwd=bins, check=True, capture_output=True,
                   stdin=subprocess.DEVNULL)  # fmt: skip
    return bins / "prog"


def _run(d: Path, store: Path, bins: Path, prof: Path, tree: str, **env: str):
    prof.mkdir()
    return _gate(d, store, TREE=tree, CHOSEN="", GOH_COVERAGE_BIN_DIR=str(bins),
                 LLVM_PROFILE_FILE=str(prof / "goh-%p.profraw"), **env)  # fmt: skip


def _ran_line(exe: Path, prof: Path, tmp: Path) -> str:
    """The count of PROG's line 4 (`ran`, the branch only test_runs_it takes) in the report the
    run's profile directory makes, as cargo llvm-cov's merge reads it (every *.profraw)."""
    llvm = _llvm_bin()
    assert llvm is not None
    whole = tmp / f"{prof.name}.profdata"
    subprocess.run([str(llvm / "llvm-profdata"), "merge", "-sparse", "-o", str(whole),
                    *map(str, prof.glob("*.profraw"))], check=True, capture_output=True,
                   stdin=subprocess.DEVNULL)  # fmt: skip
    out = subprocess.run([str(llvm / "llvm-cov"), "export", "-format=lcov", str(exe),
                          f"-instr-profile={whole}"], check=True, capture_output=True, text=True,
                         stdin=subprocess.DEVNULL).stdout  # fmt: skip
    return dict(ln[3:].split(",")[:2] for ln in out.splitlines() if ln.startswith("DA:"))["4"]


@pytest.fixture
def suite(tmp_path: Path) -> tuple[Path, Path, Path]:
    bins = tmp_path / "bins"
    exe = _build(bins, PROG)
    d = tmp_path / "s"
    d.mkdir()
    (d / "conftest.py").write_text(SESSION_CONFTEST)
    (d / "test_r.py").write_text(RUNS_IT.format(exe=str(exe)))
    return d, bins, exe


def test_an_unchanged_binary_carries_a_binary_running_test_by_its_stored_profile(
    tmp_path: Path, suite, monkeypatch: pytest.MonkeyPatch
) -> None:
    d, bins, exe = suite
    store = tmp_path / "store"
    monkeypatch.setenv("GOH_REUSE_DIR", str(store))
    first = _run(d, store, bins, tmp_path / "p0", "T0")
    assert first.returncode == 0 and f"PASSED {RUNS}" in first.stdout, first.stdout
    # T0..T1 chooses nothing and the binary is the same bytes: the node is not run, its profile is
    second = _run(d, store, bins, tmp_path / "p1", "T1")
    assert second.returncode == 0, second.stdout + second.stderr
    assert RUNS not in second.stdout and "1 by their stored profiles" in second.stdout, (
        second.stdout
    )
    assert _ran_line(exe, tmp_path / "p1", tmp_path) != "0", "the floor lost the carried run"
    rec, _ = reuse.lookup_suite(REPO_ROOT, "T1", "TOOLS")
    assert rec["passed"][RUNS] is True and rec["measured"][RUNS] is True, rec
    # ... and T1's key now holds it, so the land after carries it on.
    k1 = prof_store.key(reuse.repo_id(REPO_ROOT), "T1", "TOOLS", prof_store.bin_id(bins))
    assert prof_store.held(k1, {RUNS, "test_r.py::test_pure"}) == {RUNS}


def test_a_changed_binary_or_the_kill_switch_runs_the_test(
    tmp_path: Path, suite, monkeypatch: pytest.MonkeyPatch
) -> None:
    d, bins, exe = suite
    store = tmp_path / "store"
    monkeypatch.setenv("GOH_REUSE_DIR", str(store))
    assert _run(d, store, bins, tmp_path / "p0", "T0").returncode == 0
    off = _run(d, store, bins, tmp_path / "p1", "T1", GOH_PROFCARRY_TESTS="off")
    assert f"PASSED {RUNS}" in off.stdout, off.stdout
    other = tmp_path / "other"
    _build(other, PROG.replace('"ran"', '"ran again"'))
    shutil.copy(other / "prog", exe)  # the same path, other bytes: another binary
    prof_store._ids.clear()
    moved = _run(d, store, bins, tmp_path / "p2", "T2")
    assert f"PASSED {RUNS}" in moved.stdout, moved.stdout
    assert "stored profiles" not in moved.stdout, moved.stdout
