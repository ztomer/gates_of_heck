"""A test measured running no binary carries through every kind of run (GOH.COVNONBIN, D-0011).

GOH.COVSUITECOST (2026-11-18, tree ec82d57c, all 6359 tests): the coverage step's instrumented
run spent 2262 of its 3156 cpu-s on the 4668 tests that never run the binary and add no coverage.
The coverage step carries such a test only when an instrumented run MEASURED it running none
(tests/_prof_record.py, GOH.COVCARRY), and that fact lived only in the record of the tree the
instrumented run judged: a plain land's gate kept it for the nodes it SKIPPED alone (a shadow,
which runs its carried nodes anyway, dropped a whole source's), and a gate that took the
pre-step's verdict (lib/pre_reuse.py `suite`, GOH.SUITEONCE) recorded the tree with no measurement
at all -- so the next instrumented land could carry nothing and ran the whole suite again.

The fact is carried now exactly as a verdict is: a node measured running no binary on tree T, that
the diff T..HEAD does not choose (tools/affected_tests.py), ran none on HEAD either. And the claim
is checked wherever the binary is instrumented: a test that leaves a profile while it is claimed
as running none turns the run red, naming it (`misplaced`).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import _cov_claims
import pytest
from conftest import REPO_ROOT, git, hermetic_env
from test_suite_once import A, B, land  # noqa: F401  -- the SUITEONCE fixture
from test_verdict_carry import SESSION_CONFTEST, _gate

sys.path.insert(0, str(REPO_ROOT / "lib"))
import gate_identity
import pre_reuse
import reuse


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl"))
    monkeypatch.delenv("GOH_REUSE_TTL_S", raising=False)
    monkeypatch.delenv("GOH_REUSE_NOW", raising=False)
    return tmp_path / "store"


# test_t.py::test_bin runs the instrumented binary when RUN_BIN is set: it leaves a raw profile in
# its own profile directory, as an instrumented `goh` would.
TEST_T = """import os
from pathlib import Path

def test_ok():
    pass

def test_bin():
    if os.environ.get("RUN_BIN"):
        Path(os.environ["LLVM_PROFILE_FILE"].replace("%4m", "1")).write_bytes(b"not a profile")
"""
TEST_S = "def test_pass():\n    pass\n"
T0 = {"test_s.py::test_pass": False, "test_t.py::test_ok": False, "test_t.py::test_bin": False}


@pytest.fixture
def session(tmp_path: Path, store: Path) -> Path:
    """T0's record: an instrumented full run measured all three running no binary."""
    d = tmp_path / "s"
    d.mkdir()
    (d / "conftest.py").write_text(SESSION_CONFTEST)
    (d / "test_s.py").write_text(TEST_S)
    (d / "test_t.py").write_text(TEST_T)
    reuse.record_suite(REPO_ROOT, "T0", "TOOLS", T0, set(), True, "idle", instrumented=True,
                       measured=dict(T0))  # fmt: skip
    return d


def _inst(tmp_path: Path) -> dict[str, str]:
    (tmp_path / "prof").mkdir(exist_ok=True)
    return {"GOH_COVERAGE_BIN_DIR": str(tmp_path / "bins"),
            "LLVM_PROFILE_FILE": str(tmp_path / "prof" / "goh-%p.profraw")}  # fmt: skip


def _full(d: Path, store: Path, **env: str):
    """The idle lander's fresh full run: no reuse, the store's record alone."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA"],
        cwd=d, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
        env=hermetic_env(drop_git=True, GOH_REUSE_DIR=str(store), GOH_LOOPCTL=str(d / "no-loopctl"),
                         GOH_LAND_VERDICTS_RECORD=os.devnull, **env),
    )  # fmt: skip


def _measured(tree: str) -> dict[str, bool]:
    rec, _ = reuse.lookup_suite(REPO_ROOT, tree, "TOOLS")
    assert rec is not None
    return rec["measured"]


def test_a_plain_gates_shadow_keeps_the_measurement_of_the_nodes_it_ran_anyway(
    session: Path, store: Path
) -> None:
    """The audit's shadow runs a source's carried nodes plain: they ran, and still were measured
    running no binary on T0, which the diff does not reach. T1 keeps that, so an instrumented run
    on T2 carries them instead of running the whole suite."""
    plain = _gate(session, store, CHOSEN="test_s.py", TREE="T1", EVERY="1")
    assert "RUN anyway: the audit's shadow" in plain.stdout and plain.returncode == 0, plain.stdout
    assert _measured("T1") == {"test_t.py::test_ok": False, "test_t.py::test_bin": False}


def test_an_instrumented_land_runs_only_what_the_diff_chooses_after_a_plain_land(
    session: Path, store: Path, tmp_path: Path
) -> None:
    """T0 (instrumented) -> T1 (a plain land) -> T2 (an instrumented land): T2's coverage step runs
    the test its diff chooses and carries the two measured on T0, through T1's record."""
    plain = _gate(session, store, CHOSEN="test_s.py", TREE="T1", EVERY="1")
    assert plain.returncode == 0, plain.stdout
    conf = session / "conftest.py"
    conf.write_text(conf.read_text().replace('[("c0" * 20, "T0")]', '[("c1" * 20, "T1")]'))
    inst = _gate(session, store, CHOSEN="test_s.py", TREE="T2", **_inst(tmp_path))
    assert inst.returncode == 0, inst.stdout + inst.stderr
    assert "PASSED test_s.py::test_pass" in inst.stdout, inst.stdout
    assert "test_t.py::" not in inst.stdout, inst.stdout  # carried, not run
    assert f"2 deselected, carried from {'c1' * 4}" in inst.stdout, inst.stdout


def test_a_reused_pre_step_verdict_records_the_measurement_it_carries(land, monkeypatch) -> None:  # noqa: F811
    """GOH.SUITEONCE writes the tip's record from main's: the nodes it carries keep main's
    measurement, the one the pre-step judged has none (it ran plain)."""
    for key in ("GOH_COVERAGE_BIN_DIR", "LLVM_PROFILE_FILE"):  # an instrumented run refuses reuse
        monkeypatch.delenv(key, raising=False)
    repo, rec, base, _ = land
    tools = gate_identity.suite_tools()
    main_tree = git(repo, "rev-parse", f"{base}^{{tree}}").strip()
    reuse.record_suite(repo, main_tree, tools, {A: False, B: False}, set(), True, "idle",
                       instrumented=True, measured={A: False, B: False})  # fmt: skip
    ok, why, _ = pre_reuse.suite(repo, rec, base)
    assert ok, why
    got, _ = reuse.lookup_suite(repo, git(repo, "rev-parse", "HEAD^{tree}").strip(), tools)
    assert got is not None and got["measured"] == {A: False}, got


# -- the gate: a test that runs the binary never sits in the carried set ---------------------------


def test_misplaced_is_a_claimed_node_that_ran_the_binary() -> None:
    assert _cov_claims.misplaced({"a": False, "b": False, "c": True}, {"b", "c", "d"}) == ["b"]
    assert _cov_claims.misplaced({}, {"b"}) == []


def test_a_shadowed_claim_that_runs_the_binary_turns_the_coverage_run_red(
    session: Path, store: Path, tmp_path: Path
) -> None:
    """The shadow runs T0's carried nodes under the instrumented binary: test_bin leaves a profile
    while T0's record claims it ran none -- red, named, and T1 records what it saw."""
    r = _gate(session, store, CHOSEN="test_s.py", TREE="T1", EVERY="1", RUN_BIN="1",
              **_inst(tmp_path))  # fmt: skip
    assert r.returncode == 1, r.stdout + r.stderr
    assert "ran the instrumented binary but was carried as running none" in r.stdout, r.stdout
    assert "test_t.py::test_bin" in r.stdout, r.stdout
    assert _measured("T1")["test_t.py::test_bin"] is True


def test_a_fresh_full_run_checks_its_own_trees_claim(session: Path, store: Path, tmp_path) -> None:
    """The idle full run on main's tip (no reuse, the store's record alone) is the shadow of every
    claim the lands left on that tree: one that ran the binary turns it red."""
    r = _full(session, store, TREE="T0", RUN_BIN="1", **_inst(tmp_path))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "test_t.py::test_bin" in r.stdout and "carried as running none" in r.stdout, r.stdout
    ok = _full(session, store, TREE="T0", **_inst(tmp_path))
    assert ok.returncode == 0, ok.stdout  # T0 now records test_bin as running the binary
