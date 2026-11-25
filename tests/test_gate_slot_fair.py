"""The gate slots are fair: a commit gate never waits behind a long gate, and a freed slot goes to
the longest live waiter, a ship waiter first (GOH.SLOTFAIR, lib/gate_slot.py LANES and Ticket).

2026-11-16 12:0x: a commit gate (`.loop`'s `tools/gate.sh --staged`, about 3 min of work) waited
38+ min for one of the 2 gate slots, held by a 49-min cargo test run and a 15-min push gate; the day
before, a monitor push waited 20m30s while six gates that asked AFTER it each took a freed slot
(whichever try-lock landed first won). Pinned here, with fake holders and waiters on a private
slot directory (conftest's host_locks):

  * a `short` gate is admitted at once while long holders hold every slot -- through the real
    commit hook, which marks its gate.sh `short`;
  * long waiters are served first come, first served, even when the later one polls faster;
  * a `ship` waiter (push, land) goes ahead of an earlier `work` waiter;
  * a SIGKILLed waiter's ticket does not block the queue;
  * a class is the admission's own word: load.py consumes GOH_GATE_CLASS, a bad one is refused.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import load_kit
import pytest
import timing_bounds
from conftest import REPO_ROOT, hermetic_env
from orphan_kit import adopt, wait_for_file_py
from test_commit_gate_structural import _consumer, _git
from test_gate_slot import Gate, _env

LIB = REPO_ROOT / "lib"
LOAD = REPO_ROOT / "tools" / "load.py"

# A waiter: asks for a slot through THE admission (lib/admission.py admit), says TAKEN, then holds
# it until its release file appears. `gate_class` is passed only when the test names one.
WAITER = """
import os, sys, time
sys.path.insert(0, {lib!r})
import admission
kw = {{}} if not {klass!r} else {{"gate_class": {klass!r}}}
admission.admit({slots_dir!r}, {slots}, os.getpid(), {name!r}, poll={poll}, **kw)
print("TAKEN", flush=True)
{wait}"""


class Waiter:
    """A fake gate admitted in-process, its output (the wait report included) in a file."""

    def __init__(self, tmp_path: Path, name: str, slots: int, poll: float, klass: str = "") -> None:
        self.name = name
        self.release = tmp_path / f"release-{name}"
        self.log = tmp_path / f"{name}.out"
        (tmp_path / "slots").mkdir(exist_ok=True)  # lib/gate_slot.py hold makes it before admit
        code = WAITER.format(
            lib=str(LIB), klass=klass, slots_dir=str(tmp_path / "slots"), slots=slots,
            name=name, poll=poll, wait=wait_for_file_py(self.release),
        )  # fmt: skip
        with open(self.log, "w") as out:
            self.proc = subprocess.Popen(
                [sys.executable, "-c", code], stdout=out, stderr=subprocess.STDOUT,
                env=hermetic_env(NO_COLOR="1", GOH_MEM_ADMIT="0"), stdin=subprocess.DEVNULL,
                start_new_session=True,
            )  # fmt: skip
        adopt(self.proc)

    def out(self) -> str:
        return self.log.read_text()

    def taken(self) -> bool:
        return "TAKEN" in self.out()

    def wait_for(self, needle: str, seconds: float = 20) -> None:
        def seen() -> bool:
            if self.proc.poll() is not None and needle not in self.out():
                raise AssertionError(f"{self.name} exited {self.proc.returncode}: {self.out()!r}")
            return needle in self.out()

        assert load_kit.wait_until(seen, timing_bounds.patience(seconds)), (
            f"{self.name}: {needle!r} not seen; got {self.out()!r}"
        )

    def queued(self) -> None:
        """Wait until it has its ticket and found every slot held (the first wait report)."""
        self.wait_for("are held")

    def finish(self) -> None:
        self.release.touch()
        self.proc.wait(timeout=timing_bounds.patience(30))

    def kill(self) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):  # macOS: EPERM once reaped
            os.killpg(self.proc.pid, signal.SIGKILL)
        self.proc.wait(timeout=timing_bounds.patience(30))


def _cleanup(*waiters: Waiter | Gate | None) -> None:
    for w in waiters:
        if w is not None:
            w.kill()


@pytest.mark.load_sensitive
def test_a_short_gate_is_admitted_while_long_gates_hold_every_slot(tmp_path):
    """The inversion of 2026-11-16, ended: two long holders, and a short gate takes its lane."""
    holders = [Waiter(tmp_path, f"long-{i}", 2, 0.05) for i in range(2)]
    short = None
    try:
        for h in holders:
            h.wait_for("TAKEN")
        short = Waiter(tmp_path, "commit", 2, 0.05, klass="short")
        short.wait_for("TAKEN", 20)  # ordered, not timed: the holders end only when released
        assert all(h.proc.poll() is None and h.taken() for h in holders)
    finally:
        _cleanup(short, *holders)


@pytest.mark.load_sensitive
def test_long_waiters_are_served_first_come_first_served(tmp_path):
    """A slow-polling earlier waiter still gets the freed slot before a fast-polling later one."""
    holder = Waiter(tmp_path, "holder", 1, 0.05)
    first = second = None
    try:
        holder.wait_for("TAKEN")
        first = Waiter(tmp_path, "first", 1, 2.0)
        first.queued()
        second = Waiter(tmp_path, "second", 1, 0.02)
        second.queued()
        holder.finish()
        assert load_kit.wait_until(
            lambda: first.taken() or second.taken(), timing_bounds.patience(20)
        ), (first.out(), second.out())
        time.sleep(timing_bounds.patience(0.3))  # time to take it, had it been allowed
        assert first.taken() and not second.taken(), (first.out(), second.out())
        first.finish()
        second.wait_for("TAKEN", 20)
    finally:
        _cleanup(first, second, holder)


@pytest.mark.load_sensitive
def test_a_ship_waiter_goes_ahead_of_an_earlier_work_waiter(tmp_path):
    """Land and push over the rest (D-0008): rank before arrival."""
    holder = Waiter(tmp_path, "holder", 1, 0.05)
    work = ship = None
    try:
        holder.wait_for("TAKEN")
        work = Waiter(tmp_path, "agent-test-run", 1, 0.05, klass="work")
        work.queued()
        ship = Waiter(tmp_path, "push", 1, 0.05, klass="ship")
        ship.queued()
        holder.finish()
        ship.wait_for("TAKEN", 20)
        time.sleep(timing_bounds.patience(0.3))  # time to take it, had it been allowed
        assert not work.taken(), work.out()
        ship.finish()
        work.wait_for("TAKEN", 20)
    finally:
        _cleanup(work, ship, holder)


@pytest.mark.load_sensitive
def test_a_dead_waiter_does_not_block_the_queue(tmp_path):
    """No `finally` runs on SIGKILL: the ticket stays on disk, unlocked, and is reaped."""
    holder = Waiter(tmp_path, "holder", 1, 0.05)
    dead = alive = None
    try:
        holder.wait_for("TAKEN")
        dead = Waiter(tmp_path, "dead", 1, 0.05)
        dead.queued()
        dead.kill()
        waiting = tmp_path / "slots" / "waiting"
        assert any(n.startswith("long-") for n in os.listdir(waiting)), "no ticket left to reap"
        alive = Waiter(tmp_path, "alive", 1, 0.05)
        alive.queued()
        holder.finish()
        alive.wait_for("TAKEN", 20)
        assert not [n for n in os.listdir(waiting) if n.startswith("long-")], os.listdir(waiting)
    finally:
        _cleanup(alive, dead, holder)


SHORT_GATE = """#!/usr/bin/env bash
set -euo pipefail
. "$GOH_DIR/tui/lib.sh"; . "$GOH_DIR/lib/gate_slot.sh"
trap gate_slot_release EXIT
gate_slot_acquire repo
echo "STAGED-TAKEN class=[${GOH_GATE_CLASS:-}]"
bash "$GOH_DIR/gates/structural.sh" --staged
"""


@pytest.mark.load_sensitive
def test_the_commit_hook_admits_its_gate_in_the_short_lane(tmp_path):
    """`.loop`'s shape: a gate.sh --staged that takes a slot, committed while every slot is held."""
    env = _env(tmp_path, 1, GOH_MEM_ADMIT="0")
    holder = Gate(tmp_path, "push-holder", env)
    try:
        holder.wait_for("TAKEN")
        repo = _consumer(tmp_path, SHORT_GATE)
        (repo / "notes.md").write_text("a note\n")
        _git(repo, "add", "notes.md")
        got = subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "ours"],
            env=hermetic_env(
                GOH_GATE_SLOTS="1", GOH_GATE_SLOT_DIR=env["GOH_GATE_SLOT_DIR"], GOH_MEM_ADMIT="0",
                NO_COLOR="1",
            ),
            capture_output=True, text=True, timeout=timing_bounds.patience(60),
            stdin=subprocess.DEVNULL, check=False,
        )  # fmt: skip
        text = got.stdout + got.stderr
        assert got.returncode == 0, text
        assert "short-lane gate slot 1/1 taken" in text, text
        assert "STAGED-TAKEN class=[]" in text, text  # consumed by the acquire
        assert holder.proc.poll() is None, "the long holder was not holding"
    finally:
        holder.kill()


@pytest.mark.load_sensitive
def test_a_load_run_consumes_its_class_and_says_it(tmp_path):
    env = hermetic_env(
        NO_COLOR="1", GOH_DIR=str(REPO_ROOT), GOH_MEM_ADMIT="0", GOH_GATE_SLOTS="1",
        GOH_GATE_SLOT_DIR=str(tmp_path / "slots"), GOH_LOAD_DIR=str(tmp_path / "load"),
        GOH_GATE_CLASS="ship",
    )  # fmt: skip
    got = subprocess.run(
        [sys.executable, str(LOAD), "--background-ok", "test", "--wait-max", "20s", "0", "--",
         "/bin/sh", "-c", 'echo "CLASS=[${GOH_GATE_CLASS:-}]"'],
        env=env, capture_output=True, text=True, timeout=timing_bounds.patience(60),
        stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    text = got.stdout + got.stderr
    assert got.returncode == 0, text
    assert "class ship" in text and "CLASS=[]" in text, text


def test_an_unknown_class_is_refused(tmp_path):
    script = (
        f'. "{REPO_ROOT}/tui/lib.sh"; . "{REPO_ROOT}/lib/gate_slot.sh"; gate_slot_acquire r bogus'
    )
    got = subprocess.run(
        ["bash", "-c", script], env=_env(tmp_path, 1, GOH_MEM_ADMIT="0"), capture_output=True,
        text=True, timeout=timing_bounds.patience(60), stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    assert got.returncode == 2, got.stdout + got.stderr
    assert "short, pre, land, ship, lane or work" in got.stdout + got.stderr
