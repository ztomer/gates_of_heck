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
from conftest import REPO_ROOT, hermetic_env
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


def _land(d: Path, store: Path, bins: Path, prof: Path, tree: str) -> subprocess.CompletedProcess:
    """A land's instrumented run as tools/land.sh makes it: xdist workers, and the store named by
    GOH_LAND_VERDICTS_STORE (tests/suite_home.py `_land_store`), never GOH_REUSE_DIR."""
    prof.mkdir()
    env = hermetic_env(drop_git=True, GOH_LOOPCTL=str(d / "no-loopctl"),
                       GOH_LAND_VERDICTS_REUSE=str(d / "no-pre-record.json"),
                       GOH_LAND_VERDICTS_STORE=str(store), TREE=tree, CHOSEN="",
                       GOH_COVERAGE_BIN_DIR=str(bins),
                       LLVM_PROFILE_FILE=str(prof / "goh-%p.profraw"))  # fmt: skip
    env.pop("GOH_REUSE_DIR", None)
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA",
                           "-n", "2"], cwd=d, capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def test_a_land_under_xdist_keeps_each_profile_keyed_by_the_binary_and_the_next_carries_it(
    tmp_path: Path, suite, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GOH.PROFSTOREEMPTY: the store's name left the environment as the controller loaded
    (`_land_verdicts._take`), so no xdist worker -- where every test runs -- had a store to keep
    a profile in: after a full instrumented land the real store held no profstore/ at all."""
    d, bins, _ = suite
    store = tmp_path / "store"
    monkeypatch.setenv("GOH_REUSE_DIR", str(store))
    first = _land(d, store, bins, tmp_path / "p0", "T0")
    assert first.returncode == 0 and f"PASSED {RUNS}" in first.stdout, first.stdout
    assert "profile store: 1 of 1 binary-running tests' profiles kept under tree T0" in first.stdout
    k0 = prof_store.key(reuse.repo_id(REPO_ROOT), "T0", "TOOLS", prof_store.bin_id(bins))
    assert prof_store.held(k0, {RUNS, "test_r.py::test_pure"}) == {RUNS}, sorted(
        p.relative_to(store) for p in store.rglob("*")
    )
    second = _land(d, store, bins, tmp_path / "p1", "T1")
    assert second.returncode == 0, second.stdout + second.stderr
    assert "1 by their stored profiles" in second.stdout, second.stdout


def test_an_enabled_carry_whose_run_left_its_store_empty_is_red(tmp_path: Path, suite) -> None:
    """The class: a reuse path that is on and stays empty after a run that should fill it is a
    fault the run reports, never a silent full re-run on every land (GOH.PROFSTOREEMPTY)."""
    d, bins, _ = suite
    with (d / "conftest.py").open("a") as f:  # every worker loads it: no test's profile is kept
        f.write("import _prof_record\n_prof_record._keep = lambda node, merged: None\n")
    r = _land(d, tmp_path / "store", bins, tmp_path / "p0", "T0")
    assert f"PASSED {RUNS}" in r.stdout and r.returncode == 1, r.stdout
    said = "0 of 1 binary-running tests' profiles kept under tree T0: the enabled carry left its"
    assert f"{said} store EMPTY" in r.stdout, r.stdout


MARKED = """import _land_verdicts
def test_asks_for_it(request):
    _land_verdicts.mark_binary(request.node)  # as asking for `native_goh` marks it: nothing runs
"""


def test_a_same_tree_rerun_of_tests_marked_but_never_profiled_is_not_an_empty_store(
    tmp_path: Path, suite, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GOH.CIWALL3: a test that asks for the binary is MARKED as running it (tests/_goh_request.py)
    though it may never run it, so it leaves no profile to keep. Of the 1833 binary passes of the
    2026-11-19 full land, 127 were such tests: never kept, so the same tree's next land carried
    every profiled one and re-ran just those 127, and `kept` read the store as EMPTY -- red on
    every re-land after a flake, the carry working. A profile the run made is what it must keep."""
    d, bins, _ = suite
    (d / "test_m.py").write_text(MARKED)
    store = tmp_path / "store"
    monkeypatch.setenv("GOH_REUSE_DIR", str(store))
    first = _land(d, store, bins, tmp_path / "p0", "T0")
    assert first.returncode == 0, first.stdout
    assert "profile store: 1 of 1 binary-running tests' profiles kept" in first.stdout, first.stdout
    again = _land(d, store, bins, tmp_path / "p1", "T0")  # the same tree, landed again
    assert "PASSED test_m.py::test_asks_for_it" in again.stdout, again.stdout
    assert f"PASSED {RUNS}" not in again.stdout, again.stdout  # carried by its stored profile
    assert again.returncode == 0 and "EMPTY" not in again.stdout, again.stdout


def test_a_store_that_fills_and_never_matches_is_red_after_misses_lands(
    tmp_path: Path, suite, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GOH.PROFKEYMISS: a store that fills under keys no later land matches looks like a working
    one -- the instrumented goh built other bytes from the same inputs (4 sha256s in 5 builds), so
    every land stored ~1700 profiles and carried none, and only an EMPTY store was red. Here each
    land after T0 builds other bytes while its diff leaves every build input alone: the MISSES-th
    such land is red, naming both binaries; a land that matches again is green."""
    d, bins, exe = suite
    store = tmp_path / "store"
    monkeypatch.setenv("GOH_REUSE_DIR", str(store))
    same = tmp_path / "t0-bytes"
    shutil.copy(exe, same)
    assert _run(d, store, bins, tmp_path / "p0", "T0").returncode == 0
    r = None
    for i in range(1, prof_store.MISSES + 1):
        other = tmp_path / f"drift{i}"
        _build(other, PROG.replace("no test takes this branch", f"drift {i}"))
        shutil.copy(other / "prog", exe)  # the same inputs by the diff's account, other bytes
        r = _run(d, store, bins, tmp_path / f"p{i}", f"T{i}")
        assert f"PASSED {RUNS}" in r.stdout, r.stdout
        assert r.returncode == (1 if i == prof_store.MISSES else 0), (i, r.stdout)
    assert r is not None
    said = f"the last {prof_store.MISSES} instrumented lands that could carry matched none"
    assert said in r.stdout and "not reproducible" in r.stdout, r.stdout
    shutil.copy(same, exe)  # T0's own bytes: its stored profiles match again
    back = _run(d, store, bins, tmp_path / "p9", "T9")
    assert back.returncode == 0 and "1 by their stored profiles" in back.stdout, back.stdout


def test_a_land_is_owed_a_match_only_of_a_kept_source_still_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ledger's arithmetic: a source with no kept profiles, or whose key prune() removed, owes
    nothing; MISSES owed rows that matched none are due, one match among them clears it."""
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "store"))
    (reuse.root() / "profstore" / "k0").mkdir(parents=True)
    rows = [{"tree": "T0", "key": "k0", "kept": 5}, {"tree": "TE", "key": "kE", "kept": 0},
            {"tree": "TP", "key": "pruned", "kept": 5}]  # fmt: skip
    assert prof_store.owed(rows, ["T0"]) and not prof_store.owed(rows, ["TE", "TP", "TX"])
    miss = {"owed": True, "matched": 0}
    due = [miss] * prof_store.MISSES
    assert prof_store.misses(due) == due and not prof_store.misses(due[1:])
    assert not prof_store.misses([*due[:-1], {"owed": True, "matched": 3}])
    assert prof_store.misses([*due[:-1], {"owed": False}, miss]) == due  # an unowed row is no break
    for i in range(prof_store.ROWS + 5):
        got = prof_store.note({"tree": f"T{i}"})
    assert len(got) == prof_store.ROWS and got[-1]["tree"] == f"T{prof_store.ROWS + 4}"
    assert prof_store.ledger() == got
