"""A shell command that QUITS one of the owner's login-item apps is refused (GOH.APPCLOSE).

THE CAUSE (2026-11-15). agent_guard (GOH.APPSTATE) kept finding login-item apps gone at an
agent's stop. One shape an agent typed is on record: EST.DOCKHIDE's agent quit Routines (on the
never-quit list) twice with `osascript -e 'tell application id "com.ztomer.routines.menubar" to
quit'` to restart it under a test appearance. A kill by name (`pkill`, `killall`) is already
refused by `lib/name_kill.py`; an Apple Event quit was not. (The 2026-11-23 cases were not agents:
the unified log shows `DockHelper ... perform action for menu item` and then `Handling Quit
AppleEvent` in each app, the owner quitting from the Dock with Activity Monitor in front.)

THE RULE. An `osascript` command that sends `quit` to an app (`tell application "X" to quit`,
`tell application id "B" to quit`, `quit app "X"`) is refused when X is a login-item app's name
or B its bundle id (`appstate`'s login-item seam; the id from the app's Info.plist). An app that
is no login item -- the agent's own build, a test fixture -- quits as before. The escape is
`# quit-ok: <reason>` on the command. An unreadable login-item list refuses nothing (fail open).
Called by `hooks/claude/discard_guard.py`; pinned by tests/test_app_quit.py.

a `discard_guard.py` rule (GOH.APPCLOSE): an `osascript` quit of a login-item app (by name or
bundle id) is refused; `# quit-ok: <reason>` escapes. Pinned by `test_app_quit.py`.

`hooks/claude/discard_guard.py`: a Claude Code PreToolUse hook (matcher
`Bash|Monitor|Write|Edit|MultiEdit`): refuses a git command that would discard uncommitted work (`#
discard-ok: <reason>` is the escape) and a command OR A FILE that runs a backgrounded busy loop or
a busy-wait (`# load-ok: <reason>` is the escape, in the command or in the file). The file half is
the 2026-10-21 incident: twenty-four orphaned spinners, four cores each, and the loop arrived in a
hand-written script the Bash-only guard could not see. The shape is the house's ONE definition
(`lib/busyloop.py`), shared with the check and the backstop. It also refuses a `tools/load.py`
command line whose wrapped command runs at background priority (G1f, `lib/load_priority.py`, the
definition the tool shares) or takes a machine-wide build lock (`lib/load_locks.py`), and a wait
loop whose `pgrep -f` / `ps | grep` condition matches its own shell and so never ends (`# pgrep-ok:
<reason>` is the escape, `lib/self_match.py`). Pinned by `test_claude_discard_guard.py`,
`test_claude_load_guard.py`, `test_load_priority.py`, `test_load_locks.py`,
`test_self_match_wait.py`.
"""

from __future__ import annotations

import plistlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # its siblings, whoever imports it
import appstate  # the login-item seam (GOH.APPSTATE)

ESCAPE = re.compile(r"#\s*quit-ok:\s*\S")
QUIT = re.compile(r"\bquit\b")
TARGET = re.compile(r"\b(?:application|app)\s+(id\s+)?\\?[\"']([^\"'\\]+)\\?[\"']")


def _bundle_id(app: str) -> str | None:
    try:
        with open(Path(app) / "Contents" / "Info.plist", "rb") as fh:
            value = plistlib.load(fh).get("CFBundleIdentifier")
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    return str(value) if value else None


def targets(command: str) -> list[tuple[bool, str]]:
    """(by bundle id?, name or id) of every app an osascript quit in COMMAND addresses."""
    if "osascript" not in command or not QUIT.search(command):
        return []
    return [(bool(by_id), name.strip()) for by_id, name in TARGET.findall(command)]


def judge(command: str) -> str | None:
    """The refusal for a quit of an owner's login-item app, or None."""
    wanted = targets(command)
    if not wanted or ESCAPE.search(command):
        return None
    items = appstate._lines("GOH_APPSTATE_ITEMS_CMD") if appstate.enabled() else None
    if not items:
        return None
    hit = []
    for raw in items:
        app = appstate._app_path(raw)
        if not app.endswith(".app"):
            continue
        for by_id, name in wanted:
            if (by_id and name == _bundle_id(app)) or (
                not by_id and name.lower() == Path(app).stem.lower()
            ):
                hit.append(Path(app).stem)
    if not hit:
        return None
    names = ", ".join(sorted(set(hit)))
    return (
        f"refused: this quits {names}, a login item of the owner's (GOH.APPCLOSE). Agents leave "
        "the owner's apps as they found them, and some (the routines menu bar, the window "
        "manager, the token-pool services) are never quit. Test against your own build or a "
        "copy that is no login item; if this app really must restart, say so in your report "
        "for the coordinator. A quit the owner asked for carries `# quit-ok: <reason>`."
    )
