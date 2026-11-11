"""A build or a gate run bare while the load slot is held is refused at the tool boundary
(GOH.LOADBUILDLOCK, 2026-11-11).

Two agents ran `load.py --wait --wait-max 2700 1 -- swift test` (exit 2, the machine-wide lock
refusal), then `load.py --wait ... --shared-lock-ok "..." 0 -- swift test` (exit 3, "another load
experiment already holds the slot ... owner pid 24457 ... command: tools/land.sh": the load.py of
before GOH.LOADREFUSE, which that land.sh was landing), and then ran `swift test` bare. The lock
refusal now names the 0-spinner line that waits for the slot, and the Bash guard refuses the bare
run while a live experiment that is not the hook's own ancestor holds the slot.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import timing_bounds
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))

import load_registry as reg

GUARD = REPO_ROOT / "hooks" / "claude" / "discard_guard.py"
TOOL = REPO_ROOT / "tools" / "load.py"
SWIFT = "cd tui/menubar && swift test --package-path . --filter PreferencesLeakTests"


def _guard(command: str, load_dir: Path) -> str | None:
    event = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(load_dir)}
    ran = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(event), capture_output=True,
                         text=True, timeout=30, check=False,
                         env={**os.environ, "GOH_LOAD_DIR": str(load_dir)})  # fmt: skip
    assert ran.returncode == 0, ran.stderr
    if not ran.stdout.strip():
        return None
    return json.loads(ran.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.fixture
def held(tmp_path: Path):
    """The slot held by a live process that is not the guard's ancestor (another repo's land.sh)."""
    owner = subprocess.Popen(["sleep", "60"], stdin=subprocess.DEVNULL)
    try:
        reg.create(reg.registry_path(tmp_path), {
            "version": reg.VERSION, "owner_pid": owner.pid,
            "owner_start": reg.pid_start(owner.pid) or "", "started_at": 0, "max_s": 60,
            "deadline_at": 0, "command": ["tools/land.sh"], "pids": [],
        })  # fmt: skip
        yield tmp_path, owner.pid
    finally:
        owner.kill()
        owner.wait()


@pytest.mark.parametrize(
    "command",
    [SWIFT, "cargo test -q", "taskpolicy -c utility cargo build", "./tools/gate.sh --full",
     "bash tools/gate.sh"],
)  # fmt: skip
def test_a_bare_build_or_gate_under_a_held_slot_is_refused_with_the_wait_line(held, command):
    load_dir, pid = held
    why = _guard(command, load_dir)
    assert why is not None, command
    assert f"pid {pid}" in why and "tools/land.sh" in why, why
    assert 'tools/load.py" --wait 0 -- ' in why, why


@pytest.mark.parametrize(
    "command",
    [
        'python3 "$GOH_DIR/tools/load.py" --wait 0 -- swift test',
        "swift test  # bare-ok: a package-local .build nothing else shares",
        "git status && ls",
        "./prebuilt cargo test",
    ],
)
def test_through_load_py_escaped_or_no_build_runs(held, command):
    assert _guard(command, held[0]) is None, command


def test_a_free_slot_lets_a_bare_build_run(tmp_path):
    assert _guard("swift test", tmp_path) is None


def test_a_slot_whose_owner_is_gone_lets_it_run(held):
    load_dir, _ = held
    data = reg.read(reg.registry_path(load_dir))
    gone = subprocess.Popen(["true"], stdin=subprocess.DEVNULL)
    gone.wait()
    reg.write(reg.registry_path(load_dir), {**data, "owner_pid": gone.pid, "owner_start": "x"})
    assert _guard("swift test", load_dir) is None


def test_the_build_lock_refusal_names_the_wait_line(tmp_path):
    """The agents' first refusal (exit 2) named only `--shared-lock-ok`; it names the rerun now."""
    ran = subprocess.run(
        [sys.executable, str(TOOL), "--dir", str(tmp_path), "--wait", "--wait-max", "2700", "1",
         "--", "swift", "test", "--package-path", "."],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        timeout=timing_bounds.patience(60), check=False,
        env={**os.environ, "GOH_LOAD_NCPU": "64"},
    )  # fmt: skip
    assert ran.returncode == 2, ran.stderr
    assert 'tools/load.py" --wait 0 -- swift test --package-path .' in ran.stderr, ran.stderr
