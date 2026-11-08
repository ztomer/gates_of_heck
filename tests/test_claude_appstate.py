"""An agent that leaves one of the owner's login-item apps not running has it put back (GOH.APPSTATE).

On 2026-11-07/08 Routines.app (a login item) was left not running after two agents' UI tests, and
Osaurus.app (a login item serving the local model on :1337) was found not running.
`hooks/claude/agent_guard.py` now snapshots which login-item apps run at every spawn
(`hooks/claude/appstate.py`) and, at the agent's SubagentStop, relaunches each one running before
and not after, naming it on the ledger's outcome line and in a message to the session.

No test reads or launches a real app: the login items, the process table and the launcher are the
module's three seams, served here by a fake world in files, and under goh's suite the module
refuses its real defaults outright (the last test proves it).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO_ROOT, hermetic_env

HOOKS = REPO_ROOT / "hooks" / "claude"
GUARD = HOOKS / "agent_guard.py"
sys.path.insert(0, str(HOOKS))
import appstate

ROUTINES = "/Applications/Routines.app"
OSAURUS = "/Applications/Osaurus.app"
NOTION = "/Applications/Notion.app"

LAUNCHER = """\
#!/bin/bash
# The fake launcher: logs the launch and, unless told to fail, puts the app in the process table.
printf '%s\\n' "$1" >> "$WORLD/launched"
[ -e "$WORLD/launch-fails" ] && exit 1
[ -e "$WORLD/launch-noop" ] && exit 0
printf '%s\\n' "$1/Contents/MacOS/main" >> "$WORLD/procs"
"""

PROMPT = ("Run the UI tests.\nModel-choice: sonnet@high because checkable, known-fix\n"
          "Pool: unfit because it runs the app")  # fmt: skip
SPAWN_INPUT = {"description": "ui tests", "prompt": PROMPT, "subagent_type": "repo-worker-ui",
               "model": "sonnet", "effort": "high"}  # fmt: skip


class World:
    """Login items, a process table and a launcher, all in files under `root`."""

    def __init__(self, root: Path, items: list[str], running: list[str]) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        (root / "items").write_text("".join(f"{i}\n" for i in items))
        self.set_running(running)
        launcher = root / "launch.sh"
        launcher.write_text(LAUNCHER)
        launcher.chmod(0o755)
        self.cache = root / "cache"

    def set_running(self, apps: list[str]) -> None:
        # A non-app process and a look-alike path that must never count as Routines.
        procs = ["/usr/libexec/logd", "/Applications/Routines.app.bak/Contents/MacOS/old"]
        procs += [f"{a}/Contents/MacOS/main" for a in apps]
        (self.root / "procs").write_text("".join(f"{p}\n" for p in procs))

    def launched(self) -> list[str]:
        path = self.root / "launched"
        return path.read_text().splitlines() if path.exists() else []

    def env(self, **extra: str) -> dict[str, str]:
        seams = {"GOH_APPSTATE_ITEMS_CMD": f"cat {self.root / 'items'}",
                 "GOH_APPSTATE_RUNNING_CMD": f"cat {self.root / 'procs'}",
                 "GOH_APPSTATE_LAUNCH_CMD": str(self.root / "launch.sh")}  # fmt: skip
        return hermetic_env(XDG_CACHE_HOME=str(self.cache), WORLD=str(self.root),
                            **{**seams, **extra})  # fmt: skip

    def hook(self, event: dict[str, Any], **extra: str) -> dict[str, Any] | None:
        r = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(event),
                           capture_output=True, text=True, env=self.env(**extra), timeout=60,
                           check=False)  # fmt: skip
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout) if r.stdout.strip() else None

    def spawn(self, **extra: str) -> None:
        self.hook({"session_id": "s-1", "hook_event_name": "PreToolUse", "tool_name": "Agent",
                   "tool_input": dict(SPAWN_INPUT), "tool_use_id": "toolu_A"}, **extra)  # fmt: skip

    def stop(self, **extra: str) -> dict[str, Any] | None:
        sub = self.root / "proj" / "subagents"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "agent-a1.jsonl").write_text("")
        (sub / "agent-a1.meta.json").write_text(json.dumps({"toolUseId": "toolu_A"}))
        return self.hook({"session_id": "s-1", "hook_event_name": "SubagentStop",
                          "agent_id": "a1", "agent_type": "general-purpose",
                          "last_assistant_message": "", "stop_hook_active": False,
                          "agent_transcript_path": str(sub / "agent-a1.jsonl")}, **extra)  # fmt: skip

    def ledger(self) -> list[dict[str, Any]]:
        path = self.cache / "goh" / "agent-spawns.jsonl"
        return [json.loads(ln) for ln in path.read_text().splitlines()]


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path / "world", [ROUTINES, OSAURUS, NOTION], [ROUTINES, OSAURUS])


def test_an_app_the_agent_left_not_running_is_relaunched_and_named(world: World) -> None:
    world.spawn()
    world.set_running([OSAURUS])  # the agent's UI test quit Routines and never put it back
    out = world.stop()
    assert world.launched() == [ROUTINES]
    assert out is not None and "relaunched Routines" in out["systemMessage"], out
    spawn, outcome = world.ledger()
    assert spawn["apps"] == [OSAURUS, ROUTINES]
    assert outcome["apps"]["missing"] == [ROUTINES]
    assert outcome["apps"]["relaunched"] == [ROUTINES] and outcome["apps"]["failed"] == []


def test_an_app_still_running_and_one_never_running_are_left_alone(world: World) -> None:
    """Notion was not running at spawn: the owner's choice stands, the agent's end does not start it."""
    world.spawn()
    out = world.stop()
    assert world.launched() == []
    assert out is None
    assert world.ledger()[-1]["apps"]["missing"] == []


def test_a_relaunch_that_never_shows_up_is_named_as_failed(world: World) -> None:
    """The launcher's exit status is not the effect: an app counts once it is SEEN running."""
    world.spawn()
    world.set_running([])
    (world.root / "launch-noop").write_text("")
    out = world.stop()
    assert sorted(world.launched()) == [OSAURUS, ROUTINES]
    assert out is not None and "could NOT relaunch Osaurus, Routines" in out["systemMessage"]
    assert world.ledger()[-1]["apps"]["failed"] == [OSAURUS, ROUTINES]


def test_an_unreadable_login_item_list_launches_nothing(world: World) -> None:
    """Unknown is not "nothing running": a state that cannot be read relaunches nothing."""
    world.spawn(GOH_APPSTATE_ITEMS_CMD="false")
    world.set_running([])
    assert world.stop() is None
    assert world.launched() == []
    assert world.ledger()[0]["apps"] is None


def test_the_snapshot_is_spent_by_its_stop(world: World) -> None:
    """A second stop for the same spawn has no snapshot left: it cannot relaunch twice."""
    world.spawn()
    world.set_running([OSAURUS])
    world.stop()
    world.set_running([OSAURUS])
    assert world.stop() is None
    assert world.launched() == [ROUTINES]


def test_under_the_suite_the_real_defaults_never_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """No seam, under goh's suite: nothing is read and nothing launched -- the owner's apps are
    out of every test's reach, whatever a test forgets to set."""
    for key in appstate.DEFAULTS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(appstate.SUITE_MARKER, "/nonexistent")

    def no_process(*_a: object, **_k: object) -> None:
        raise AssertionError("appstate ran a process under the suite without a seam")

    monkeypatch.setattr(appstate.subprocess, "run", no_process)
    assert not appstate.enabled()
    assert appstate.running() is None
    assert appstate.snapshot("toolu_X") is None
    assert appstate._launch(ROUTINES) is False


def test_the_real_reader_is_system_events_and_the_process_table() -> None:
    """The defaults the module ships: the probe on macOS 27.2 (module docstring) is what they ask."""
    assert appstate.DEFAULTS["GOH_APPSTATE_ITEMS_CMD"][0] == "osascript"
    assert "path of every login item" in appstate.ITEMS_SCRIPT
    assert appstate.DEFAULTS["GOH_APPSTATE_RUNNING_CMD"] == ["ps", "-axww", "-o", "comm="]
    assert appstate.DEFAULTS["GOH_APPSTATE_LAUNCH_CMD"][:1] == ["open"]
