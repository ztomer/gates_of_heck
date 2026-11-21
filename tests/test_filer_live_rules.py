"""Every item goh files into the loop's queue goes through the loop's REAL rules (GOH.REUSEFAULTVA):
the real `loopctl file` on a temp copy of the live queue -- its phases, its `rules`, its
queue_rules/triggers -- through the filers' own seams (GOH_LOOPCTL, GOH_LOOP_QUEUE). A hand-written
fake loopctl copies the rules it knew of; when the loop added `verify_after`
(triggers.verify_problems) every reuse path that turned off filed nothing, and the fake still said
"filed". The filers:

    lib/reuse_fault.py file_fault     a reuse path turned off
    lib/ship_full_run.py file_fault   a red ship's bisect naming its culprit land

Runs only where .loop is checked out (never in goh's CI); the hermetic fake in
tests/test_reuse_audit.py mirrors the same rule for CI."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import reuse
import reuse_fault
import ship_full_run
import suite_home

# The REAL home's: the suite home hides the loop's checkout (suite_home.HIDDEN), as
# tests/test_agent_stalls_loop.py names it.
REAL_HOME = Path(os.environ.get(suite_home.MARKER) or suite_home.REAL_HOME)
LOOP = REAL_HOME / "Projects" / ".loop"
pytestmark = pytest.mark.skipif(
    not (LOOP / "tools" / "loopctl.py").is_file(),
    reason="no .loop checkout (its rules are what is filed through)",
)


@pytest.fixture
def live(tmp_path, monkeypatch) -> Path:
    """The real loopctl, on a temp copy of the live queue (never the live one)."""
    queue = tmp_path / "loop" / "queue.json"
    queue.parent.mkdir()
    shutil.copy(LOOP / "queue.json", queue)
    monkeypatch.setenv("GOH_LOOPCTL", str(LOOP / "tools" / "loopctl.py"))
    monkeypatch.setenv("GOH_LOOP_QUEUE", str(queue))
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "reuse"))
    monkeypatch.delenv("LOOPCTL_UMBRELLA", raising=False)
    return queue


def test_a_reuse_path_off_files_its_item_through_the_real_queue_rules(live) -> None:
    """GOH.REUSEFAULTVA: contract_fields gave `metric` and `predicted` and no `verify_after`; the
    loop refused the prediction ("nothing re-measures it") and the off was silent."""
    p = reuse.ReusePath(name="filercontract", fields=("k",), ttl_s=60, doc="the filer contract")
    said = reuse_fault.file_fault(p, "miss", "a key input was not keyed", {"fresh_from": "x"})
    assert said == "filed GOH.REUSEOFFFILERCONTRACT", said


def test_a_red_ship_bisect_files_its_item_through_the_real_queue_rules(live, tmp_path) -> None:
    """The sibling filer: the ship's bisect item carried the same `metric`/`predicted` pair with no
    `verify_after`, so a red ship's culprit would have been refused the same way."""
    repo = tmp_path / "gates_of_heck"
    said = ship_full_run.file_fault(repo, "deadbeef" * 5, "c" * 40, "b" * 40, "a red land")
    assert said == "filed GOH.SHIPREDDEADBEEF", said
