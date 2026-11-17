"""Every entry point that gates joins the host's measurement lock, and waits while it is held.

Split from test_bench_lock.py (the 500-line cap); the helpers are that file's.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from test_bench_lock import _env, _hold
from timing_bounds import patience

pytestmark = pytest.mark.load_sensitive(reason="host locks held by spawned holders")


def _waits_then_runs(tmp_path: Path, argv: list[str], cwd: Path, stdin: str = "") -> str:
    """Run a real entry point while the host is held: it must wait, then finish once released.

    The holder is released when the gate SAYS it is waiting, never after a fixed sleep: under load
    a gate that has not yet reached the lock in 2.5 s was released first and never said it
    (GOH.SELFGATEPS's class, a fixed wall wait the load stretches)."""
    lock = tmp_path / "lock"
    holder = _hold(lock, tmp_path)
    gate = subprocess.Popen(argv, cwd=cwd, env=_env(lock), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)  # fmt: skip
    seen: list[str] = []
    said = threading.Event()

    def read_stderr() -> None:
        for line in gate.stderr:
            seen.append(line)
            if "waiting for a measurement" in line:
                said.set()

    reader = threading.Thread(target=read_stderr, daemon=True)
    reader.start()
    try:
        arrived = said.wait(patience(30))
        still = gate.poll() is None
    finally:
        holder.kill()
        holder.wait(timeout=patience(10))
    gate.communicate(stdin, timeout=patience(60))
    reader.join(timeout=patience(10))
    err = "".join(seen)
    assert arrived, f"{argv[1]} never said it was waiting for a measurement:\n{err}"
    assert still, f"{argv[1]} ran while a measurement held the host:\n{err}"
    return err


def test_a_gate_slow_to_reach_the_lock_is_still_held_until_it_says_it_waits(
    repo: Path, tmp_path: Path
) -> None:
    """The faked load: the gate reaches the lock 4 s after it starts, past the old fixed 2.5 s."""
    slow = ["bash", "-c", f'sleep 4; exec bash "{REPO_ROOT}/gates/local_ci.sh" --step true']
    err = _waits_then_runs(tmp_path, slow, repo)
    assert "waiting for a measurement" in err


def test_every_gate_joins_through_goh_init(repo: Path, tmp_path: Path) -> None:
    (repo / "a.py").write_text("x = 1\n")
    _waits_then_runs(tmp_path, ["bash", str(REPO_ROOT / "gates" / "py_staged.sh"), "."], repo)


def test_local_ci_joins(repo: Path, tmp_path: Path) -> None:
    _waits_then_runs(tmp_path, ["bash", str(REPO_ROOT / "gates" / "local_ci.sh"), "--step", "true"],
                     repo)  # fmt: skip


def test_the_push_gate_joins(repo: Path, tmp_path: Path) -> None:
    _waits_then_runs(tmp_path, ["bash", str(REPO_ROOT / "gates" / "push_gate.sh"), "origin", "u"],
                     repo)  # fmt: skip
