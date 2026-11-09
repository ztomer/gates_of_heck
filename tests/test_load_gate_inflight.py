"""tools/load.py refuses to starve another session's gate that is in flight (GOH.LOADX).

THE CLASS (2026-11-01). The load tool refused a SECOND load experiment, and nothing else: a divoom
repro ran 15 spinners twice (load average 137 on 16 cores) while a private consumer's pre-push gate -- which
fails past 30 minutes -- was running beside it. One experiment's load is another session's red
gate, and the gate's log blames nothing.

THE REGISTRY is the one every goh gate already joins (lib/bench_lock.sh: a file named by the
gate's pid in $GOH_BENCH_LOCK_DIR/gates, holding its stage and its repo), read by ONE helper
(lib/gates_in_flight.py). A dead pid is not a gate; neither is a pid the kernel handed to a
process that started after the entry was written; neither is the caller's own ancestor (a gate
that runs load.py inside itself chose its own load).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import load_kit
import timing_bounds
from conftest import REPO_ROOT
from test_commit_gate_structural import _commit, _consumer

from tui.lib import ICON_ERR

TOOL = REPO_ROOT / "tools" / "load.py"
BENCH = REPO_ROOT / "lib" / "bench_lock.sh"
EXIT_GATE = 75
QUICK = ["/bin/sh", "-c", "exit 0"]


def _gates() -> Path:
    """The private registry this test's autouse fixture gave it (tests/host_locks.py)."""
    gates = Path(os.environ["GOH_BENCH_LOCK_DIR"]) / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    return gates


def _register(
    pid: int, stage: str = "pre-push", repo: str = "/Users/someone/Projects/consumer"
) -> Path:
    entry = _gates() / str(pid)
    entry.write_text(f"{stage}\n{repo}\n")
    return entry


def _run(tmp_path: Path, *options: str, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), "--dir", str(tmp_path / "load"), *options, "1", "--", *QUICK],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timing_bounds.patience(timeout),
        check=False,
    )  # fmt: skip


def _gate_process() -> load_kit.Owned:
    """A live process standing in for another session's gate; the suite owns and reaps it."""
    return load_kit.Owned(["/bin/sleep", "120"])


def test_a_live_gate_in_flight_is_refused_and_named(tmp_path: Path) -> None:
    (tmp_path / "load").mkdir()
    try:
        gate = _gate_process()
        _register(gate.proc.pid)
        result = _run(tmp_path)
        assert result.returncode == EXIT_GATE, (result.returncode, result.stdout, result.stderr)
        said = result.stderr
        assert "pre-push" in said and "/Users/someone/Projects/consumer" in said, said
        assert f"pid {gate.proc.pid}" in said and "running" in said, said
        assert "--wait" in said and "--despite-gate" in said, said
        assert "spinner(s) started" not in result.stdout, "it burned before it refused"
    finally:
        load_kit.reap()


def test_the_override_runs_beside_a_gate(tmp_path: Path) -> None:
    (tmp_path / "load").mkdir()
    try:
        gate = _gate_process()
        _register(gate.proc.pid)
        result = _run(tmp_path, "--despite-gate")
        assert result.returncode == 0, result.stderr
        assert "spinner(s) started" in result.stdout, result.stdout
    finally:
        load_kit.reap()


def test_a_dead_pid_is_not_a_gate_and_its_entry_is_pruned(tmp_path: Path) -> None:
    (tmp_path / "load").mkdir()
    dead = subprocess.Popen(["/bin/sh", "-c", "exit 0"], stdin=subprocess.DEVNULL)
    dead.wait(timeout=30)
    entry = _register(dead.pid)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert not entry.exists(), "a dead gate's entry was left for the next reader"


def test_a_recycled_pid_is_not_the_gate_that_registered(tmp_path: Path) -> None:
    """The entry was written BEFORE the live process with that pid started: the kernel reused
    the number, and the process holding it now is not a gate."""
    (tmp_path / "load").mkdir()
    try:
        gate = _gate_process()
        entry = _register(gate.proc.pid)
        os.utime(entry, (time.time() - 3600, time.time() - 3600))
        result = _run(tmp_path)
        assert result.returncode == 0, result.stderr
    finally:
        load_kit.reap()


def test_the_callers_own_gate_does_not_refuse_it(tmp_path: Path) -> None:
    """A gate that runs load.py inside itself (this test is load.py's ancestor) chose its load."""
    (tmp_path / "load").mkdir()
    _register(os.getpid(), stage="pre-push", repo=str(REPO_ROOT))
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_wait_starts_once_the_gate_exits(tmp_path: Path) -> None:
    directory = tmp_path / "load"
    directory.mkdir()
    gate = _gate_process()
    _register(gate.proc.pid)
    with load_kit.owned(directory, 1, QUICK, options=("--wait", "--max", "120")) as tool:
        assert load_kit.wait_until(lambda: "waiting" in tool.spoken()), tool.spoken()
        assert tool.proc.poll() is None, f"it did not wait: {tool.spoken()!r}"
        assert not tool.spinner_pids(), "it burned while the gate was in flight"
        gate.stop()
        tool.proc.wait(timeout=timing_bounds.patience(60))
        assert tool.proc.returncode == 0, tool.spoken()
        assert "spinner(s) started" in tool.spoken(), tool.spoken()


def test_the_wait_has_its_own_bound_separate_from_max(tmp_path: Path) -> None:
    """GOH.LOADWAIT (2026-11-08): the wait was bounded by the EXPERIMENT's --max, so ZN.APPCHECK's
    `--wait` held 25-50 min and once spent its whole budget queueing. `--wait-max` bounds the wait
    alone; a long --max does not lengthen it."""
    (tmp_path / "load").mkdir()
    try:
        gate = _gate_process()
        _register(gate.proc.pid)
        started = time.monotonic()
        result = _run(tmp_path, "--wait", "--wait-max", "2", "--max", "600")
        assert result.returncode == EXIT_GATE, (result.returncode, result.stderr)
        assert time.monotonic() - started < timing_bounds.patience(30)
        assert "spinner(s) started" not in result.stdout, "it burned after the wait expired"
    finally:
        load_kit.reap()


def test_an_expired_wait_names_what_it_waited_for_and_the_load(tmp_path: Path) -> None:
    """On expiry it says WHICH gate it waited on (stage, repo, pid), for how long, and the load
    reading it saw, through the Kare lib's `err` -- not a bare print a reader cannot tell apart."""
    (tmp_path / "load").mkdir()
    try:
        gate = _gate_process()
        _register(gate.proc.pid)
        result = _run(tmp_path, "--wait", "--wait-max", "1")
        assert result.returncode == EXIT_GATE, (result.returncode, result.stderr)
        said = result.stderr
        assert f"{ICON_ERR} load: --wait-max of 1s expired" in said, said
        assert f"pid {gate.proc.pid}" in said and "/Users/someone/Projects/consumer" in said, said
        assert "load average" in said and "core(s)" in said, said
    finally:
        load_kit.reap()


def test_the_wait_does_not_queue_behind_gates_that_arrive_later(tmp_path: Path) -> None:
    """The cause of the unbounded wait: it waited for the machine to be CLEAR, and with many
    sessions a new gate starts before the last one ends (measured 2026-11-08: a ZoneWM pre-push
    of 8 min overlapped by a stream of monitor pre-commits). A gate that starts after the wait
    began is no worse off than one that starts a second after the experiment does: the wait
    queues behind the gates AHEAD of it, and names the late ones."""
    directory = tmp_path / "load"
    directory.mkdir()
    first = _gate_process()
    _register(first.proc.pid)
    with load_kit.owned(directory, 1, QUICK, options=("--wait",)) as tool:
        assert load_kit.wait_until(lambda: "waiting" in tool.spoken()), tool.spoken()
        late = _gate_process()
        _register(late.proc.pid, stage="pre-commit", repo="/Users/someone/Projects/monitor")
        time.sleep(1.5)  # more than a poll: the waiter has seen the late gate
        first.stop()
        tool.proc.wait(timeout=timing_bounds.patience(30))
        said = tool.spoken()
        assert tool.proc.returncode == 0, said
        assert "spinner(s) started" in said, said
        assert f"pid {late.proc.pid}" in said, (
            f"the late gate it did not wait for is unnamed: {said}"
        )


def test_a_hook_that_dies_leaves_no_live_entry(tmp_path: Path) -> None:
    """The real registration, SIGKILLed before its cleanup: the file stays, the gate does not."""
    (tmp_path / "load").mkdir()
    repo = tmp_path / "consumer"
    repo.mkdir()
    killed = subprocess.run(
        ["bash", "-c", f'. "{BENCH}"; bench_lock_join pre-commit; echo "$BENCH_LOCK_ENTRY"; kill -9 $$'],
        cwd=repo,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )  # fmt: skip
    entry = Path(killed.stdout.strip())
    assert entry.exists(), f"the join wrote nothing: {killed.stdout!r} {killed.stderr!r}"
    assert entry.read_text() == f"pre-commit\n{repo}\n", entry.read_text()
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert not entry.exists(), "the dead hook's entry was not pruned"


def test_the_commit_hook_registers_its_stage_and_repo(tmp_path: Path) -> None:
    """goh's own pre-commit entry point is in the registry for as long as its gate runs."""
    gate = (
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'cat "$GOH_BENCH_LOCK_DIR/gates/$PPID" > "$SCRATCH/entry"\n'
        'exec bash "$GOH_DIR/gates/structural.sh" --staged\n'
    )
    repo = _consumer(tmp_path, gate)
    got = _commit(repo, tmp_path, "notes\n")
    assert got.returncode == 0, got.stdout + got.stderr
    entry = (tmp_path / "scratch" / "entry").read_text()
    assert entry.splitlines()[:2] == ["pre-commit", str(repo)], entry
    assert not any(_gates().iterdir()), "the commit gate's entry outlived it"
