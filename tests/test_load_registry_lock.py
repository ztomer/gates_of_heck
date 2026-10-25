"""The load registry has two writers, and every mutation of it is serialized by ONE lock.

THE CLASS (2026-10-25). `tools/load.py` rewrites the registry once per spinner as it starts them
and removes it when it is done; the minute backstop (`load_reap.reap_registry`) reads it, prunes
it, and writes it back. Neither held a lock, so a read-modify-write could straddle the other
writer's change. Twice that day `load.py --max ... 16 -- ...` hit its deadline and left a stale
`registry.json` naming the dead owner and only SOME of its sixteen spinners, each with its start
time -- 8 pids (owner 634), then 2 (owner 10250): `start()`'s incremental snapshot, written back
by a backstop pass that had read it mid-start. Two shapes of one race:

  * LOST UPDATE: a pass that reads mid-start and writes afterwards leaves the registry naming
    fewer spinners than are burning -- the leak the registry exists to prevent;
  * RESURRECTION: a pass that reads before `finish()` removes the file and writes afterwards
    brings a dead experiment's slot back, and the next `load.py` is refused with "holds an
    experiment".

Each is reproduced at BOTH places a pass can be paused, through the backstop's own seams, with the
real tool's `Experiment` as the other writer and the backstop in a process of its own (the lock is
a lock between processes, so a test inside one process would prove nothing):

  * `before_write` -- under the lock, between the re-read and the write: the tool's mutation must
    WAIT for the pass, and land after it;
  * `after_kill` -- after the slow part (the kills), before the lock is taken: the tool's mutation
    must NOT wait (the lock is never held across a kill), and the pass must apply its prune to
    what the registry holds NOW, not to what it read a kill ago.

And the sibling the same audit found: a pass that sees a registry naming no spinner removed it
even when its owner was alive -- the moment between `claim()` and the first spinner, or an
experiment whose spinners all died under it -- so a second experiment could take a slot the
first still held. The slot is held exactly while its owner lives.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Self

import load_kit
import pytest
import timing_bounds
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import load as tool
import load_reap
import load_registry as reg

pytestmark = pytest.mark.xdist_group("load_orphans")

# The backstop, run as the minute job runs it -- a process of its own -- and paused at one seam
# until the test says go. A pause that is never released gives up on its own clock, so a failing
# test cannot leave it waiting.
BACKSTOP = """
import sys, time
from pathlib import Path
from typing import Self
sys.path.insert(0, sys.argv[1])
import load_reap
path, stage, paused, go = Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]), Path(sys.argv[5])
patience = float(sys.argv[6])
def pause():
    paused.touch()
    give_up = time.monotonic() + patience
    while not go.exists() and time.monotonic() < give_up:
        time.sleep(0.01)
load_reap.reap_registry(path, **{stage: pause})
"""

STAGES = ["before_write", "after_kill"]

# An entry the pass has to prune, so it WRITES: a pass with nothing to drop writes nothing back,
# and a race it cannot lose proves nothing. The pid is beyond macOS's range, so it names no one.
DEAD = {"pid": 999999, "start": "Tue Jan  1 00:00:00 1990"}

# How long the tool's mutation is given to land while the pass is paused. Under the lock it never
# lands (it waits for the pass); outside it, it lands in milliseconds.
LANDS_S = 2.0


def _pids(directory: Path) -> list[int]:
    data = reg.read(reg.registry_path(directory)) or {}
    return sorted(int(entry["pid"]) for entry in data.get("pids", []))


class Paused:
    """A backstop pass, paused at `stage` until `go()` -- the pass the race needs."""

    def __init__(self, directory: Path, stage: str) -> None:
        self.paused = directory / "backstop.paused"
        self.go_file = directory / "backstop.go"
        self.owner = load_kit.owning(
            [
                sys.executable,
                "-c",
                BACKSTOP,
                str(REPO_ROOT / "lib"),
                str(reg.registry_path(directory)),
                stage,
                str(self.paused),
                str(self.go_file),
                str(timing_bounds.patience(60)),
            ]
        )

    def __enter__(self) -> Self:
        self.child = self.owner.__enter__()
        give_up = timing_bounds.GiveUp(timing_bounds.patience(30))
        while not self.paused.exists():
            assert self.child.proc.poll() is None, "the backstop ended before its pause"
            assert not give_up.passed(), "the backstop never reached its pause"
            time.sleep(0.01)
        return self

    def go(self) -> None:
        self.go_file.touch()
        assert self.child.proc.wait(timeout=timing_bounds.patience(30)) == 0

    def __exit__(self, *exc: object) -> None:
        self.go_file.touch()
        self.owner.__exit__(*exc)


def _meanwhile(action) -> tuple[threading.Thread, bool]:
    """Run the tool's mutation while the pass is paused; report whether it had to WAIT."""
    worker = threading.Thread(target=action, daemon=True)
    worker.start()
    worker.join(timeout=LANDS_S)
    return worker, worker.is_alive()


@pytest.mark.parametrize("stage", STAGES)
def test_a_pass_that_read_mid_start_does_not_lose_a_spinner(tmp_path: Path, stage: str) -> None:
    """LOST UPDATE: the pass reads the registry with one spinner, the tool registers the second,
    and the registry must still name both when the pass is done."""
    experiment = tool.Experiment(tmp_path, 2, ["true"])
    experiment.claim()
    first, resume = threading.Event(), threading.Event()
    release = experiment._release

    def release_then_pause(fifo: Path) -> None:
        release(fifo)
        if not first.is_set():
            experiment.data["pids"].append(DEAD)
            reg.write(experiment.registry, experiment.data)
            first.set()
            resume.wait(timeout=timing_bounds.patience(30))

    experiment._release = release_then_pause
    starter = threading.Thread(target=experiment.start, daemon=True)
    try:
        starter.start()
        assert first.wait(timeout=timing_bounds.patience(30)), "the first spinner never started"
        with Paused(tmp_path, stage) as backstop:
            worker, waited = _meanwhile(lambda: (resume.set(), starter.join()))
            backstop.go()
            worker.join(timeout=timing_bounds.patience(30))
        spinners = sorted(proc.pid for proc in experiment.procs)
        assert len(spinners) == 2, spinners
        assert set(spinners) <= set(_pids(tmp_path)), "the registry lost a spinner that is burning"
        assert waited == (stage == "before_write"), (
            "the tool's write did not wait for a pass holding the lock"
            if stage == "before_write"
            else "the tool's write waited for a pass that was only killing"
        )
    finally:
        resume.set()
        starter.join(timeout=timing_bounds.patience(30))
        experiment.finish()


@pytest.mark.parametrize("stage", STAGES)
def test_a_pass_that_read_before_finish_does_not_resurrect_the_slot(
    tmp_path: Path, stage: str
) -> None:
    """RESURRECTION: the pass reads the registry, the tool finishes and removes it, and the pass
    must not bring it back -- else the next experiment is refused by a dead one."""
    experiment = tool.Experiment(tmp_path, 1, ["true"])
    experiment.claim()
    try:
        experiment.start()
        experiment.data["pids"].append(DEAD)
        reg.write(experiment.registry, experiment.data)
        with Paused(tmp_path, stage) as backstop:
            worker, waited = _meanwhile(experiment.finish)
            backstop.go()
            worker.join(timeout=timing_bounds.patience(30))
        assert not reg.registry_path(tmp_path).exists(), "a finished experiment's slot came back"
        assert waited == (stage == "before_write"), (
            "the tool's remove did not wait for a pass holding the lock"
            if stage == "before_write"
            else "the tool's remove waited for a pass that was only killing"
        )
        tool.Experiment(tmp_path, 0, ["true"]).claim()  # the slot is free for the next one
    finally:
        experiment.finish()
        reg.remove(reg.registry_path(tmp_path))


def test_a_live_owner_keeps_its_slot_before_its_first_spinner(tmp_path: Path) -> None:
    """A registry that names no spinner yet is not an empty record: its owner is alive, and a
    pass that removed it handed the slot to a second experiment the first would then clobber."""
    experiment = tool.Experiment(tmp_path, 0, ["true"])
    experiment.claim()
    try:
        load_reap.reap_registry(reg.registry_path(tmp_path))
        assert reg.registry_path(tmp_path).exists(), "a live owner's slot was removed"
        with pytest.raises(FileExistsError):
            tool.Experiment(tmp_path, 0, ["true"]).claim()
    finally:
        experiment.finish()


def test_a_dead_owner_with_no_spinner_left_frees_the_slot(tmp_path: Path) -> None:
    """The negative control: the slot of an owner that is gone is freed, or nothing could ever
    run again after a SIGKILLed experiment. The pid is this process; the start time is not, so
    the owner the record names is gone."""
    path = reg.registry_path(tmp_path)
    reg.write(
        path,
        {
            "version": reg.VERSION,
            "owner_pid": os.getpid(),
            "owner_start": "Tue Jan  1 00:00:00 1990",
            "pids": [],
        },
    )
    report = load_reap.reap_registry(path)
    assert report.removed == [str(path)], report
    assert not path.exists()
