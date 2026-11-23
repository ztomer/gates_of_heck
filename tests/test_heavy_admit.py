"""A heavy job from ANY session comes in by the one admission (GOH.ADMISSION, lib/heavy_admit.py).

2026-11-22: a shellcheck burst from another session drove the load to 80-113 on 16 cores and failed
the v0.150.0 ship on a load flake. The gates came in by a gate slot (lib/admission.py), but a
session's own cargo build, swift test or shellcheck sweep ran at once. The Bash guard
(hooks/claude/discard_guard.py) now rewrites such a command to take a gate slot first, its holder
record naming the session and the command, so a run that waits names who held the machine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from _hook_inproc import run_hook
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import GiveUp, patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import heavy_admit

GUARD = REPO_ROOT / "hooks" / "claude" / "discard_guard.py"
TAKE = REPO_ROOT / "lib" / "heavy_admit.py"


@pytest.mark.parametrize(
    ("command", "family"),
    [
        ("cargo build --release", "cargo"),
        ("cd crates/x && RUSTFLAGS=-Dwarnings cargo clippy -p x", "cargo"),
        ("nice -n 5 swift test", "swift"),
        ("xcodebuild -scheme App build", "xcodebuild"),
        ("shellcheck gates/*.sh", "shellcheck"),
        ("git ls-files '*.sh' | xargs shellcheck", "shellcheck"),
        ("ls -la", None),
        ("shellcheck one.sh", None),  # one file: no burst
        ("cargo fmt --check", None),
        ("python3 tools/load.py 0 -- cargo test", None),  # already admitted
        ("echo 'cargo build'", None),
        ("bash tools/gate.sh --full", None),  # a gate admits itself
        ("git commit -q -F - <<'EOF'\nfix: cargo build\nEOF", None),  # a heredoc body is text
        ("cargo test  # bare-ok: a package-local target", None),
    ],
)
def test_the_heavy_classes(command, family):
    assert heavy_admit.heavy(command) == family


def test_the_guard_rewrites_a_heavy_command_to_take_admission_first(tmp_path):
    event = {"tool_name": "Bash", "session_id": "abcdef0123456789", "cwd": str(tmp_path),
             "tool_input": {"command": "cargo build -p x", "timeout": 600000}}  # fmt: skip
    r = run_hook(GUARD, json.dumps(event), hermetic_env(drop_git=True))
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)["hookSpecificOutput"]
    new = out["updatedInput"]
    assert new["timeout"] == 600000 and "permissionDecision" not in out, out
    assert new["command"].endswith("\ncargo build -p x"), new["command"]
    assert "heavy_admit.py" in new["command"] and "abcdef01" in new["command"], new["command"]
    light = {**event, "tool_input": {"command": "ls"}}
    quiet = run_hook(GUARD, json.dumps(light), hermetic_env(drop_git=True)).stdout
    assert not quiet.strip()


def _env(tmp_path: Path) -> dict[str, str]:
    mem = tmp_path / "mem"
    mem.write_text("avail_pct=80 swap_used_mb=0 mem_total_mb=65536\n")
    return {**hermetic_env(), "GOH_GATE_SLOTS": "1", "GOH_GATE_SLOT_DIR": str(tmp_path / "slots"),
            "GOH_MEM_READING": str(mem)}  # fmt: skip


def _session(env: dict[str, str], label: str, then: str, wait: float) -> subprocess.Popen:
    """One fake session's Bash tool call: the guard's prefix, then the command."""
    prefix = heavy_admit.prefix(label, wait, py=sys.executable, take=str(TAKE))
    return subprocess.Popen(["bash", "-c", f"{prefix}\n{then}"], env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL)  # fmt: skip


@pytest.mark.load_sensitive  # two sessions, a holder each, bounded by patience
def test_a_second_sessions_heavy_job_waits_and_names_who_holds_the_machine(tmp_path):
    env = _env(tmp_path)
    go = tmp_path / "go"
    a = _session(env, "session aaaa1111: cargo build", f"touch {tmp_path}/a.ran; "
                 f"while [ ! -e {go} ]; do sleep 0.1; done", patience(30))  # fmt: skip
    give_up = GiveUp(30)
    while not (tmp_path / "a.ran").exists():
        assert not give_up.passed() and a.poll() is None, a.communicate()
        time.sleep(0.05)
    b = _session(env, "session bbbb2222: shellcheck", f"touch {tmp_path}/b.ran", patience(30))
    said = b.stderr.readline()  # the child says it waits, and before whom (bounded: max-wait)
    assert "waiting for a gate slot" in said, said
    assert "session aaaa1111: cargo build" in said, said  # who held the machine, named
    assert not (tmp_path / "b.ran").exists(), "the second session's job ran at once"
    go.touch()
    _, a_err = a.communicate(timeout=patience(30))
    _, b_err = b.communicate(timeout=patience(30))
    assert (tmp_path / "b.ran").exists() and b.returncode == 0, b_err
    assert a.returncode == 0, a_err


@pytest.mark.load_sensitive  # a holder, bounded by patience
def test_a_wait_past_its_bound_runs_anyway_and_says_beside_whom(tmp_path):
    env = _env(tmp_path)
    go = tmp_path / "go"
    a = _session(env, "session aaaa1111: cargo test", f"touch {tmp_path}/a.ran; "
                 f"while [ ! -e {go} ]; do sleep 0.1; done", patience(30))  # fmt: skip
    give_up = GiveUp(30)
    while not (tmp_path / "a.ran").exists():
        assert not give_up.passed() and a.poll() is None, a.communicate()
        time.sleep(0.05)
    b = _session(env, "session bbbb2222: cargo build", f"touch {tmp_path}/b.ran", 1)
    _, b_err = b.communicate(timeout=patience(30))
    go.touch()
    a.communicate(timeout=patience(30))
    assert (tmp_path / "b.ran").exists() and b.returncode == 0, b_err
    assert "running anyway" in b_err and "session aaaa1111: cargo test" in b_err, b_err


@pytest.mark.load_sensitive  # a session subprocess, bounded by patience
def test_an_escape_or_slots_off_takes_nothing(tmp_path):
    assert heavy_admit.heavy("cargo build # admit-ok: a one-off probe") is None
    env = {**_env(tmp_path), "GOH_GATE_SLOTS": "0"}
    s = _session(env, "session cccc: cargo build", "echo ran", 5)
    out, err = s.communicate(timeout=patience(30))
    assert out.strip() == "ran" and s.returncode == 0, err
    assert not os.path.exists(tmp_path / "slots") or not os.listdir(tmp_path / "slots")
