"""The flake register (lib/flakes.py, GOH.FLAKEREGISTER): every pass-on-retry is one appended
record, a test with two in 14 days files exactly one item, the lander names the entry, and
`goh flakes` lists the register.

91 fault lines from 71 agents said a test "passed when run again"; the retry greened the run and
nothing was kept, and one such "load flake" was a real regression (GOH.BACKSTOPSTAMP, 53eee0ba)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from test_reuse_audit import fake_loopctl, filed, store  # noqa: F401  (fixtures)

sys.path.insert(0, str(REPO_ROOT / "lib"))
import flakes  # lib/, after its path
import land_records
import reuse_audit
import small_stores

STEP = {"repo": "/r/.git", "tree": "t" * 40, "step": "./gates/rust_gate.sh", "closure": "off",
        "identity": "i" * 64, "env": ""}  # fmt: skip
TEST = "tests/test_x.py::test_races"


@pytest.fixture(autouse=True)
def register(tmp_path, monkeypatch) -> Path:
    """Each test its own register (the GOH_FLAKE_DIR seam): never the machine's."""
    monkeypatch.setenv("GOH_FLAKE_DIR", str(tmp_path / "flakes"))
    return tmp_path / "flakes" / "register.jsonl"


def _rows(path: Path) -> list[dict]:
    """The register's records (goh.db's flakes rows beside its directory, GOH.STATEDBS3)."""
    return [r for r in small_stores.flakes(path.parent) if "test" in r]


def test_a_forced_fail_then_pass_at_a_retry_site_writes_one_record(register) -> None:
    """The proven step's shadow (gates/local_ci.sh `_again`): fresh red, run again green. The
    retry greened the step and, before, nothing was kept; now one record names it."""
    reuse_audit.compare("step", STEP, "green", "red", lambda: ("green", "green"),
                        logs=("/l/step-1.log", "/l/step-1.again.log"))  # fmt: skip
    [rec] = _rows(register)
    assert rec["test"] == STEP["step"] and rec["repo"] == STEP["repo"], rec
    assert rec["sha"] == STEP["tree"] and (rec["first"], rec["second"]) == ("red", "green"), rec
    assert rec["log"] == {"first": "/l/step-1.log", "second": "/l/step-1.again.log"}, rec
    assert isinstance(rec["load"], float) and rec["id"] == f"{flakes.key(STEP['step'])}.1", rec
    # A red the retry repeats is a disagreement, an agreement no retry at all: neither is a flake.
    reuse_audit.compare("step", STEP, "green", "red", lambda: ("green", "red"))
    reuse_audit.compare("step", STEP, "green", "green")
    assert len(_rows(register)) == 1


def test_two_in_14_days_files_exactly_one_item(register, fake_loopctl) -> None:  # noqa: F811
    first = flakes.record(TEST, "/p/gates_of_heck", "a" * 40, "site", ("/l/1", "/l/2"))
    assert "filed" not in first and filed(fake_loopctl) == [], first
    second = flakes.record(TEST, "/p/gates_of_heck", "b" * 40, "site", ("/l/3", "/l/4"))
    third = flakes.record(TEST, "/p/gates_of_heck", "c" * 40, "site")
    [item] = filed(fake_loopctl)
    assert second["filed"] == f"filed {flakes.item_id(TEST)}" and "filed" not in third, second
    assert item["id"] == flakes.item_id(TEST) and item["status"] == "todo", item
    assert item["phase"] == "SPD" and item["regression"] is True and item["class_of"] == "none"
    assert item["class_why"] and [r["id"] for r in item["flake_records"]] == [
        first["id"], second["id"]], item  # fmt: skip
    assert "/l/1" in item["item"] and len(_rows(register)) == 3


def test_a_record_older_than_14_days_files_nothing(register, fake_loopctl) -> None:  # noqa: F811
    """A record of 1970 (an epoch, not a clock read) and one now: one in 14 days, nothing filed."""
    small_stores.flake_add(register.parent, TEST, {"id": f"{flakes.key(TEST)}.1", "epoch": 1.0,
                                                   "test": TEST})  # fmt: skip
    assert flakes.record(TEST, "/p/r", "b" * 40, "site")["id"] == f"{flakes.key(TEST)}.2"
    flakes.record("tests/test_y.py::test_other", "/p/r", "b" * 40, "site")
    assert filed(fake_loopctl) == []


def test_a_re_enqueue_on_the_same_commit_that_lands_is_a_pass_on_retry(tmp_path) -> None:
    done = tmp_path / "done"
    done.mkdir()
    tip = "e" * 40
    red = {
        "state": "red",
        "item": "GOH.LANE",
        "sha": tip,
        "log": "/q/red.log",
        "load_end": 61.5,
        "why": f"enqueue pre-step: the pre-land tests failed on {tip[:8]} (failing: {TEST}) --",
    }
    other = {**red, "sha": "f" * 40, "why": "exit 1"}  # another commit: a fix, not a retry
    (done / "01-GOH.LANE.json").write_text(json.dumps(other))
    (done / "02-GOH.LANE.json").write_text(json.dumps(red))
    landed = {"state": "landed", "mode": "land", "sha": "d" * 40, "enqueued": tip, "attempt": 3,
              "repo": "/p/gates_of_heck", "log": "/q/green.log"}  # fmt: skip
    got = flakes.land_retry(land_records.records(done.parent), "GOH.LANE", landed)
    [rec] = _rows(flakes.register())
    assert got == {"flakes": [{"entry": rec["id"], "test": TEST, "filed": ""}]}, got
    assert rec["load"] == 61.5 and rec["log"] == {"first": "/q/red.log", "second": "/q/green.log"}
    [line] = flakes.said({**landed, **got})
    assert f"flake register entry {rec['id']}" in line and "flaky" not in line, line
    assert (
        flakes.land_retry(land_records.records(done.parent), "GOH.LANE", {**landed, "attempt": 1})
        == {}
    )


def test_the_lander_names_the_register_entry_on_the_landing_record(tmp_path, monkeypatch):
    import importlib.util

    from test_diffcov_carry import FAKE_LOOPCTL

    spec = importlib.util.spec_from_file_location("landq_flakes", REPO_ROOT / "tools" / "landq.py")
    landq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(landq)
    q = tmp_path / "landq"
    for d in ("running", "done", "logs"):
        (q / d).mkdir(parents=True)
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    monkeypatch.setenv("GOH_LOOPCTL", str(fake))
    monkeypatch.setenv("FAKE_LOOPCTL_LOG", str(tmp_path / "calls"))
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path / "timing"))
    tip = "fedcba9876543210"
    red = {
        "state": "red",
        "item": "GOH.LANE",
        "sha": tip,
        "mode": "land",
        "why": f"the pre-land tests failed on {tip[:8]} (failing: {TEST}) -- fix them",
    }
    (q / "done" / "00-GOH.LANE.json").write_text(json.dumps(red))
    req = {"sha": tip, "repo": str(tmp_path), "mode": "land", "item": "GOH.LANE",
           "worktree": str(tmp_path)}  # fmt: skip
    path = q / "running" / "01-GOH.LANE.json"
    path.write_text(json.dumps(req))
    outcome = {"sha": "0" * 16, "enqueued": tip, "repo": str(tmp_path), "mode": "land",
               "state": "landed"}  # fmt: skip
    landq.record(path, outcome, "GOH.LANE")
    done = json.loads((q / "done" / path.name).read_text())
    assert [f["test"] for f in done["flakes"]] == [TEST], done


def test_goh_flakes_lists_the_register(register) -> None:
    flakes.record(TEST, "/p/r", "a" * 40, "land re-enqueue (tools/landq.py)", ("/l/red", ""))
    cli = [sys.executable, str(REPO_ROOT / "lib" / "flakes.py")]
    got = subprocess.run(cli, capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL)
    assert TEST in got.stdout and f"{flakes.key(TEST)}.1" in got.stdout and "/l/red" in got.stdout
    js = subprocess.run([*cli, "--json"], capture_output=True, text=True, check=True,
                        stdin=subprocess.DEVNULL)  # fmt: skip
    assert [json.loads(x)["test"] for x in js.stdout.splitlines()] == [TEST]
