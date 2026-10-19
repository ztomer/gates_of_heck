"""A consumer's `tools/gate.sh --full` holds the gate slot whatever its own copy says (v0.39.0).

v0.37.0 put the slot in every full-gate entry point goh ships -- local_ci.sh, round.sh, the starter
gate.sh. The ~15 consumer copies written before it (Finance, ZoneWM, divoom-control, games, servers,
...) run `"$GOH/gates/structural.sh" "$@"` and then their own heavy layers, and never exec
local_ci.sh: hand-run, they took no slot and raced every push on the machine. install.sh never
overwrites a gate.sh, so the fix lives in the one goh file they all already call: structural.sh
--full, run from a `tools/gate.sh`, takes the slot ON BEHALF OF that gate.sh -- a holder that lives
until the gate.sh process ends, not until structural.sh does.

The class pinned here: a full gate whose copy predates the slot still waits its turn, while
  * `--staged` (the commit hook) takes none,
  * a gate nested in one already holding a slot (push gate -> gate.sh, gate.sh -> local_ci.sh)
    takes no second one,
  * a daemon the gate leaves behind does not keep the slot.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from _fast_git import fast_init
from test_gate_slot import LOCAL_CI, Gate, _env
from test_push_gate import _git

# The pre-v0.37 consumer shape: structural first, then a layer of its own, no slot code anywhere.
OLD_GATE = """#!/usr/bin/env bash
set -euo pipefail
GOH="${{GOH_DIR:-$HOME/Projects/gates_of_heck}}"
"$GOH/gates/structural.sh" "$@"
case "${{1:-}}" in
  --full)
    echo "LAYER-START $$"
    {layer}
    echo "LAYER-END"
    ;;
esac
"""
WAIT_RELEASE = 'while [ ! -f "{release}" ]; do sleep 0.05; done'


def _consumer(tmp_path: Path, name: str, layer: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    fast_init(repo, "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "tools").mkdir()
    gate = repo / "tools" / "gate.sh"
    gate.write_text(OLD_GATE.format(layer=layer))
    gate.chmod(0o755)
    (repo / "marker.txt").write_text("x\n")
    (repo / ".gatesrc").write_text("")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    return repo


class Consumer(Gate):
    """`tools/gate.sh <scope>` in a consumer repo, through the Gate harness."""

    def __init__(
        self,
        tmp_path: Path,
        name: str,
        env: dict[str, str],
        scope: str = "--full",
        layer: str | None = None,
    ) -> None:
        self.name = name
        self.release = tmp_path / f"release-{name}"
        self.log = tmp_path / f"{name}.out"
        repo = _consumer(tmp_path, name, layer or WAIT_RELEASE.format(release=self.release))
        with open(self.log, "w") as out:
            self.proc = subprocess.Popen(
                ["bash", "tools/gate.sh", scope],
                cwd=repo,
                stdout=out,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
            )


def _cenv(tmp_path: Path, goh: Path, slots: int = 1) -> dict[str, str]:
    return _env(tmp_path, slots, GOH_BIN=str(goh))


def test_two_old_consumer_full_gates_on_one_slot_run_their_layers_one_at_a_time(tmp_path, goh):
    env = _cenv(tmp_path, goh)
    first = Consumer(tmp_path, "first", env)
    second = None
    try:
        first.wait_for("LAYER-START")
        assert "gate slot 1/1 taken" in first.out(), first.out()
        second = Consumer(tmp_path, "second", env)
        second.wait_for("all 1 gate slots are held")
        time.sleep(0.5)
        assert "LAYER-START" not in second.out(), "a full gate ran its layer beside another's"
        assert first.finish() == 0, first.out()
        assert second.finish() == 0, second.out()
        assert "taken after" in second.out(), second.out()
    finally:
        first.kill()
        if second is not None:
            second.kill()


def test_a_staged_consumer_gate_takes_no_slot(tmp_path, goh):
    env = _cenv(tmp_path, goh)
    holder = Gate(tmp_path, "holder", env)
    try:
        holder.wait_for("TAKEN")
        staged = Consumer(tmp_path, "staged", env, scope="--staged")
        assert staged.proc.wait(timeout=60) == 0, staged.out()
        assert "gate slot" not in staged.out(), staged.out()
    finally:
        holder.kill()


def test_the_slot_ends_with_the_gate_not_with_a_daemon_it_left(tmp_path, goh):
    env = _cenv(tmp_path, goh)
    daemon = tmp_path / "daemon.pid"
    layer = f'nohup sleep 120 >/dev/null 2>&1 & echo $! > "{daemon}"'
    gate = Consumer(tmp_path, "daemon", env, layer=layer)
    try:
        assert gate.proc.wait(timeout=60) == 0, gate.out()
        assert "gate slot 1/1 taken" in gate.out(), gate.out()
        after = Gate(tmp_path, "after", env, body="true")
        try:
            after.wait_for("DONE", seconds=20)
            assert "all 1 gate slots are held" not in after.out(), after.out()
        finally:
            after.kill()
    finally:
        try:
            os.kill(int(daemon.read_text()), signal.SIGKILL)
        except (OSError, ValueError):
            pass
        gate.kill()


def test_a_consumer_gate_running_local_ci_after_structural_takes_one_slot(tmp_path, goh):
    env = _cenv(tmp_path, goh)
    layer = f'bash "{LOCAL_CI}" --steps-only --step "echo STEP-RAN" .'
    gate = Consumer(tmp_path, "nested", env, layer=layer)
    try:
        assert gate.proc.wait(timeout=60) == 0, gate.out()
        text = gate.out()
        assert "STEP-RAN" in text, text
        assert text.count("gate slot 1/1 taken") == 1, text
        assert "inside a gate that holds slot-0" in text, text
    finally:
        gate.kill()


def test_structural_full_outside_a_gate_sh_takes_no_slot(tmp_path, goh):
    """A bare `structural.sh --full` (a terminal, a script step) is no gate.sh to hold one for."""
    env = _cenv(tmp_path, goh)
    repo = _consumer(tmp_path, "bare", "true")
    holder = Gate(tmp_path, "holder", env)
    try:
        holder.wait_for("TAKEN")
        r = subprocess.run(
            ["bash", "-c", f'bash "{env["GOH_DIR"]}/gates/structural.sh" --full; true'],
            check=False,
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "gate slot" not in r.stdout + r.stderr
    finally:
        holder.kill()
