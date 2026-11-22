"""A test verdict is carried across trees: the land gate re-runs only what the diff chooses.

GOH.VERDICTCARRY. A land's gate reused the pre-step's passes only on the SAME tree, and main moved
under 5 of 8 green pre-steps (2026-11-16), so most lands ran the whole suite again (551-731 s at
load). Now every recording run merges its passes into the one store's `carry` path (repo, tree,
tools), and the gate deselects a node that passed on another tree when tools/affected_tests.py,
asked about the diff, does not choose it (tests/_land_verdicts.py). Pinned here: what may be
carried, that a file the diff chooses IS re-run and goes red, that the audit's shadow catches a
planted under-keyed carry and turns the path off, that only a run that carried nothing is a FULL
verdict, and that goh's release kit refuses a tip without one.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import _land_verdicts as lv
import pytest
from conftest import REPO_ROOT, commit_all, git, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import gate_identity
import reuse
import reuse_audit
import ship_suite

# -- what may be carried -----------------------------------------------------------------------


PASSED = {
    "tests/test_a.py::test_one": False,
    "tests/test_a.py::test_two[x]": False,
    "tests/test_b.py::test_pure": False,
    "tests/test_b.py::test_reads[p]": False,
    "tests/test_b.py::TestK::test_bin": True,
}


def test_a_node_the_diff_does_not_choose_is_carried_and_a_chosen_one_is_not() -> None:
    chosen = {"tests/test_a.py", "tests/test_b.py::test_reads"}
    assert lv.carried(PASSED, chosen, coverage=False, binary=False) == {
        "tests/test_b.py::test_pure",
        "tests/test_b.py::TestK::test_bin",
    }


@pytest.mark.parametrize(("coverage", "binary"), [(True, False), (False, True)])
def test_a_node_that_ran_the_binary_is_not_carried_under_coverage_or_a_build_change(
    coverage: bool, binary: bool
) -> None:
    assert "tests/test_b.py::TestK::test_bin" not in lv.carried(PASSED, set(), coverage, binary)


def test_a_build_input_change_is_one_for_the_binary() -> None:
    assert lv._builds(["crates/goh/src/main.rs"]) and lv._builds(["Cargo.lock"])
    assert not lv._builds(["lib/reuse.py", "crates_notes.md"])


@pytest.mark.parametrize("path", ["tests/conftest.py", "pyproject.toml"])
def test_nothing_is_carried_across_a_change_to_what_pytests_start_reads_whole(path: str) -> None:
    chosen, why = lv.choose(REPO_ROOT, [path, "lib/reuse.py"])
    assert chosen is None and path in why


# -- the store: merged records, the full verdict ---------------------------------------------------


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl"))
    monkeypatch.delenv("GOH_REUSE_TTL_S", raising=False)
    monkeypatch.delenv("GOH_REUSE_NOW", raising=False)
    return tmp_path / "store"


def test_a_tree_record_merges_its_runs_and_a_failure_ends_its_full_verdict(store, repo) -> None:
    reuse.record_suite(repo, "T", "TOOLS", {"t.py::a": False}, set(), True, "gate")
    reuse.record_suite(repo, "T", "TOOLS", {"t.py::b": True}, set(), False, "pre-land")
    rec, _ = reuse.lookup_suite(repo, "T", "TOOLS")
    assert rec["passed"] == {"t.py::a": False, "t.py::b": True} and rec["full"]
    reuse.record_suite(repo, "T", "TOOLS", {}, {"t.py::a"}, False, "gate")
    rec, _ = reuse.lookup_suite(repo, "T", "TOOLS")
    assert rec["passed"] == {"t.py::b": True} and not rec["full"]
    assert reuse.lookup_suite(repo, "T", "OTHER")[0] is None  # other tools: a miss


def test_only_a_run_that_carried_nothing_is_a_full_suite_verdict(store, repo) -> None:
    sha = git(repo, "rev-parse", "HEAD").strip()
    tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
    tools = gate_identity.suite_tools()
    reuse.record_suite(repo, tree, tools, {"t.py::a": False}, set(), False, "gate", carried=7)
    rec, why = ship_suite.full_suite(repo, sha)
    assert rec is None and "7 tests carried from another tree" in why, why
    reuse.record_suite(
        repo, tree, tools, {"t.py::a": False}, set(), True, "gate", instrumented=True
    )  # a ship's full run drove the instrumented binary
    assert ship_suite.full_suite(repo, sha)[0] is not None


# -- a gate's suite, end to end (a fixture session with the plugin's seams) -------------------


SESSION_CONFTEST = f"""
import dataclasses, os, sys
sys.path.insert(0, {str(REPO_ROOT / "tests")!r})
sys.path.insert(0, {str(REPO_ROOT / "lib")!r})
import _land_verdicts as lv
import reuse
lv.identity = lambda root=None: "ID"
lv.here = lambda root=None: (os.environ.get("TREE", "T1"), "TOOLS")
lv.trees = lambda root, pre: [("c0" * 20, "T0")]
lv.diff = lambda root, sha: ["lib/x.py"]
lv.choose = lambda root, changed, *a, **k: (set(os.environ.get("CHOSEN", "").split()), "")
if os.environ.get("EVERY"):
    reuse.PATHS["carry"] = dataclasses.replace(reuse.PATHS["carry"], every=int(os.environ["EVERY"]))

def pytest_configure(config):
    config.pluginmanager.register(lv, "goh-land-verdicts")
"""

# T0 passed all of these; on T1 the rebase broke test_s.py::test_broken (a file the diff chooses)
# and test_t.py::test_planted (a file it does NOT choose: the under-keyed carry).
TEST_S = """
def test_pass():
    pass

def test_broken():
    assert False, "the rebase broke this"
"""
TEST_T = """
def test_ok():
    pass

def test_planted():
    assert False, "an input the carry's key lacks"
"""
T0 = {
    "test_s.py::test_pass": False,
    "test_s.py::test_broken": False,
    "test_t.py::test_ok": False,
    "test_t.py::test_planted": False,
}


def _gate(d: Path, store: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA", *args],
        cwd=d, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
        env=hermetic_env(drop_git=True, GOH_REUSE_DIR=str(store),
                         GOH_LOOPCTL=str(d / "no-loopctl"),
                         GOH_LAND_VERDICTS_REUSE=str(d / "no-pre-record.json"), **env),
    )  # fmt: skip


@pytest.fixture
def session(tmp_path: Path, store: Path) -> Path:
    d = tmp_path / "s"
    d.mkdir()
    (d / "conftest.py").write_text(SESSION_CONFTEST)
    (d / "test_s.py").write_text(TEST_S)
    (d / "test_t.py").write_text(TEST_T)
    reuse.record_suite(REPO_ROOT, "T0", "TOOLS", T0, set(), True, "gate")
    return d


@pytest.mark.parametrize("workers", [[], ["-n", "2"]], ids=["serial", "xdist"])
def test_a_rebased_gate_reruns_only_what_the_diff_chooses_and_a_chosen_file_goes_red(
    session: Path, store: Path, workers: list[str]
) -> None:
    r = _gate(session, store, *workers, CHOSEN="test_s.py")
    out = r.stdout
    assert f"2 deselected, carried from {'c0' * 4}" in out, out
    assert "FAILED test_s.py::test_broken" in out and r.returncode == 1, out
    assert "PASSED test_s.py::test_pass" in out, out
    assert "test_t.py::test_ok" not in out and "test_t.py::test_planted" not in out, out
    rec, _ = reuse.lookup_suite(REPO_ROOT, "T1", "TOOLS")
    assert rec["carried"] == 2 and not rec["full"], rec  # carried: no full verdict on T1
    assert "test_t.py::test_ok" in rec["passed"] and "test_s.py::test_broken" not in rec["passed"]


@pytest.mark.parametrize("workers", [[], ["-n", "2"]], ids=["serial", "xdist"])
def test_the_audits_shadow_runs_a_planted_under_keyed_carry_red_and_turns_carry_off(
    session: Path, store: Path, workers: list[str]
) -> None:
    shadow = _gate(session, store, *workers, CHOSEN="test_s.py", EVERY="1")
    out = shadow.stdout
    assert "RUN anyway: the audit's shadow" in out, out
    assert "FAILED test_t.py::test_planted" in out and shadow.returncode == 1, out
    state = reuse_audit.state("carry")
    assert state["state"] == "off" and state["cause"] == "disagree", state
    assert state["evidence"]["again"] == {"reused": "green", "fresh": "red"}, state
    after = _gate(session, store, *workers, CHOSEN="test_s.py")
    assert "the audit turned `carry` off" in after.stdout, after.stdout
    assert "PASSED test_t.py::test_ok" in after.stdout, after.stdout


def test_a_whole_run_that_carried_nothing_records_a_full_verdict(session: Path, store) -> None:
    (session / "test_s.py").write_text("def test_pass():\n    pass\n")
    (session / "test_t.py").write_text("def test_ok():\n    pass\n")
    r = _gate(session, store, CHOSEN="test_s.py test_t.py")
    assert r.returncode == 0, r.stdout
    rec, _ = reuse.lookup_suite(REPO_ROOT, "T1", "TOOLS")
    assert rec["full"] and rec["carried"] == 0, rec
    narrowed = _gate(session, store, "-k", "pass", CHOSEN="test_s.py test_t.py", TREE="T2")
    assert narrowed.returncode == 0, narrowed.stdout
    rec, _ = reuse.lookup_suite(REPO_ROOT, "T2", "TOOLS")
    assert rec["passed"] == {"test_s.py::test_pass": False} and not rec["full"], rec


# -- goh's release kit refuses a tip no full run judged ------------------------------------------


KIT = REPO_ROOT / "tools" / "release_goh.sh"


def test_the_release_kit_refuses_a_tip_without_a_full_suite_verdict(repo: Path, store) -> None:
    (repo / ".gatesrc").write_text("GOH_SHIP_FULL_SUITE=on\n")
    (repo / "CHANGELOG.md").write_text("# CHANGELOG\n\n## Unreleased\n\n* **One.** a\n")
    commit_all(repo, "feat: x")
    r = subprocess.run(
        ["bash", str(KIT), "0.2.0"], cwd=repo, capture_output=True, text=True, timeout=patience(60),
        stdin=subprocess.DEVNULL, check=False,
        env=hermetic_env(drop_git=True, GOH_REUSE_DIR=str(store)),
    )  # fmt: skip
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no full suite verdict" in r.stderr and "nothing moved" in r.stderr, r.stderr
    assert not git(repo, "branch", "--list", "release-*").strip()


# -- the suite's own home keeps a land's keys and the caller's store --------------------------


def test_the_suite_home_keeps_the_land_keys_and_resolves_the_callers_store(tmp_path) -> None:
    """tests/suite_home.py drops every caller GOH_* it does not name (GOH.LIVEROOTLEAK): the land
    keys were among them, so no land's suite ever recorded or reused a verdict."""
    probe = (
        "import os, sys; sys.path.insert(0, sys.argv[1]); import suite_home; "
        "e = os.environ; print(e.get('GOH_LAND_VERDICTS_REUSE'), e.get('GOH_LAND_VERDICTS_STORE'))"
    )
    env = hermetic_env(GOH_LAND_VERDICTS_REUSE="/x/rec.json", XDG_STATE_HOME=str(tmp_path))
    env.pop("GATES_OF_HECK_SUITE_REAL_HOME", None)
    env.pop("GOH_REUSE_DIR", None)  # each test's own (tests/host_locks.py): the XDG one is asked
    r = subprocess.run([sys.executable, "-c", probe, str(REPO_ROOT / "tests")], env=env,
                       capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
                       timeout=patience(60))  # fmt: skip
    assert r.stdout.split() == ["/x/rec.json", str(tmp_path / "goh" / "reuse")], r.stdout + r.stderr
