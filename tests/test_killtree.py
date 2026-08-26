"""Timeout orphaning contract: a timed-out gate step must leave NOTHING
running.

The class (2026-08-25): subprocess.run's TimeoutExpired handler kills only the
direct child. Any step shaped `X & Y` (background work under a shell) left
orphans behind every timeout — repeated gate failures accumulated stray
workers nobody could see. All captured-subprocess sites now run their child
in its OWN SESSION (Popen(start_new_session=True)) and, on timeout,
os.killpg() the whole group (fallback p.kill()), then drain the pipes.

Every test here builds a real `background sleep + foreground sleep` shell
step and proves, via pgrep on unique markers embedded in each child's argv,
that NOTHING survives the timeout.
"""

import argparse
import importlib.util
import subprocess
import sys
import time
import uuid

import pytest

from conftest import REPO_ROOT


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


killtree = _load("killtree_under_test", "lib/killtree.py")
fresh = _load("fresh_under_test", "checks/check_generated_fresh.py")
ratchet = _load("ratchet_under_test", "checks/check_baseline_ratchet.py")
sys.path.insert(0, str(REPO_ROOT))
from lib import mcp_scaffold as mc  # noqa: E402


def _orphan_step(marker: str) -> str:
    """Shell step: background + foreground sleeper, each carrying MARKER in
    its argv so pgrep can find them even after their parent shell dies."""
    py = sys.executable
    return (
        f'"{py}" -c "import time; time.sleep(120)" {marker}-bg '
        f"& "
        f'"{py}" -c "import time; time.sleep(120)" {marker}-fg'
    )


def _unique_marker(site: str) -> str:
    # Per-invocation marker: a marker reused across runs would match orphans
    # left by an EARLIER (pre-fix) run still inside its 120s sleep — the test
    # must judge only this run's processes.
    return f"GOHKT-{site}-{uuid.uuid4().hex[:8]}"


def _assert_nothing_survives(marker: str):
    deadline = time.time() + 5
    survivors = None
    while time.time() < deadline:
        r = subprocess.run(
            ["pgrep", "-f", marker], capture_output=True, text=True
        )
        if r.returncode != 0:  # no match: nothing left
            return
        survivors = r.stdout
        time.sleep(0.2)
    raise AssertionError(f"orphaned processes survived the timeout:\n{survivors}")


# ---- the shared helper --------------------------------------------------------


def test_run_captured_returns_completed_process():
    out = killtree.run_captured(
        [sys.executable, "-c", "print('captured-ok')"], timeout=30
    )
    assert out.returncode == 0 and out.stdout.strip() == "captured-ok"


def test_run_captured_kills_whole_group_on_timeout():
    marker = _unique_marker("HELPER")
    with pytest.raises(subprocess.TimeoutExpired):
        killtree.run_captured(
            _orphan_step(marker), shell=True, timeout=1
        )
    _assert_nothing_survives(marker)


def test_run_captured_feeds_stdin_and_captures_both_pipes():
    out = killtree.run_captured(
        [sys.executable, "-c", "import sys; d=sys.stdin.read(); "
         "sys.stderr.write('E'); print(d.upper())"],
        timeout=30, input_text="payload",
    )
    assert out.returncode == 0
    assert out.stdout.strip() == "PAYLOAD" and out.stderr == "E"


# ---- site 1: mcp_scaffold.run_subprocess --------------------------------------


def test_mcp_run_subprocess_leaves_no_orphans_on_timeout():
    marker = _unique_marker("MCP")
    with pytest.raises(mc.SubprocessTimeout):
        mc.run_subprocess(["/bin/bash", "-c", _orphan_step(marker)], timeout=1)
    _assert_nothing_survives(marker)


# ---- site 2: check_generated_fresh.generate -----------------------------------


def test_generated_fresh_generate_leaves_no_orphans_on_timeout(tmp_path):
    marker = _unique_marker("FRESH")
    with pytest.raises(subprocess.TimeoutExpired):
        fresh.generate(_orphan_step(marker), tmp_path, timeout=1)
    _assert_nothing_survives(marker)


# ---- site 3: check_baseline_ratchet.load_current -------------------------------


def test_ratchet_load_current_leaves_no_orphans_on_timeout():
    marker = _unique_marker("RATCHET")
    args = argparse.Namespace(
        current=None,
        current_from_command=_orphan_step(marker),
        timeout=1,
    )
    with pytest.raises(subprocess.TimeoutExpired):
        ratchet.load_current(args)
    _assert_nothing_survives(marker)
