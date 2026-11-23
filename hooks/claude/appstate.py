#!/usr/bin/env python3
"""Which of the owner's login-item apps are running -- snapshotted at a subagent's spawn, compared
at its end (GOH.APPSTATE), and REPORTED, never relaunched (GOH.APPRESTOREOWNER).

Agents that test an app relaunch or quit it, and leave it in the wrong state: on 2026-11-07/08
a login-item app was left NOT running after two agents' UI tests (one SIGTERMed its own
instance, having found none running), and Osaurus.app (a login item that serves the local model on
:1337) was found not running. A note asking agents to put things back is a rule to remember; this
is the system that sees it. `agent_guard.py` calls `snapshot()` at every spawn and `gone()` at
the agent's SubagentStop: a login-item app running before and not after is named on the ledger's
outcome line and in a message to the session (tests/test_claude_appstate.py).

It never starts one. The guard cannot tell an agent's quit from the owner's, and on 2026-11-23 it
relaunched apps the owner had just quit from the Dock to free the machine (owner: "if they are not
needed, don't start them if they are closed"). A lane whose work needs an app starts it and quits
what it started; this module only says what went missing, so the cause is fixed where it happened.

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
* GOH_APPSTATE_INSTALLED_CMD exits 0 when the app (its path appended) is installed; default
  `test -d`; under the suite without it every app counts as installed
Under goh's own suite (`GATES_OF_HECK_SUITE_REAL_HOME`) the defaults are never used: without both
reading seams the module reads nothing, so no test can reach a real app.

An app the OWNER removed while the agent ran -- no longer a login item, or no longer installed --
is not reported (GOH.APPCLOSE; owner 2026-11-21: "note that I removed
stenoAi"). Every reading (any agent's spawn or stop) records when each login-item app was last
seen running (`seen.json`), so a report names the agent and the window the app exited in: last
seen running at T1, gone by T2.

It fails OPEN: anything it cannot read is `None` (unknown), never "nothing running", so an
unreadable state reports nothing.

CLI: `appstate.py running` prints the login-item apps running now, one per line.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tui.lib import noun  # the count meets its noun (GOH.HEDGEOWN)

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
}
READ_TIMEOUT_S = 4.0
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


def _read() -> tuple[list[str], list[str]] | None:
    """(the login-item apps, those running now), both sorted; None when either side is unreadable.
    What runs is recorded as seen now, for every agent's window (`seen.json`)."""
    if not enabled():
        return None
    items = _lines("GOH_APPSTATE_ITEMS_CMD")
    procs = _lines("GOH_APPSTATE_RUNNING_CMD")
    if items is None or procs is None:
        return None
    apps = {_app_path(i) for i in items if _app_path(i).endswith(".app")}
    up = sorted(a for a in apps if any(p.startswith(f"{a}/Contents/MacOS/") for p in procs))
    _saw(up)
    return sorted(apps), up


def running() -> list[str] | None:
    """The login-item apps running now, sorted; None when either side cannot be read."""
    state = _read()
    return state[1] if state else None


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _seen() -> dict[str, str]:
    try:
        d = json.loads((_state_dir() / "seen.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}


def _saw(apps: list[str]) -> None:
    """Record APPS as seen running now, keeping a later sighting another agent recorded."""
    seen, now = _seen(), _now_iso()
    seen.update({a: max(now, seen.get(a, "")) for a in apps})
    with contextlib.suppress(OSError):
        _state_dir().mkdir(parents=True, exist_ok=True)
        tmp = _state_dir() / f"seen.json.{os.getpid()}"
        tmp.write_text(json.dumps(seen, sort_keys=True), encoding="utf-8")
        tmp.replace(_state_dir() / "seen.json")


def _installed(app: str) -> bool:
    raw = os.environ.get("GOH_APPSTATE_INSTALLED_CMD", "").strip()
    if not raw:
        return True if os.environ.get(SUITE_MARKER) else os.path.isdir(app)
    try:
        r = subprocess.run([*shlex.split(raw), app], capture_output=True, timeout=READ_TIMEOUT_S,
                           stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return True  # unknown is not "removed": the app is still put back
    return r.returncode == 0


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


def gone(key: str | None) -> dict[str, Any] | None:
    """Every login-item app the spawn under `key` saw running that is not running now. Never
    launches one (GOH.APPRESTOREOWNER).

    `{"before": [...], "missing": [...], "exits": {app: {"last_seen", "gone_by"}}}`, or None when
    there was no snapshot or the state cannot be read."""
    if not key:
        return None
    path = _state_file(key)
    try:
        before = json.loads(path.read_text(encoding="utf-8"))["apps"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    with contextlib.suppress(OSError):
        path.unlink()
    seen = _seen()  # read before this reading records its own sightings
    state = _read()
    if state is None or not isinstance(before, list):
        return None
    items, now = state
    gone_by = _now_iso()
    # an app the owner took out of the login items, or uninstalled, is the owner's removal
    missing = sorted(a for a in set(before) - set(now) if a in items and _installed(a))
    exits = {a: {"last_seen": seen.get(a), "gone_by": gone_by} for a in missing}
    return {"before": before, "missing": missing, "exits": exits}


def describe(result: dict[str, Any] | None, agent: str | None = None) -> str | None:
    """The message for the session, or None when nothing was missing."""
    if not result or not result["missing"]:
        return None
    parts = []
    for app, w in sorted((result.get("exits") or {}).items()):
        parts.append(f"{Path(app).stem} last seen running {w.get('last_seen') or 'never'}, "
                     f"gone by {w.get('gone_by')}")  # fmt: skip
    apps = noun(len(result["missing"]), "app")
    who = f"agent {agent}" if agent else "the agent"
    return (f"agent_guard: login-item {apps} running when {who} was spawned were not running "
            f"when it stopped -- {'; '.join(parts)}; not relaunched: a closed app stays closed, "
            f"and a lane that quit one it needs starts it (GOH.APPRESTOREOWNER).")  # fmt: skip


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
