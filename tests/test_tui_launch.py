"""`tui.launch`: the one way a Python caller launches an app (GOH.LAUNCHAPPID).

Before it, each Python caller built `$GOH_DIR/bin/goh launch-app ...` by hand (goh's own appstate
hook, two ZeroThunder tools, scripts' doom.py) or ran `env -i open` itself (divoom-control). The
helper resolves the `goh` of the checkout it is imported from and spells every flag once.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT

from tui import launch


def test_the_command_is_this_checkouts_goh_launch_app() -> None:
    cmd = launch.launch_argv("/Applications/X.app", background=True)
    goh = str(REPO_ROOT / "bin" / "goh")
    assert cmd == [goh, "launch-app", "--background", "/Applications/X.app"]


def test_every_flag_and_the_apps_own_arguments() -> None:
    cmd = launch.launch_argv(bundle_id="com.example.App", new=True, wait=True, args=["-e", "x"])
    assert cmd[1:] == ["launch-app", "--new", "--wait", "--bundle-id", "com.example.App",
                       "--", "-e", "x"]  # fmt: skip


def test_a_prefix_takes_the_target_last() -> None:
    """No target and no id: the prefix a caller appends its target to (appstate's launcher)."""
    assert launch.launch_argv(background=True)[1:] == ["launch-app", "--background"]


def test_a_target_and_a_bundle_id_are_refused_together() -> None:
    with pytest.raises(ValueError):
        launch.launch_argv("X.app", bundle_id="com.example.App")


def test_launch_app_runs_goh_with_the_callers_stdin_closed(tmp_path: Path, monkeypatch) -> None:
    """The run itself, through a fake goh that records its argv and whether stdin was readable."""
    log = tmp_path / "argv"
    fake = tmp_path / "goh"
    fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {log}\n'
                    f"read -r x && echo stdin >> {log}\nexit 3\n")  # fmt: skip
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(launch, "GOH", fake)
    r = launch.launch_app(bundle_id="com.example.App", background=True)
    assert isinstance(r, subprocess.CompletedProcess) and r.returncode == 3
    assert log.read_text().split("\n")[:-1] == ["launch-app", "--background", "--bundle-id",
                                                "com.example.App"]  # fmt: skip


def test_goh_python_callers_launch_through_the_helper() -> None:
    """No Python file of goh's builds `bin/goh launch-app` by hand: one spelling, here."""
    by_hand = []
    for path in [*(REPO_ROOT / "hooks").rglob("*.py"), *(REPO_ROOT / "tools").rglob("*.py")]:
        text = path.read_text(encoding="utf-8", errors="replace")
        if '"launch-app"' in text or "'launch-app'" in text:
            by_hand.append(str(path.relative_to(REPO_ROOT)))
    assert by_hand == []
