"""The five saved-pid liveness probes GOH.STRAYSIGNAL2 left bare, each moved onto lib/pid_owner.

Each record named a process by its pid alone, and `kill -0`/`os.kill(pid, 0)` answered yes for
whatever process the kernel handed the number to after the recorded one died (GOH.STRAYSIGNAL3):

  * lib/bench_lock.sh   a gate's entry in gates/ (the drain waited on a stranger)
  * lib/lock_order.py   a GOH_HELD_LOCKS mark (an order refusal naming a holder long gone)
  * lib/load_registry   a release pipe's owner pid (the backstop kept a dead tool's pipe)
  * lib/place.sh        a staging file's pid (a killed copy's stage never reaped)
  * tools/land.sh       GOH_LAND_LOCK_OWNER's pid (a dead ship's child skipped the land queue)

The kernel cannot be made to reuse a number on demand, so each test seats a STAND-IN -- a sleeper
of the test's own -- at the pid the record names, with the record older than the stand-in (a file's
mtime set an hour back, or a mark carrying another process's birth). The probe must read the
stand-in as gone; the real writer, recorded after it started, as alive. Every process is the test's.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import load_reap
import load_registry as reg
import lock_order
import pid_owner

pytestmark = pytest.mark.load_sensitive(reason="spawns and kills its own stand-in sleepers")
TREE_READS = ("lib/",)  # the five libs it sources and imports
AN_HOUR_AGO = time.time() - 3600


@contextmanager
def stand_in() -> Iterator[subprocess.Popen]:
    proc = subprocess.Popen(["/bin/sleep", "60"], stdin=subprocess.DEVNULL)
    try:
        yield proc
    finally:
        proc.kill()
        proc.wait()


def _aged(path: Path) -> Path:
    """The record as a dead writer left it: written before the stand-in at its pid started."""
    os.utime(path, (AN_HOUR_AGO, AN_HOUR_AGO))
    return path


def _bash(lib: str, script: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f'. "{REPO_ROOT / "lib" / lib}"\n{script}'],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          env=hermetic_env(**env), timeout=patience(60),
                          check=False)  # fmt: skip


def test_the_bench_drain_reads_a_stand_in_at_a_gates_pid_as_gone(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    (lock / "gates").mkdir(parents=True)
    with stand_in() as proc:
        entry = lock / "gates" / str(proc.pid)
        entry.write_text("pre-commit\n/a/repo\n")
        _aged(entry)
        done = _bash("bench_lock.sh", 'bench_lock_exclusive "probe" 3 && echo held',
                     GOH_BENCH_LOCK_DIR=str(lock))  # fmt: skip
        assert done.stdout.strip() == "held", done.stderr
        assert not entry.exists(), "the stand-in's stale entry was not pruned"
        mine = lock / "gates" / str(proc.pid)
        mine.write_text("pre-commit\n/a/repo\n")  # written after the process started: its own
        done = _bash("bench_lock.sh", f'_bench_running {proc.pid} "{mine}" && echo running',
                     GOH_BENCH_LOCK_DIR=str(lock))  # fmt: skip
        assert done.stdout.strip() == "running", done.stderr


def test_a_lock_mark_whose_pid_is_a_stand_ins_names_no_holder() -> None:
    with stand_in() as gone:
        was = pid_owner.stamp(gone.pid)
    assert was is not None
    with stand_in() as proc:
        # Linux's birth is /proc's starttime, in clock ticks (10 ms): a stand-in started in the
        # gone one's tick has ITS birth, and the mark below was the real holder's on the worker
        # (GOH.WORKERFAILS). Another's birth is then one tick earlier, which no live pid has.
        mine = pid_owner.stamp(proc.pid)
        other = was.born if mine is None or mine.born != was.born else (was.born[0] - 1, 0)
        born = f"{other[0]}.{other[1]}"  # lock_order's mark spelling of a birth
        for mark in (f"load@{proc.pid}", f"load@{proc.pid}+{born}"):  # no birth; another's birth
            assert lock_order.held({lock_order.ENV: mark}) == [], mark
            assert lock_order.refusal("land", {lock_order.ENV: mark}) is None, mark
        now = pid_owner.stamp(proc.pid)
        assert now is not None
        real = f"load@{proc.pid}+{now.born[0]}.{now.born[1]}"
        assert lock_order.held({lock_order.ENV: real}) == [("load", proc.pid)]
        assert lock_order.refusal("land", {lock_order.ENV: real}) is not None


def test_the_backstop_sweeps_a_release_pipe_whose_owner_pid_is_a_stand_ins(tmp_path: Path) -> None:
    with stand_in() as proc:
        stale = tmp_path / reg.release_name(proc.pid, 0)
        os.mkfifo(stale)
        _aged(stale)
        assert load_reap.sweep_release_pipes(tmp_path) == [str(stale)]
        live = tmp_path / reg.release_name(proc.pid, 1)
        os.mkfifo(live)  # made after its owner started: the owner's own
        assert reg.owner_alive(live, proc.pid)
        assert load_reap.sweep_release_pipes(tmp_path) == []
        assert not reg.owner_alive(tmp_path / "release-1-9.fifo", proc.pid), "no pipe, no owner"


def test_a_placement_reaps_a_stage_whose_pid_is_a_stand_ins(tmp_path: Path) -> None:
    dest = tmp_path / "bin" / "tool"
    dest.parent.mkdir()
    with stand_in() as proc:
        stale = Path(f"{dest}.{proc.pid}.staging")
        stale.write_text("half a binary")
        _aged(stale)
        live = Path(f"{dest}.{os.getpid()}.staging")
        live.write_text("mid-copy")  # this process's, written after it started
        done = _bash("place.sh", f'goh_place_reap "{dest}"')
        assert done.returncode == 0, done.stderr
        assert not stale.exists(), "a killed copy's stage was kept for the stand-in at its pid"
        assert live.exists(), "a live copy's stage was reaped"


def test_a_land_under_a_dead_holders_pid_is_not_the_holders(tmp_path: Path) -> None:
    lock = tmp_path / "goh-land.lock"
    with stand_in() as proc:
        lock.write_text(f"goh ship (pid {proc.pid}, since 00:00:00)\n")
        _aged(lock)
        owner = f"{lock} {proc.pid}"
        done = _bash("land_lock.sh", f'goh_land_lock_ours "{lock}" && echo ours || echo queue',
                     GOH_LAND_LOCK_OWNER=owner)  # fmt: skip
        assert done.stdout.strip() == "queue", done.stderr
        lock.write_text(f"goh ship (pid {proc.pid}, since 00:00:00)\n")  # the holder's own line
        done = _bash("land_lock.sh", f'goh_land_lock_ours "{lock}" && echo ours || echo queue',
                     GOH_LAND_LOCK_OWNER=owner)  # fmt: skip
        assert done.stdout.strip() == "ours", done.stderr
