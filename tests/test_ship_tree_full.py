"""An idle full run on a tree, then the ship's @tree run on it, leave it FULL (GOH.SHIPTREEFULL).

The v0.139.6 ship failed its suite step in 199 s on a green run: "the suite record of tree
81510329d921 is no full run". Its record held 6450 passes and 6449 `judged`; the odd one out was
`test_orphan_canary.py::test_a_missing_runner_is_refused_not_silently_unbounded[<checkout>]`, a node
id naming the checkout it was collected in. The land gate (in a push checkout) carried it from the
parent tree; the idle lander and the ship ran in other checkouts, so they collected, ran and judged
THEIR id, never that one, and CARRYASFULL's rule (lib/reuse.py record_suite: no full record holds a
pass outside `judged`) kept the tree unfull for good. Pinned here end to end: a carried pass a full
run never meets (an id no checkout of its own collects) leaves the record when a full run judges
the tree, so the record is full; a pass the full run meets is run and judged, as before.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from test_verdict_carry import SESSION_CONFTEST, _gate

sys.path.insert(0, str(REPO_ROOT / "lib"))
import reuse
import ship_full_run

# One node's id names the checkout that collected it, as test_orphan_canary's did. The id is
# explicit: pytest 9.2 auto-ids a str value over 100 characters as argname+index (its
# parametrize_long_str_id_strategy "short"), so on a runner whose tmp path is long the auto id was
# `test_here[where0]` and named no checkout (GOH.CIRED01396).
TEST_T = """import pytest
from pathlib import Path

WHERE = str(Path(__file__).resolve().parent)

def test_ok():
    pass

@pytest.mark.parametrize("where", [WHERE], ids=[WHERE])
def test_here(where):
    pass
"""
S, OK = "test_s.py::test_pass", "test_t.py::test_ok"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl"))
    for k in ("GOH_REUSE_TTL_S", "GOH_REUSE_NOW"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path / "store"


def _checkout(tmp_path: Path, name: str) -> Path:
    d = tmp_path / name
    d.mkdir()
    (d / "conftest.py").write_text(SESSION_CONFTEST)
    (d / "test_s.py").write_text("def test_pass():\n    pass\n")
    (d / "test_t.py").write_text(TEST_T)
    return d


def _full(d: Path, store: Path, tmp_path: Path, **reuse_key: str) -> subprocess.CompletedProcess:
    """The idle lander's run (lib/idle_suite.py: the store's record alone) or, with the @tree key,
    the ship's (lib/ship_full_run.py); both instrumented, both on T1."""
    (tmp_path / "prof").mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA"],
        cwd=d, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
        env=hermetic_env(drop_git=True, GOH_REUSE_DIR=str(store), GOH_LOOPCTL=str(d / "no-loopctl"),
                         GOH_LAND_VERDICTS_RECORD=os.devnull, TREE="T1",
                         GOH_COVERAGE_BIN_DIR=str(tmp_path / "bins"),
                         LLVM_PROFILE_FILE=str(tmp_path / "prof" / "goh-%p.profraw"), **reuse_key),
    )  # fmt: skip


def _t1() -> dict:
    rec, why = reuse.lookup_suite(REPO_ROOT, "T1", "TOOLS")
    assert rec is not None, why
    return rec


def test_an_idle_full_run_then_the_ships_run_on_one_tree_leave_it_full(
    tmp_path: Path, store: Path
) -> None:
    push, idle, ship = (_checkout(tmp_path, n) for n in ("push", "idle", "ship"))
    here = f"test_t.py::test_here[{push.resolve()}]"
    # T0's gate ran in the push checkout; T1's land does too, and the full runs in two others.
    reuse.record_suite(REPO_ROOT, "T0", "TOOLS", dict.fromkeys((S, OK, here), False), set(), True,
                       "gate")  # fmt: skip
    land = _gate(push, store, CHOSEN="test_s.py")  # T1's land: S runs, OK and `here` carried
    assert land.returncode == 0 and "2 deselected, carried from" in land.stdout, land.stdout
    assert here in _t1()["passed"] and _t1()["judged"] == [S], _t1()
    first = _full(idle, store, tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr
    # GOH.TIMINGSBLEED: the idle lander's whole instrumented run is no pre-land run, and says so
    assert _t1()["by"] == "full run", _t1()
    then = _full(ship, store, tmp_path, GOH_LAND_VERDICTS_REUSE=ship_full_run.REUSE)
    assert then.returncode == 0, then.stdout + then.stderr
    rec = _t1()
    assert rec["full"] and rec["instrumented"] and rec["by"] == "full run", rec
    assert here not in rec["passed"], rec  # no checkout of the full runs collects it
    assert OK in rec["judged"] and set(rec["passed"]) <= set(rec["judged"]), rec


def test_a_full_run_drops_only_the_unjudged_passes_it_never_met(store: Path) -> None:
    """The store side: a full run's own unjudged pass still refuses `full` (CARRYASFULL), an old
    judged pass it did not meet stays, an old carried one it did not meet leaves."""
    reuse.record_suite(REPO_ROOT, "T", "TOOLS", {S: False, OK: False}, set(), False, "land gate",
                       1, judged={S})  # fmt: skip
    reuse.record_suite(REPO_ROOT, "T", "TOOLS", {"x::y": False}, set(), True, "full run")
    rec = reuse.lookup_suite(REPO_ROOT, "T", "TOOLS")[0]
    assert rec["full"] and set(rec["passed"]) == {S, "x::y"}, rec
    reuse.record_suite(REPO_ROOT, "T", "TOOLS", {OK: False}, set(), True, "full run", judged=set())
    assert not reuse.lookup_suite(REPO_ROOT, "T", "TOOLS")[0]["full"]
