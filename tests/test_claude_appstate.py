"""An agent that leaves one of the owner's login-item apps not running has it put back (GOH.APPSTATE).

On 2026-11-07/08 a login-item app was left not running after two agents' UI tests, and
Osaurus.app (a login item serving the local model on :1337) was found not running.
`hooks/claude/agent_guard.py` now snapshots which login-item apps run at every spawn
(`hooks/claude/appstate.py`) and, at the agent's SubagentStop, names each one running before and
not after on the ledger's outcome line and in a message to the session. It never relaunches one
(GOH.APPRESTOREOWNER): the owner quits apps too, and a closed app stays closed.

No test reads or launches a real app: the login items and the process table are the module's
seams (a fake launcher stays as a tripwire that logs any launch), served here by a fake world in
files, and under goh's suite the module refuses its real defaults outright (a test proves it).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from _goh_db_fixture import ledger
from _hook_inproc import run_hook
from conftest import REPO_ROOT, hermetic_env

HOOKS = REPO_ROOT / "hooks" / "claude"
GUARD = HOOKS / "agent_guard.py"
sys.path.insert(0, str(HOOKS))
import appstate

WIDGET = "/Applications/Widget.app"
OSAURUS = "/Applications/Osaurus.app"
NOTION = "/Applications/Notion.app"

LAUNCHER = """\
#!/bin/bash
# A tripwire launcher: logs any launch and puts the app in the process table. Nothing may call it.
printf '%s\\n' "$1" >> "$WORLD/launched"
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
        # A non-app process and a look-alike path that must never count as the app.
        procs = ["/usr/libexec/logd", "/Applications/Widget.app.bak/Contents/MacOS/old"]
        procs += [f"{a}/Contents/MacOS/main" for a in apps]
        (self.root / "procs").write_text("".join(f"{p}\n" for p in procs))

    def launched(self) -> list[str]:
        path = self.root / "launched"
        return path.read_text().splitlines() if path.exists() else []

    def env(self, **extra: str) -> dict[str, str]:
        # the old launch seam stays set to the tripwire: if anything still launched, it would log
        seams = {"GOH_APPSTATE_ITEMS_CMD": f"cat {self.root / 'items'}",
                 "GOH_APPSTATE_RUNNING_CMD": f"cat {self.root / 'procs'}",
                 "GOH_APPSTATE_LAUNCH_CMD": str(self.root / "launch.sh")}  # fmt: skip
        return hermetic_env(XDG_CACHE_HOME=str(self.cache), WORLD=str(self.root),
                            GOH_DB=str(self.cache / "goh.db"), **{**seams, **extra})  # fmt: skip

    def hook(self, event: dict[str, Any], **extra: str) -> dict[str, Any] | None:
        r = run_hook(GUARD, json.dumps(event), self.env(**extra))
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
        return ledger(self.cache / "goh.db")


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path / "world", [WIDGET, OSAURUS, NOTION], [WIDGET, OSAURUS])


def test_an_app_gone_at_stop_is_reported_and_never_launched(world: World) -> None:
    """Owner 2026-11-23: "if they are not needed, don't start them if they are closed" -- the
    guard cannot tell an agent's quit from the owner's (it relaunched apps the owner had quit from
    the Dock), so a gone app is named, never started (GOH.APPRESTOREOWNER)."""
    world.spawn()
    world.set_running([OSAURUS])  # Widget gone at stop: the owner's quit or an agent's
    out = world.stop()
    assert world.launched() == []
    assert out is not None, out
    msg = out["systemMessage"]
    assert "Widget" in msg and "not relaunched" in msg and "relaunched Widget" not in msg, msg
    spawn, outcome = world.ledger()
    assert spawn["apps"] == [OSAURUS, WIDGET]
    assert outcome["apps"]["missing"] == [WIDGET]
    assert "relaunched" not in outcome["apps"] and "failed" not in outcome["apps"]


def test_an_app_still_running_and_one_never_running_are_left_alone(world: World) -> None:
    """Notion was not running at spawn: the owner's choice stands, the agent's end does not start it."""
    world.spawn()
    out = world.stop()
    assert world.launched() == []
    assert out is None
    assert world.ledger()[-1]["apps"]["missing"] == []


def test_an_unreadable_login_item_list_launches_nothing(world: World) -> None:
    """Unknown is not "nothing running": a state that cannot be read relaunches nothing."""
    world.spawn(GOH_APPSTATE_ITEMS_CMD="false")
    world.set_running([])
    assert world.stop() is None
    assert world.launched() == []
    assert world.ledger()[0]["apps"] is None


def test_the_snapshot_is_spent_by_its_stop(world: World) -> None:
    """A second stop for the same spawn has no snapshot left: it cannot report twice."""
    world.spawn()
    world.set_running([OSAURUS])
    assert world.stop() is not None
    world.set_running([OSAURUS])
    assert world.stop() is None
    assert world.launched() == []


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


def test_the_real_reader_is_system_events_and_the_process_table() -> None:
    """The defaults the module ships: the probe on macOS 27.2 (module docstring) is what they ask."""
    assert appstate.DEFAULTS["GOH_APPSTATE_ITEMS_CMD"][0] == "osascript"
    assert "path of every login item" in appstate.ITEMS_SCRIPT
    assert appstate.DEFAULTS["GOH_APPSTATE_RUNNING_CMD"] == ["ps", "-axww", "-o", "comm="]
    # nothing launches: no launch seam, no launcher in the module (GOH.APPRESTOREOWNER)
    assert "GOH_APPSTATE_LAUNCH_CMD" not in appstate.DEFAULTS
    assert not hasattr(appstate, "_launch") and not hasattr(appstate, "launch_argv")


# --- GOH.APPCLOSE: an app the owner removed is dropped; a report names the agent and the window --


def test_an_app_no_longer_a_login_item_is_neither_relaunched_nor_reported(world: World) -> None:
    """Owner 2026-11-21: "note that I removed stenoAi" -- an app taken out of the login items (or
    uninstalled) while the agent ran is the owner's removal, not something to put back."""
    world.spawn()
    (world.root / "items").write_text(f"{OSAURUS}\n{NOTION}\n")  # the owner removed Widget
    world.set_running([OSAURUS])
    assert world.stop() is None
    assert world.launched() == []
    assert world.ledger()[-1]["apps"]["missing"] == []


def test_an_uninstalled_app_is_neither_relaunched_nor_reported(world: World) -> None:
    world.spawn()
    world.set_running([OSAURUS])
    gone = world.root / "installed.sh"  # Widget is still a login item, but no longer on disk
    gone.write_text(f'#!/bin/sh\n[ "$1" != "{WIDGET}" ]\n')
    gone.chmod(0o755)
    assert world.stop(GOH_APPSTATE_INSTALLED_CMD=str(gone)) is None
    assert world.launched() == []


def test_the_report_names_the_agent_and_when_the_app_was_last_seen(world: World) -> None:
    world.spawn()
    world.set_running([OSAURUS])
    out = world.stop()
    assert out is not None
    msg = out["systemMessage"]
    assert "agent a1" in msg and "Widget last seen running " in msg and "gone by " in msg
    exits = world.ledger()[-1]["apps"]["exits"]
    assert set(exits) == {WIDGET}
    seen, gone = exits[WIDGET]["last_seen"], exits[WIDGET]["gone_by"]
    assert seen <= gone  # ISO times in UTC: the window the app exited in


def test_the_last_sighting_is_shared_across_agents(world: World) -> None:
    """Another agent's spawn saw Widget running later than this agent's: the window narrows."""
    world.spawn()
    appstate_dir = world.cache / "goh" / "appstate"
    seen = json.loads((appstate_dir / "seen.json").read_text())
    seen[WIDGET] = "2999-01-01T00:00:00+00:00"  # a later sighting by another agent
    (appstate_dir / "seen.json").write_text(json.dumps(seen))
    world.set_running([OSAURUS])
    world.stop()
    assert world.ledger()[-1]["apps"]["exits"][WIDGET]["last_seen"] == "2999-01-01T00:00:00+00:00"
