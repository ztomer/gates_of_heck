"""Every item goh files into the loop's queue goes through the loop's REAL rules (GOH.REUSEFAULTVA):
the real `loopctl file` on a temp copy of the live queue -- its phases, its `rules`, its
queue_rules/triggers -- through the filers' own seams (GOH_LOOPCTL, GOH_LOOP_QUEUE). A hand-written
fake loopctl copies the rules it knew of; when the loop added `verify_after`
(triggers.verify_problems) every reuse path that turned off filed nothing, and the fake still said
"filed". The filers:

    lib/reuse_fault.py file_fault     a reuse path turned off
    lib/ship_full_run.py file_fault   a red ship's bisect naming its culprit land
    lib/flakes.py record              a test's second pass on retry in 14 days (GOH.FLAKEREGISTER)
    lib/diffcov_carry.py file_item    tools/landq.py's landing: a land's own uncovered lines
    scripts/spend_watch.py _file      a spend regression; scripts/model_choice_watch.py files
                                      through it (spend_watch.run), so one test is both
    hooks/claude/spawn_register.py    `loopctl start` of the pending-spawn item a brief names
    lib/ship_item.py item_refusal     `loopctl shippable`, goh ship --item's preflight

Each is red-proved by a field the loop requires dropped from what the filer sends
(GOH.FILERCONTRACT2).

Runs only where .loop is checked out (never in goh's CI); the hermetic fake in
tests/test_reuse_audit.py mirrors the same rule for CI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import diffcov_carry
import flakes
import pytest
import reuse
import reuse_fault
import ship_full_run
import ship_item
import suite_home
from conftest import REPO_ROOT
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import spend_watch  # scripts/, put on the path above

# The REAL home's: the suite home hides the loop's checkout (suite_home.HIDDEN), as
# tests/test_agent_stalls_loop.py names it.
REAL_HOME = Path(os.environ.get(suite_home.MARKER) or suite_home.REAL_HOME)
LOOP = REAL_HOME / "Projects" / ".loop"
pytestmark = pytest.mark.skipif(
    not (LOOP / "tools" / "loopctl.py").is_file(),
    reason="no .loop checkout (its rules are what is filed through)",
)


OPEN = ("todo", "in_progress")


def live_queue() -> dict:
    """The live queue as the loop holds it. LOOP.QUEUEARCHIVE moves it to loop.db: this one read
    then goes through goh's queue reader (lib/queue_read.py)."""
    return json.loads((LOOP / "queue.json").read_text())


@pytest.fixture
def live(tmp_path, monkeypatch) -> Path:
    """The real loopctl, on a temp copy of the live queue (never the live one)."""
    return arm(live_queue(), tmp_path, monkeypatch)


def arm(data: dict, tmp_path: Path, monkeypatch) -> Path:
    """Point goh's filers at the real loopctl over a temp copy of queue DATA."""
    queue = tmp_path / "loop" / "queue.json"
    queue.parent.mkdir()
    # The rules, phases and closed items as they are; the OPEN items out. `loopctl file` refuses an
    # item whose text overlaps an open one (loopdup), so a live open ship-red item ate the test's
    # own synthetic one (2026-11-21: GOH.SHIPRED12163BB1 vs "a red land"): the test passed or
    # failed by what the queue held that day.
    data = {**data, "items": [i for i in data["items"] if i.get("status") not in OPEN]}
    # The rules minus the owner's freezes (GOH.FILERLIVEFREEZE): FRZ.QUEUEDB-GOH froze
    # gates_of_heck on 2026-11-23, the live rules refused both probes, and every goh gate went
    # red until it lifted. A freeze is a legitimate state of the queue, never a rules regression.
    data["rules"] = {k: v for k, v in data.get("rules", {}).items() if k != "freezes"}
    queue.write_text(json.dumps(data))
    monkeypatch.setenv("GOH_LOOPCTL", str(LOOP / "tools" / "loopctl.py"))
    monkeypatch.setenv("GOH_LOOP_QUEUE", str(queue))
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "reuse"))
    monkeypatch.delenv("LOOPCTL_UMBRELLA", raising=False)
    # The loop's rules resolve a repo token under ~/Projects (queue_rules.ROOT, expanduser): the
    # real home's, not the suite home that hides the checkouts (the land's pre-step refused
    # "repo token 'gates_of_heck' is no repo under ~/Projects" without it).
    monkeypatch.setenv("HOME", str(REAL_HOME))
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


def test_a_second_flake_in_14_days_files_its_item_through_the_real_queue_rules(
    live, tmp_path, monkeypatch
) -> None:
    """lib/flakes.py's item -- `flake_records`, SPD's metric and prediction, `class_of: none` --
    as the loop's own rules take it, on the temp queue (LOOPCTL_QUEUE too), never the live one."""
    monkeypatch.setenv("GOH_FLAKE_DIR", str(tmp_path / "flakes"))  # HOME is the real one here
    monkeypatch.setenv("LOOPCTL_QUEUE", str(live))
    test = "tests/test_filer_live_rules.py::a_synthetic_flake"
    flakes.record(test, str(tmp_path / "gates_of_heck"), "a" * 40, "site", ("/l/1", "/l/2"))
    rec = flakes.record(test, str(tmp_path / "gates_of_heck"), "b" * 40, "site")
    assert rec["filed"] == f"filed {flakes.item_id(test)}", rec
    [item] = [i for i in json.loads(live.read_text())["items"] if i["id"] == flakes.item_id(test)]
    assert len(item["flake_records"]) == 2 and item["status"] == "todo", item
    # Another test's flake reads like this one's open item to loopdup: it is filed, not refused.
    other = "tests/test_filer_live_rules.py::another_synthetic_flake"
    flakes.record(other, str(tmp_path / "gates_of_heck"), "c" * 40, "site")
    assert flakes.record(other, str(tmp_path / "gates_of_heck"), "d" * 40, "site")["filed"] == (
        f"filed {flakes.item_id(other)}")  # fmt: skip


def test_a_landing_with_its_own_uncovered_line_files_its_item_through_the_real_queue_rules(
    live,
) -> None:
    """tools/landq.py's filer: a landing that carried a line of its own files GOH.DIFFCOV<sha>."""
    carry = {"file": "crates/goh/src/x.rs", "line": 4, "text": "let x = 1;", "own": True,
             "why": "external suite only, or no test", "measured": True}  # fmt: skip
    outcome = {
        "state": "landed",
        "sha": "feedc0de" * 5,
        "repo": str(REAL_HOME / "Projects" / "gates_of_heck"),
    }  # the lander's main checkout
    loopctl = os.environ["GOH_LOOPCTL"]
    got = diffcov_carry.file_item(outcome, {"cov_risk": {"carry": [carry]}}, loopctl, "GOH.LANE")
    assert got == {"diffcov_item": {"id": "GOH.DIFFCOVFEEDC0DE", "said": "filed"}}, got


def test_a_spend_regression_files_its_item_through_the_real_queue_rules(live) -> None:
    """scripts/spend_watch.py's (and so model_choice_watch's) item, for every metric it watches."""
    loopctl = Path(os.environ["GOH_LOOPCTL"])
    for key in spend_watch.METRICS:
        iid = f"SPEND.WORSE.{key.upper()}.2026-W48"
        item = spend_watch._item(iid, key, f"the {key} metric rose past its tolerance", "under 1")
        got = spend_watch._file(loopctl, live, item)
        assert got == {"id": iid, "outcome": "filed"}, got


def test_a_spawn_registers_its_pending_item_through_the_real_queue_rules(live) -> None:
    """hooks/claude/spawn_register.py's `loopctl start`, on the temp queue, of the item it names."""
    spawn_probe(live)


def spawn_probe(live: Path) -> None:
    sys.path.insert(0, str(REPO_ROOT / "hooks" / "claude"))
    import queue_hold
    import spawn_register

    data = json.loads(live.read_text())
    item = {"id": "GOH.FILERSPAWNPROBE", "repo": "gates_of_heck", "phase": "DF",
            "status": "in_progress", "agent": queue_hold.PENDING_SPAWN, "depends": [],
            "item": "a synthetic pending-spawn item the contract test registers",
            "done_state": "registered", "class_of": "none", "class_why": "a probe"}  # fmt: skip
    data["items"].append(item)
    live.write_text(json.dumps(data))
    argv = spawn_register.start_argv(str(live), item, "a0contract0probe")
    argv[0:2] = [sys.executable, os.environ["GOH_LOOPCTL"]]  # the real loopctl, not a copy's
    # Outside a Claude session the id is taken as given (.loop loopagentid.resolve); inside one the
    # hook hands the id the spawn returned, which that session's transcripts hold.
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
    r = subprocess.run(argv, cwd=live.parent, env=env, capture_output=True, text=True,
                       check=False, stdin=subprocess.DEVNULL, timeout=patience(120))  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    [got] = [i for i in json.loads(live.read_text())["items"] if i["id"] == item["id"]]
    assert got["agent"] == "a0contract0probe", got


def test_a_ship_item_is_asked_of_the_real_queue_rules(live, monkeypatch) -> None:
    """lib/ship_item.py's `loopctl shippable`: an open gates_of_heck item may ship from it."""
    ship_probe(live, monkeypatch)


def ship_probe(live: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOOPCTL_QUEUE", str(live))
    data = json.loads(live.read_text())
    data["items"].append({"id": "GOH.FILERSHIPPROBE", "repo": "gates_of_heck", "phase": "DF",
                          "status": "todo", "depends": [], "item": "a synthetic ship probe",
                          "done_state": "shipped", "class_of": "none",
                          "class_why": "a probe"})  # fmt: skip
    live.write_text(json.dumps(data))
    loopctl = Path(os.environ["GOH_LOOPCTL"])
    assert ship_item.item_refusal(REPO_ROOT, loopctl, "GOH.FILERSHIPPROBE", "gates_of_heck") is None
    why = ship_item.item_refusal(REPO_ROOT, loopctl, "GOH.FILERSHIPPROBE", "monitor")
    assert why and "refuses it" in why, why


def test_an_owner_freeze_on_gates_of_heck_never_reds_the_probes(tmp_path, monkeypatch) -> None:
    """GOH.FILERLIVEFREEZE: a freeze on gates_of_heck planted in a copy of the live rules (as
    FRZ.QUEUEDB-GOH was on 2026-11-23). The probes still pass: the freeze is the owner's, the rules
    under it are what the filers are checked against."""
    data = live_queue()
    rules = data.setdefault("rules", {})
    rules["freezes"] = [*(rules.get("freezes") or []), {
        "id": "FRZ.FILERPROBE", "repo": "gates_of_heck", "since": "2026-11-23",
        "frozen": "every item but a planted one", "owner_words": "a planted freeze",
        "lift": "never: the test's own copy", "allow_items": ["GOH.NOTAPROBE"]}]  # fmt: skip
    live = arm(data, tmp_path, monkeypatch)
    spawn_probe(live)
    ship_probe(live, monkeypatch)
