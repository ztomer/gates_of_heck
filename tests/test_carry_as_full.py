"""A pass carried onto a tree's own record is never a pass judged on it (GOH.CARRYASFULL).

Found by GOH.SHIPBREACH0137: a gate on tree T1 that carried passes from T0 recorded them in T1's
own record (`passed`, outside `judged`). A second gate on T1 (a land re-enqueued on the same tree)
took that record as "the same tree" (tests/_land_verdicts.py `_carry`), counted nothing as carried
and recorded T1 FULL, so the release kit (`reuse.py full-suite`) and the ship's skip
(lib/ship_full_run.py `skip_reason`) stood on passes judged on another tree. Pinned here: such a
run is no full verdict and names what it carried, and the store refuses `full` for any record
with a pass no run judged on its tree (lib/reuse.py record_suite).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from test_verdict_carry import SESSION_CONFTEST, _gate

sys.path.insert(0, str(REPO_ROOT / "lib"))
import reuse

# The source tree comes from the environment: T0 (another tree) or T1 (the gate's own).
CONFTEST = SESSION_CONFTEST + 'lv.trees = lambda root, pre: [("c0" * 20, os.environ["SRC"])]\n'
S, OK, B = "test_s.py::test_pass", "test_t.py::test_ok", "test_t.py::test_also"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl"))
    for k in ("GOH_REUSE_TTL_S", "GOH_REUSE_NOW"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path / "store"


def _t1() -> dict:
    rec, why = reuse.lookup_suite(REPO_ROOT, "T1", "TOOLS")
    assert rec is not None, why
    return rec


def test_a_gate_on_its_own_record_of_carried_passes_is_no_full_verdict(
    tmp_path: Path, store: Path
) -> None:
    d = tmp_path / "s"
    d.mkdir()
    (d / "conftest.py").write_text(CONFTEST)
    (d / "test_s.py").write_text("def test_pass():\n    pass\n")
    (d / "test_t.py").write_text("def test_ok():\n    pass\n\ndef test_also():\n    pass\n")
    reuse.record_suite(REPO_ROOT, "T0", "TOOLS", dict.fromkeys((S, OK, B), False), set(), True,
                       "gate")  # fmt: skip
    first = _gate(d, store, CHOSEN="test_s.py", SRC="T0")  # T1's gate: S run, OK and B carried
    assert first.returncode == 0 and "2 deselected, carried from" in first.stdout, first.stdout
    assert _t1()["judged"] == [S] and not _t1()["full"], _t1()
    again = _gate(d, store, SRC="T1")  # the same tree again: its own record is the only source
    assert again.returncode == 0, again.stdout + again.stderr
    rec = _t1()
    assert not rec["full"] and rec["carried"] == 2, rec  # OK and B were judged on T0 alone
    assert rec["judged"] == [S], rec


def test_the_store_refuses_full_for_a_record_holding_a_pass_judged_on_no_run_of_its_tree(
    store: Path,
) -> None:
    reuse.record_suite(REPO_ROOT, "T", "TOOLS", {S: False, OK: False}, set(), True, "gate",
                       judged={S})  # fmt: skip
    assert not reuse.lookup_suite(REPO_ROOT, "T", "TOOLS")[0]["full"]
    reuse.record_suite(REPO_ROOT, "T", "TOOLS", {OK: False}, set(), True, "gate", judged={OK})
    assert reuse.lookup_suite(REPO_ROOT, "T", "TOOLS")[0]["full"]  # now every pass is judged
    reuse.record_suite(REPO_ROOT, "U", "TOOLS", {S: False}, set(), True, "gate")
    # A caller naming no `judged` ran every pass it records.
    assert reuse.lookup_suite(REPO_ROOT, "U", "TOOLS")[0]["judged"] == [S]
