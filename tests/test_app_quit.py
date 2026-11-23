"""GOH.APPCLOSE: an agent's Apple Event quit of an owner's login-item app is refused at the source.

The shape on record (EST.DOCKHIDE's agent, 2026-11-15): `osascript -e 'tell application id
"com.ztomer.routines.menubar" to quit'`, twice, on Routines, a never-quit app. The login items are
`appstate`'s seam, served by a file; the apps are directories with an Info.plist.
"""

from __future__ import annotations

import json
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

GUARD = REPO_ROOT / "hooks" / "claude" / "discard_guard.py"


@pytest.fixture
def items(tmp_path: Path) -> tuple[Path, Path]:
    """Two login-item apps (Routines, by its real bundle id; Widget), and one app that is none."""
    apps = {"Routines": "com.ztomer.routines.menubar", "Widget": "com.example.widget",
            "MyBuild": "com.example.mybuild"}  # fmt: skip
    for name, bid in apps.items():
        plist = tmp_path / f"{name}.app" / "Contents" / "Info.plist"
        plist.parent.mkdir(parents=True)
        plist.write_bytes(plistlib.dumps({"CFBundleIdentifier": bid}))
    listing = tmp_path / "items"
    listing.write_text("".join(f"{tmp_path / n}.app\n" for n in ("Routines", "Widget")))
    return tmp_path, listing


def _verdict(listing: Path, command: str) -> str | None:
    seams = {"GOH_APPSTATE_ITEMS_CMD": f"cat {listing}", "GOH_APPSTATE_RUNNING_CMD": "true"}
    event = {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Bash",
             "tool_input": {"command": command}, "cwd": "/"}  # fmt: skip
    r = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(event), capture_output=True,
                       text=True, timeout=patience(30), check=False,
                       env=hermetic_env(**seams))  # fmt: skip
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else {}
    return out.get("permissionDecisionReason")


@pytest.mark.parametrize("command", [
    """osascript -e 'tell application id "com.ztomer.routines.menubar" to quit'""",
    """osascript -e 'tell application "Routines" to quit'; sleep 2; open -a Routines""",
    """osascript -e 'quit app "widget"'""",
])  # fmt: skip
def test_a_quit_of_a_login_item_app_is_refused(items: tuple[Path, Path], command: str) -> None:
    _, listing = items
    why = _verdict(listing, command)
    assert why is not None and "GOH.APPCLOSE" in why and "login item" in why


@pytest.mark.parametrize("command", [
    """osascript -e 'tell application "MyBuild" to quit'""",
    """osascript -e 'tell application "Routines" to activate'""",
    """osascript -e 'tell application "Routines" to quit' # quit-ok: owner asked for a restart""",
    """echo quit app Routines""",
])  # fmt: skip
def test_other_apps_other_verbs_and_the_escape_pass(items: tuple[Path, Path], command: str) -> None:
    _, listing = items
    assert _verdict(listing, command) is None


def test_an_unreadable_login_item_list_refuses_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-list"
    assert _verdict(missing, """osascript -e 'tell application "Routines" to quit'""") is None
