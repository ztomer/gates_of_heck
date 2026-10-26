"""The backstop degrades when a lookup is slow; it never aborts the pass.

THE CLASS (2026-10-25). The backstop runs every minute, and it is needed most when the machine is
busiest -- that is when an orphan is burning. On such a machine `pgrep` and `ps` are slow, and
every one of the backstop's lookups ran with a timeout and nothing to catch it: the launchd log
held eleven tracebacks, nine of them `pgrep -f release-...` timing out after 10 s inside
`reap_orphans`, two of them `ps -ax` timing out after 30 s inside `burning_orphans`. Each one
ended the pass, so the shapes after it never ran, at exactly the moment they were needed.

So a lookup that times out makes the backstop BLIND to one shape for one pass, and says so;
the other shapes still run, and the job exits 0 -- a slow machine is a status, not a fault. A
per-pid read that times out is UNKNOWN, and an unknown is never a reason to kill, to drop an
entry, or to call an owner gone. And a shape that crashes for any other reason no longer takes
the rest of the pass with it: the others run, and the job exits 1 so the fault is seen.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import load_kit
import pytest
import timing_bounds
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))

import load_reap
import load_registry as reg

pytestmark = pytest.mark.xdist_group("load_orphans")

GONE_OWNER_START = "Tue Jan  1 00:00:00 1990"


def _job():
    """The minute job's own module (`tools/load_reap.py`), under a name that is not the library's."""
    spec = importlib.util.spec_from_file_location(
        "load_reap_job", REPO_ROOT / "tools" / "load_reap.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _slow(
    monkeypatch,
    *,
    pgrep: bool = False,
    table: bool = False,
    per_pid: bool = False,
    parent: bool = False,
) -> None:
    """Make the named lookups time out, the way a loaded machine makes them; leave the rest real."""
    real = subprocess.run

    def run(argv, *args, **kwargs):
        slow = (
            (pgrep and argv[0] == "pgrep")
            or (table and argv[:2] == ["ps", "-ax"])
            or (per_pid and argv[0] == "ps" and "-p" in argv)
            or (parent and argv[0] == "ps" and "ppid=" in argv)
        )
        if slow:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0))
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)


def _register_orphan(directory: Path) -> int:
    pid = load_kit.orphan()
    reg.write(
        reg.registry_path(directory),
        {
            "version": reg.VERSION,
            "owner_pid": os.getpid(),
            "owner_start": GONE_OWNER_START,
            "pids": [{"pid": pid, "start": reg.pid_start(pid) or ""}],
        },
    )
    return pid


def test_a_slow_pgrep_leaves_the_rest_of_the_pass_running(tmp_path, monkeypatch, capsys) -> None:
    """The nine tracebacks: `pgrep` timed out, and the pass ended there."""
    pid = _register_orphan(tmp_path)
    job = _job()
    ran = []
    monkeypatch.setattr(
        job.load_reap, "reap_burning_orphans", lambda: ran.append(1) or load_reap.Report()
    )
    _slow(monkeypatch, pgrep=True)
    assert job.main(["--dir", str(tmp_path)]) == 0
    out = capsys.readouterr()
    assert ran, "the burning shape never ran after the house shape's lookup timed out"
    assert not load_kit.alive(pid), "the registry's orphan outlived a pass with a slow pgrep"
    assert "timed out" in out.out and "pgrep" in out.out, out
    assert "Traceback" not in out.err, out.err


def test_a_slow_process_table_is_a_stated_blind_spot(tmp_path, monkeypatch, capsys) -> None:
    """The two tracebacks: `ps -ax` timed out inside the burning shape."""
    job = _job()
    _slow(monkeypatch, table=True)
    assert job.main(["--dir", str(tmp_path)]) == 0
    out = capsys.readouterr()
    assert "timed out" in out.out and "ps -ax" in out.out, out


def test_a_slow_per_pid_read_is_unknown_and_unknown_kills_nothing(tmp_path, monkeypatch) -> None:
    """A read that timed out is not an answer: the owner is not gone, the pid is not reused, the
    spinner is not an orphan. The registry is left as it was, and nothing is killed."""
    with load_kit.owning(["/bin/sh", "-c", "sleep 300"]) as child:
        path = reg.registry_path(tmp_path)
        data = {
            "version": reg.VERSION,
            "owner_pid": os.getpid(),
            "owner_start": reg.pid_start(os.getpid()) or "",
            "pids": [{"pid": child.proc.pid, "start": reg.pid_start(child.proc.pid) or ""}],
        }
        reg.write(path, data)
        _slow(monkeypatch, per_pid=True)
        assert not reg.owner_gone(data), "a live owner whose start time could not be read is gone"
        report = load_reap.reap_registry(path)
        monkeypatch.undo()  # the test's own reads are real
        assert child.proc.pid in report.kept, report
        assert load_kit.alive(child.proc.pid)
        assert reg.read(path)["pids"] == data["pids"]


def test_a_candidate_that_cannot_be_read_back_is_left_alone(monkeypatch) -> None:
    """The house shape's read-back (`ps -p`) timed out: the candidate is unknown, so it is skipped."""
    _slow(monkeypatch, per_pid=True)
    assert load_reap.our_orphans([os.getpid()]) == []


def test_a_shape_that_crashes_does_not_take_the_others_with_it(
    tmp_path, monkeypatch, capsys
) -> None:
    """Any other fault in one shape: the rest of the pass runs, and the job exits 1 so it is seen."""
    job = _job()
    ran = []

    def broken():
        raise RuntimeError("a shape that broke")

    monkeypatch.setattr(job.load_reap, "reap_orphans", broken)
    monkeypatch.setattr(
        job.load_reap, "reap_burning_orphans", lambda: ran.append(1) or load_reap.Report()
    )
    assert job.main(["--dir", str(tmp_path)]) == 1
    assert ran, "the burning shape never ran after the house shape crashed"
    assert "a shape that broke" in capsys.readouterr().err


def _release(fifo: Path) -> None:
    """Hand a spinner its byte, once its reader is there (the load tool's own release)."""
    give_up = timing_bounds.GiveUp(timing_bounds.patience(5))
    while not give_up.passed():
        try:
            handle = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            time.sleep(0.01)
            continue
        os.write(handle, b"\n")
        os.close(handle)
        return


def test_a_record_is_settled_after_every_shape_has_killed(tmp_path, monkeypatch) -> None:
    """The push gate of v0.49.1 (2026-10-25): a SIGKILLed tool's registry outlived its spinners.
    Under load the registry pass could not read a spinner's parent (unknown, so kept), and the
    house-shape sweep that ran AFTER it killed the spinner -- so the record named a dead pid for
    another pass, and the next experiment was refused for a minute. The record is settled last,
    once every shape in the pass has acted."""
    fifo = tmp_path / reg.release_name(999999, 0)  # the owner the pipe names is gone
    os.mkfifo(fifo)
    pid = load_kit.orphan(["/bin/sh", "-c", reg.spinner_command(fifo)])
    _release(fifo)
    reg.write(
        reg.registry_path(tmp_path),
        {
            "version": reg.VERSION,
            "owner_pid": 999999,
            "owner_start": GONE_OWNER_START,
            "pids": [{"pid": pid, "start": reg.pid_start(pid) or ""}],
        },
    )
    job = _job()
    _slow(monkeypatch, parent=True)
    assert job.main(["--dir", str(tmp_path)]) == 0
    monkeypatch.undo()
    assert load_kit.wait_until(lambda: not load_kit.alive(pid)), "the orphan outlived the pass"
    assert not reg.registry_path(tmp_path).exists(), "the record outlived the spinner it named"
