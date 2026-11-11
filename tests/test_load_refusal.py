"""A busy machine's refusal names the holder and the exact rerun line; a 0-spinner run waits.

GOH.LOADREFUSE (2026-11-11): two agents met `load: another load experiment already holds the slot`
on a gate they ran with N=0 and stepped around it by running the gate bare, which loads the
machine just the same and is tracked by nothing. The ruling is docs/decisions/D-0002: the right
path is the easy one. A refusal prints the line to paste (`--wait` added), `--wait` waits for
the slot as well as for the gates in flight, and a run with no spinners waits by default.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
import time
from pathlib import Path

import load_kit
import pytest
import timing_bounds
from conftest import REPO_ROOT

TOOL = REPO_ROOT / "tools" / "load.py"
pytestmark = pytest.mark.xdist_group("load_orphans")  # with tests/test_load_tool.py


def _run(args: list[str], timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timing_bounds.patience(timeout),
        check=False,
    )  # fmt: skip


def test_a_busy_slot_refusal_names_the_holder_and_the_exact_rerun_line(tmp_path: Path) -> None:
    args = ["--dir", str(tmp_path), "1", "--", "/bin/sh", "-c", "exit 0"]
    with load_kit.owned(tmp_path, 1, ["/bin/sh", "-c", "sleep 30 # the-holder"]) as tool:
        tool.wait_for_spinners()
        second = _run(args)
    assert second.returncode == 3, (second.returncode, second.stderr)
    assert "the-holder" in second.stderr, second.stderr  # the holder, by its command
    line = f"python3 {shlex.quote(str(TOOL))} --wait {shlex.join(args)}"
    assert line in second.stderr, second.stderr


def test_wait_takes_the_slot_once_the_holder_is_done(tmp_path: Path) -> None:
    with load_kit.owned(tmp_path, 1, ["/bin/sh", "-c", "sleep 2"]) as tool:
        tool.wait_for_spinners()
        t0 = time.monotonic()
        second = _run(["--dir", str(tmp_path), "--wait", "1", "--", "/bin/sh", "-c", "exit 5"])
    assert second.returncode == 5, (second.returncode, second.stderr)
    assert time.monotonic() - t0 < timing_bounds.patience(30)


def test_a_run_with_no_spinners_waits_for_the_slot_without_being_asked(tmp_path: Path) -> None:
    with load_kit.owned(tmp_path, 1, ["/bin/sh", "-c", "sleep 2"]) as tool:
        tool.wait_for_spinners()
        gate = _run(["--dir", str(tmp_path), "0", "--", "/bin/sh", "-c", "exit 0"])
    assert gate.returncode == 0, (gate.returncode, gate.stderr)
    assert "waiting for the slot" in gate.stderr + gate.stdout


def test_the_slot_wait_is_bounded_and_says_what_it_waited_on(tmp_path: Path) -> None:
    with load_kit.owned(tmp_path, 1, ["/bin/sh", "-c", "sleep 30 # the-holder"]) as tool:
        tool.wait_for_spinners()
        late = _run(["--dir", str(tmp_path), "--wait-max", "1", "0", "--", "true"])
    assert late.returncode == 3, (late.returncode, late.stderr)
    assert "--wait-max of 1s expired" in late.stderr and "the-holder" in late.stderr, late.stderr


def test_a_wait_that_ends_without_running_the_command_is_not_a_pass(tmp_path: Path) -> None:
    """A CadGoose2 agent's `load.py --wait ... -- git push` looked green while nothing was pushed:
    a wait that runs out never ran the command, so it exits nonzero naming the holder."""
    ran = tmp_path / "ran"
    with load_kit.owned(tmp_path, 1, ["/bin/sh", "-c", "sleep 30 # the-holder"]) as tool:
        tool.wait_for_spinners()
        late = _run(["--dir", str(tmp_path), "--wait", "--wait-max", "1", "1", "--",
                     "/bin/sh", "-c", f"touch {shlex.quote(str(ran))}"])  # fmt: skip
    assert late.returncode != 0, (late.returncode, late.stdout, late.stderr)
    assert not ran.exists(), "the wrapped command ran while the slot was held"
    assert "the-holder" in late.stderr and "still held" in late.stderr, late.stderr
