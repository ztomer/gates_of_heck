"""A plain run measures which tests ran the binary, so coverage carries the rest (GOH.PLAINBINMARK).

GOH.LANDGATE15 (docs/reuse.md): every instrumented land reran the WHOLE suite (`carried 0`, 429-781
s), because under coverage a same-tree pass is skipped only when a run MEASURED it running no
binary, and the pre-step that passed it on this very tree ran plain and measured nothing. Now a
plain `goh` leaves a mark where LLVM_PROFILE_FILE points, when that directory asks for one
(`.goh-binmark`, crates/goh/src/main.rs `leave_run_mark`); tests/_prof_record.py gives each test of
a plain recording run its own such directory, calibrated once per worker (the resolved binary must
leave the mark, else the worker measures nothing), and records `measured` as the instrumented run
does. The coverage run's same-tree path then skips the nodes measured running none, and its audit
sample (one use of `test` in ten) runs some anyway, under coverage, where `_cov_claims` turns a
claim the profile refutes red.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "lib"))
import reuse

CONFTEST = f"""
import dataclasses, os, sys
from pathlib import Path
import pytest
sys.path.insert(0, {str(REPO_ROOT / "tests")!r})
sys.path.insert(0, {str(REPO_ROOT / "lib")!r})
import _land_verdicts as lv
import _prof_record
import reuse
lv.identity = lambda root=None: os.environ.get("IDENT", "ID")
lv.here = lambda root=None: (os.environ.get("TREE", "T1"), os.environ.get("TOOLS", "TOOLS"))
lv.trees = lambda root, pre: [("c1" * 20, os.environ.get("SRC", "T1"))]
lv.diff = lambda root, sha: ["lib/x.py"]
lv.choose = lambda root, changed, *a, **k: (set(os.environ.get("CHOSEN", "").split()), "")
for name in ("test", "carry"):
    every = os.environ.get("EVERY_" + name.upper(), "1000")
    reuse.PATHS[name] = dataclasses.replace(reuse.PATHS[name], every=int(every))

def pytest_configure(config):
    config.pluginmanager.register(lv, "goh-land-verdicts")

@pytest.fixture(autouse=True, scope="session")
def _calibrated():  # what conftest's native_goh does with the binary it resolved
    if os.environ.get("CALIBRATE"):
        _prof_record.calibrate(Path(os.environ["CALIBRATE"]))
"""

# test_bin runs the REAL plain goh (the one under test) in a plain run, and leaves a raw profile
# under coverage, as the instrumented goh would; RUN_BIN unset, it runs nothing in a plain run.
TEST_T = """import os, subprocess
from pathlib import Path

def test_ok():
    pass

def test_bin():
    if os.environ.get("GOH_COVERAGE_BIN_DIR"):
        if os.environ.get("RUN_BIN_COV", "1"):
            Path(os.environ["LLVM_PROFILE_FILE"].replace("%4m", "1")).write_bytes(b"a profile")
    elif os.environ.get("RUN_BIN", "1"):
        subprocess.run([os.environ["REAL_GOH"], "--version"], check=True, capture_output=True,
                       stdin=subprocess.DEVNULL)
"""


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl"))
    monkeypatch.delenv("GOH_REUSE_TTL_S", raising=False)
    monkeypatch.delenv("GOH_REUSE_NOW", raising=False)
    return tmp_path / "store"


@pytest.fixture
def session(tmp_path: Path, store: Path) -> Path:
    d = tmp_path / "s"
    d.mkdir()
    (d / "conftest.py").write_text(CONFTEST)
    (d / "test_t.py").write_text(TEST_T)
    return d


def _run(d: Path, store: Path, goh: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    """A child session; plain unless `env` names the coverage pair (empty, not absent: under the
    coverage gate conftest's Popen hook would hand a child without it the outer test's profile)."""
    env.setdefault("CALIBRATE", str(goh))
    for k in ("LLVM_PROFILE_FILE", "GOH_COVERAGE_BIN_DIR", "GOH_TEST_BIN"):
        env.setdefault(k, "")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA", *args],
        cwd=d, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
        env=hermetic_env(drop_git=True, GOH_REUSE_DIR=str(store), REAL_GOH=str(goh),
                         GOH_LOOPCTL=str(d / "no-loopctl"), **env),
    )  # fmt: skip


def _plain(d: Path, store: Path, goh: Path, *args: str, **env: str):
    """The pre-step: a plain run recording what it passed into its file and the store."""
    r = _run(d, store, goh, *args, GOH_LAND_VERDICTS_RECORD=str(d.parent / "pre.json"), **env)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def _cov(d: Path, store: Path, goh: Path, tmp_path: Path, **env: str):
    """The land gate's coverage run, reusing the pre-step's record."""
    (tmp_path / "prof").mkdir(exist_ok=True)
    return _run(d, store, goh, GOH_LAND_VERDICTS_REUSE=str(d.parent / "pre.json"),
                GOH_COVERAGE_BIN_DIR=str(tmp_path / "bins"),
                LLVM_PROFILE_FILE=str(tmp_path / "prof" / "goh-%p.profraw"), **env)  # fmt: skip


def _measured(tree: str = "T1") -> dict[str, bool]:
    rec, _ = reuse.lookup_suite(REPO_ROOT, tree, "TOOLS")
    assert rec is not None
    return rec["measured"]


def test_the_plain_goh_leaves_a_mark_only_where_one_is_asked_for(native_goh: Path, tmp_path):
    """The mark is the binary's own: `goh --version` drops one `goh-<pid>.binmark` beside
    LLVM_PROFILE_FILE when that directory holds `.goh-binmark`, and none anywhere else (a caller's
    own profile pool is never written into). Asked in the test's own profile directory when it has
    one: an instrumented run counts the lines that write the mark there."""
    here = os.environ.get("LLVM_PROFILE_FILE")
    asked, other = Path(here).parent if here else tmp_path / "asked", tmp_path / "other"
    for d in (asked, other):
        d.mkdir(parents=True, exist_ok=True)
    (asked / ".goh-binmark").touch()
    before = set(asked.glob("*.binmark"))
    for d in (asked, other):
        env = hermetic_env(LLVM_PROFILE_FILE=str(d / "goh-%4m.profraw"))
        subprocess.run([str(native_goh), "--version"], env=env, check=True, capture_output=True,
                       stdin=subprocess.DEVNULL)  # fmt: skip
    assert len(set(asked.glob("goh-*.binmark")) - before) == 1, list(asked.iterdir())
    assert list(other.glob("*.binmark")) == [], list(other.iterdir())


@pytest.mark.parametrize("workers", [[], ["-n", "2"]], ids=["serial", "xdist"])
def test_a_plain_recording_run_measures_which_tests_ran_the_binary(
    session: Path, store: Path, native_goh: Path, workers: list[str]
) -> None:
    _plain(session, store, native_goh, *workers)
    assert _measured() == {"test_t.py::test_ok": False, "test_t.py::test_bin": True}
    pre = json.loads((session.parent / "pre.json").read_text())
    assert pre["passed"] == {"test_t.py::test_ok": False, "test_t.py::test_bin": True}


def test_the_coverage_run_skips_what_the_plain_run_measured_and_never_a_test_that_ran_the_binary(
    session: Path, store: Path, native_goh: Path, tmp_path: Path
) -> None:
    _plain(session, store, native_goh)
    r = _cov(session, store, native_goh, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASSED test_t.py::test_bin" in r.stdout, r.stdout  # it ran the binary: it runs
    assert "test_t.py::test_ok" not in r.stdout.split("short test summary")[-1], r.stdout
    assert "same-tree passes rerun under coverage" not in r.stdout, r.stdout


def test_an_uncalibrated_worker_measures_nothing(session: Path, store: Path, native_goh, tmp_path):
    """A resolved binary that leaves no mark (an older goh) measures nothing: no node is claimed
    as running none, so the coverage run reruns them all."""
    _plain(session, store, native_goh, CALIBRATE="/usr/bin/true", RUN_BIN="")
    assert _measured() == {}
    r = _cov(session, store, native_goh, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASSED test_t.py::test_ok" in r.stdout and "PASSED test_t.py::test_bin" in r.stdout
    _plain(session, store, native_goh, CALIBRATE="", RUN_BIN="", TREE="T3")  # never calibrated
    assert _measured("T3") == {}


@pytest.mark.parametrize("changed", [{"TREE": "T2", "CHOSEN": "test_t.py"}, {"TOOLS": "TOOLS2"}],
                         ids=["tree", "tools"])  # fmt: skip
def test_a_changed_input_misses(session: Path, store: Path, native_goh: Path, tmp_path,
                                changed: dict[str, str]) -> None:  # fmt: skip
    """Another tree (whose diff chooses the file) or other tools: the plain measurement is not this
    run's (the store's key, lib/reuse.py `carry`: repo, tree, tools), so nothing it claims skips."""
    _plain(session, store, native_goh)
    r = _cov(session, store, native_goh, tmp_path, **changed)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASSED test_t.py::test_ok" in r.stdout, r.stdout


def test_the_audit_sample_runs_a_plain_claim_under_coverage_and_a_refuted_one_is_red(
    session: Path, store: Path, native_goh: Path, tmp_path: Path
) -> None:
    """The plain run's test_bin ran nothing; under coverage it leaves a profile. Sampled (`test`
    one in one), it runs, and the run is red naming it; the tree's record then says it ran."""
    _plain(session, store, native_goh, RUN_BIN="")
    assert _measured()["test_t.py::test_bin"] is False
    r = _cov(session, store, native_goh, tmp_path, EVERY_TEST="1", EVERY_CARRY="1")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "RUN anyway: the audit's shadow" in r.stdout, r.stdout
    assert "test_t.py::test_bin ran the instrumented binary but was carried" in r.stdout, r.stdout
    assert _measured()["test_t.py::test_bin"] is True


def test_a_plain_claim_never_overwrites_a_run_that_saw_the_binary(store: Path) -> None:
    """A node any run saw running the binary on a tree stays so: a later run's `False` (a plain
    mark that missed a route) cannot clear it."""
    reuse.record_suite(REPO_ROOT, "T1", "TOOLS", {"a": True, "b": False}, set(), False, "gate",
                       instrumented=True, measured={"a": True, "b": False})  # fmt: skip
    reuse.record_suite(REPO_ROOT, "T1", "TOOLS", {"a": False, "b": True}, set(), False, "pre-land",
                       measured={"a": False, "b": True})  # fmt: skip
    assert _measured() == {"a": True, "b": True}
