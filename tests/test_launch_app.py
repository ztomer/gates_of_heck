"""`goh launch-app` gives the app launchd's LOGIN environment, never the caller's (GOH.CLEANOPEN).

The probe that chose the mechanism, kept as the test: a throwaway `.app` whose executable writes its
own environ to a file, launched with a sentinel set in the caller. A bare `open` hands the app the
sentinel (the control arm: the instrument can see a leak); `goh launch-app` must not. A ship's
`open -a` once started ZoneWM with the ship's GOH_DIR, and every app ZoneWM relaunched kept it.
"""

from __future__ import annotations

import os
import pwd
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import hermetic_env, native_goh_path
from env_probe import probe_sh, read_probe
from timing_bounds import GiveUp

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="LaunchServices' open is macOS")

SENTINEL = "GOH_CLEANOPEN_SENTINEL"
PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>envprobe</string>
<key>CFBundleIdentifier</key><string>local.goh.test.envprobe.{tag}</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>LSUIElement</key><true/>
</dict></plist>
"""


def _probe_app(root: Path, tag: str) -> tuple[Path, Path]:
    """An app that writes its environ's names (tests/env_probe.py) to `<root>/<tag>.env`, exits."""
    app = root / f"EnvProbe{tag}.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text(PLIST.format(tag=tag))
    out = root / f"{tag}.env"
    exe = app / "Contents" / "MacOS" / "envprobe"
    # Names, plus the two values asserted on (GOH.ENVDUMP): the app's environ is a login one.
    exe.write_text(f"#!/bin/sh\n{probe_sh(out, keep=(SENTINEL, 'HOME'))}\n")
    exe.chmod(0o755)
    return app, out


def _environ_of(out: Path) -> dict[str, str]:
    give_up = GiveUp(20)  # a failure is "never": stretched by the load at every check
    while not out.exists():
        assert not give_up.passed(), f"the probe app never wrote {out}"
        time.sleep(0.2)
    return read_probe(out)


def _caller_env(**extra: str) -> dict[str, str]:
    return hermetic_env(**{SENTINEL: "caller", "GOH_DIR": "/nonexistent/goh/head/abc", **extra})


def test_a_bare_open_hands_the_app_the_callers_environment(tmp_path: Path) -> None:
    """The control: without it, an absent sentinel below could mean the probe sees nothing."""
    app, out = _probe_app(tmp_path, "bare")
    subprocess.run(["/usr/bin/open", "-g", str(app)], env=_caller_env(), check=True,
                   stdin=subprocess.DEVNULL, timeout=60)  # fmt: skip
    seen = _environ_of(out)
    carried = seen.get(SENTINEL) == "caller"
    assert carried, sorted(seen)  # names only, never values


def test_launch_app_gives_the_login_environment(tmp_path: Path) -> None:
    app, out = _probe_app(tmp_path, "clean")
    r = subprocess.run([str(native_goh_path()), "launch-app", "--background", str(app)],
                       env=_caller_env(), capture_output=True, text=True, check=False,
                       stdin=subprocess.DEVNULL, timeout=60)  # fmt: skip
    assert r.returncode == 0, r.stderr
    seen = _environ_of(out)
    # Booleans and names only: pytest prints an asserted dict whole, and the caller's environ
    # holds its secrets.
    names = sorted(seen)
    leaked, goh_dir = SENTINEL in seen, "GOH_DIR" in seen
    login_home = seen.get("HOME") == pwd.getpwuid(os.getuid()).pw_dir
    assert not leaked, f"the caller's environment reached the app: {names}"
    assert not goh_dir, names
    # launchd's own set, not an empty one: the login HOME and a PATH.
    assert login_home and "PATH" in names, names


def test_launch_app_refuses_a_missing_bundle(tmp_path: Path) -> None:
    r = subprocess.run([str(native_goh_path()), "launch-app", str(tmp_path / "No.app")],
                       env=_caller_env(), capture_output=True, text=True, check=False,
                       stdin=subprocess.DEVNULL, timeout=60)  # fmt: skip
    assert r.returncode == 2 and "no such app" in r.stderr, r.stderr
