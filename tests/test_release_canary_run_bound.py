"""A canary run is bounded by CPU stall, never by a wall clock alone (GOH.CANARYFLAKE).

`structural()` ran one side's `goh structural --full --keep-going` under `communicate(timeout=900)`.
At load 80-150, with four canary jobs and two other lands beside it, a run starved past 900 s of
WALL was killed and the consumer read "past its 900 s ceiling": a refusal for a tree that passes
when the machine is quieter, the class `check_empty_scope` already left (GOH.CANARYLOAD). The
run's bound is the house's: a STALL (no CPU, nothing runnable) of the nominal ceiling, and a wall
BACKSTOP four times it. Starvation is made deterministic through the sampler seam, as in
tests/test_empty_scope_starved.py.
"""

from __future__ import annotations

import importlib.util
import stat
from pathlib import Path

import pgid_cpu
import pid_owner
import pytest
from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "release_canary.py"
GREEN = "· a step (≤1800s)\n✓ a step\n✓ all structural gates passed\n"


def _canary():
    spec = importlib.util.spec_from_file_location("release_canary_run_bound", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _side(mod, tmp_path: Path, body: str):
    (tmp_path / "green.txt").write_text(GREEN)
    bin_ = tmp_path / "goh"
    bin_.write_text(f"#!/bin/bash\n{body}\n")
    bin_.chmod(bin_.stat().st_mode | stat.S_IXUSR)
    tree = tmp_path / "tree"
    tree.mkdir(exist_ok=True)
    return mod.Side("head", bin_, tmp_path, tmp_path), tree


@pytest.mark.load_sensitive
def test_a_run_starved_past_the_nominal_ceiling_still_gets_its_verdict(tmp_path, monkeypatch):
    mod = _canary()
    monkeypatch.setattr(mod, "RUN_TIMEOUT_S", 1)
    # Runnable and spending no CPU: what a starved process looks like to the sampler.
    monkeypatch.setattr(pgid_cpu, "pgid_cpu_and_runnable", lambda _pgid: (0.0, 1))
    side, tree = _side(mod, tmp_path, f"sleep 2.5\ncat {tmp_path / 'green.txt'}")
    got = mod.structural(side, tree)
    assert got.error is None and got.green == {"a step"}, got


@pytest.mark.load_sensitive
def test_a_run_that_spends_nothing_and_has_nothing_runnable_is_stopped_and_named(
    tmp_path, monkeypatch
):
    mod = _canary()
    monkeypatch.setattr(mod, "RUN_TIMEOUT_S", 1)
    monkeypatch.setattr(pgid_cpu, "pgid_cpu_and_runnable", lambda _pgid: (0.0, 0))
    mark = tmp_path / "pid"
    side, tree = _side(mod, tmp_path, f"echo $$ $(ps -o lstart= -p $$) > {mark}\nexec sleep 600")
    got = mod.structural(side, tree)
    assert got.error and "stalled" in got.error, got
    monkeypatch.undo()
    # stopped with everything it started, not left: by its start time, never a reused pid
    assert not pid_owner.alive(pid_owner.read_stamp(mark))


def test_a_run_that_finishes_is_parsed_as_it_always_was(tmp_path):
    mod = _canary()
    side, tree = _side(mod, tmp_path, f"cat {tmp_path / 'green.txt'}")
    got = mod.structural(side, tree)
    assert got.error is None and got.green == {"a step"} and not got.red, got
