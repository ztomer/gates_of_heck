#!/usr/bin/env python3
"""Which of the owner's login-item apps are running -- snapshotted at a subagent's spawn, restored at
its end (GOH.APPSTATE).

Agents that test an app relaunch or quit it, and leave it in the wrong state: on 2026-11-07/08
Routines.app (a login item) was left NOT running after two agents' UI tests (one SIGTERMed its own
instance, having found none running), and Osaurus.app (a login item that serves the local model on
:1337) was found not running. A note asking agents to put things back is a rule to remember; this
is the system that does it. `agent_guard.py` calls `snapshot()` at every spawn and `restore()` at
the agent's SubagentStop: a login-item app running before and not after is relaunched and named on
the ledger's outcome line and in a message to the session (tests/test_claude_appstate.py).

How the state is read, probed on macOS 27.2 (2026-11-08):
* the login items: System Events' `path of every login item` (0.2-0.3 s; the BTM records behind
  `sfltool dumpbtm` need root). A path, not a name: the name is the display name ("Cron" is
  `Notion Calendar.app`), the path is what `open -a` takes and what a process is matched by.
* running: `ps -axww -o comm=` names each process's executable by its full path, so an app is
  running when some executable sits under `<app>/Contents/MacOS/`. No Apple Events to each app.

Seams, so both directions are tested without touching the owner's apps -- each a command whose
words are split like a shell's, never run through one:
* GOH_APPSTATE_ITEMS_CMD   prints the login-item app paths, one per line
* GOH_APPSTATE_RUNNING_CMD prints running executables' paths, one per line
* GOH_APPSTATE_LAUNCH_CMD  launches one app; its path is appended as the last argument
Under goh's own suite (`GATES_OF_HECK_SUITE_REAL_HOME`) the defaults are never used: without all
three seams the module reads nothing and launches nothing, so no test can reach a real app.

It fails OPEN: anything it cannot read is `None` (unknown), never "nothing running", so an
unreadable state relaunches nothing.

CLI: `appstate.py running` prints the login-item apps running now, one per line.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SUITE_MARKER = "GATES_OF_HECK_SUITE_REAL_HOME"  # tests/suite_home.py MARKER
ITEMS_SCRIPT = (
    'tell application "System Events"\n'
    "set AppleScript's text item delimiters to linefeed\n"
    "return (path of every login item) as text\n"
    "end tell"
)
DEFAULTS = {
    "GOH_APPSTATE_ITEMS_CMD": ["osascript", "-e", ITEMS_SCRIPT],
    "GOH_APPSTATE_RUNNING_CMD": ["ps", "-axww", "-o", "comm="],
    "GOH_APPSTATE_LAUNCH_CMD": ["open", "-g", "-a"],
}
READ_TIMEOUT_S = 4.0
LAUNCH_SETTLE_S = 3.0  # how long a relaunched app has to show up in the process table
STALE_S = (
    2 * 24 * 3600
)  # a snapshot whose agent never stopped (killed session) is pruned after this


def _command(key: str) -> list[str] | None:
    """The command for one seam, or None when it must not run (the suite without its seam)."""
    raw = os.environ.get(key, "").strip()
    if raw:
        return shlex.split(raw)
    if os.environ.get(SUITE_MARKER):
        return None
    return list(DEFAULTS[key])


def enabled() -> bool:
    """False under goh's suite unless every seam is given: no default may run in a test."""
    return all(_command(k) is not None for k in DEFAULTS)


def _lines(key: str) -> list[str] | None:
    cmd = _command(key)
    if cmd is None:
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=READ_TIMEOUT_S,
                           stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def _app_path(raw: str) -> str:
    return raw.rstrip("/")


def running() -> list[str] | None:
    """The login-item apps running now, sorted; None when either side cannot be read."""
    if not enabled():
        return None
    items = _lines("GOH_APPSTATE_ITEMS_CMD")
    procs = _lines("GOH_APPSTATE_RUNNING_CMD")
    if items is None or procs is None:
        return None
    apps = {_app_path(i) for i in items if _app_path(i).endswith(".app")}
    return sorted(a for a in apps if any(p.startswith(f"{a}/Contents/MacOS/") for p in procs))


def _state_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "goh" / "appstate"


def _state_file(key: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    return _state_dir() / f"{safe}.json"


def snapshot(key: str | None) -> list[str] | None:
    """Record which login-item apps run now, under the spawn's `key`; what was recorded, or None."""
    if not key:
        return None
    apps = running()
    if apps is None:
        return None
    try:
        d = _state_dir()
        d.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for old in d.glob("*.json"):
            try:
                if now - old.stat().st_mtime > STALE_S:
                    old.unlink()
            except OSError:
                pass
        _state_file(key).write_text(json.dumps({"apps": apps}), encoding="utf-8")
    except OSError:
        return None
    return apps


def _launch(app: str) -> bool:
    cmd = _command("GOH_APPSTATE_LAUNCH_CMD")
    if cmd is None:
        return False
    try:
        r = subprocess.run([*cmd, app], capture_output=True, text=True, timeout=READ_TIMEOUT_S,
                           stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def restore(key: str | None, settle_s: float = LAUNCH_SETTLE_S) -> dict[str, Any] | None:
    """Relaunch every login-item app the spawn under `key` saw running and that is not running now.

    `{"before": [...], "missing": [...], "relaunched": [...], "failed": [...]}`, or None when there
    was no snapshot or the state cannot be read. An app counts as relaunched only once it is SEEN
    running again (the launcher's exit status is not the effect)."""
    if not key:
        return None
    path = _state_file(key)
    try:
        before = json.loads(path.read_text(encoding="utf-8"))["apps"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        path.unlink()
    except OSError:
        pass
    now = running()
    if now is None or not isinstance(before, list):
        return None
    missing = sorted(set(before) - set(now))
    launched = [a for a in missing if _launch(a)]
    up: set[str] = set()
    end = time.monotonic() + settle_s
    while launched:
        seen = running()
        up = set(launched) & set(seen or [])
        if up == set(launched) or time.monotonic() >= end:
            break
        time.sleep(0.2)  # the next pass re-reads the process table: a poll, not a guess
    relaunched = sorted(up)
    return {"before": before, "missing": missing, "relaunched": relaunched,
            "failed": sorted(set(missing) - up)}  # fmt: skip


def describe(result: dict[str, Any] | None) -> str | None:
    """The message for the session, or None when nothing was missing."""
    if not result or not result["missing"]:
        return None
    parts = []
    if result["relaunched"]:
        parts.append(f"relaunched {', '.join(Path(a).stem for a in result['relaunched'])}")
    if result["failed"]:
        parts.append(f"could NOT relaunch {', '.join(Path(a).stem for a in result['failed'])}")
    return (f"agent_guard: login-item app(s) running when the agent was spawned were not running "
            f"when it stopped -- {'; '.join(parts)} (GOH.APPSTATE).")  # fmt: skip


def main(argv: list[str]) -> int:
    if argv[1:] != ["running"]:
        print("usage: appstate.py running", file=sys.stderr)
        return 2
    apps = running()
    if apps is None:
        print("appstate: the login items or the process table could not be read", file=sys.stderr)
        return 1
    for a in apps:
        print(a)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
