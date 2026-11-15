"""tui/launch.py -- launch an app with launchd's LOGIN environment, from Python (GOH.LAUNCHAPPID).

The one way a Python caller starts an app: `goh launch-app` clears the caller's environment before
`open` (GOH.CLEANOPEN, crates/goh/src/launchapp.rs), and this module spells that command once, so
no caller builds `$GOH_DIR/bin/goh launch-app ...` by hand or runs `env -i open` itself.

    from tui.launch import launch_app, launch_argv
    launch_app("/Applications/Ghostty.app", background=True)
    launch_app(bundle_id="com.google.GeminiMacOS")

The `goh` run is the one of the checkout this module is imported from (`PYTHONPATH=$GOH_DIR`).
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from os import PathLike
from pathlib import Path

GOH = Path(__file__).resolve().parents[1] / "bin" / "goh"


def launch_argv(
    target: str | PathLike[str] | None = None,
    *,
    bundle_id: str | None = None,
    background: bool = False,
    new: bool = False,
    wait: bool = False,
    args: Sequence[str] = (),
) -> list[str]:
    """`goh launch-app` with these flags. `target` is a `.app`, an app NAME or a document;
    `bundle_id` is `open -b`; neither gives the prefix a caller appends its target to."""
    if target is not None and bundle_id is not None:
        raise ValueError("launch_argv: a target or a bundle_id, not both")
    cmd = [str(GOH), "launch-app"]
    flags = ((background, "--background"), (new, "--new"), (wait, "--wait"))
    cmd += [flag for on, flag in flags if on]
    if bundle_id is not None:
        cmd += ["--bundle-id", bundle_id]
    if target is not None:
        cmd.append(str(target))
    if args:
        cmd += ["--", *args]
    return cmd


def launch_app(
    target: str | PathLike[str] | None = None,
    *,
    bundle_id: str | None = None,
    background: bool = False,
    new: bool = False,
    wait: bool = False,
    args: Sequence[str] = (),
    timeout: float | None = 60,
) -> subprocess.CompletedProcess[str]:
    """Run `launch_argv(...)`, stdin closed, output captured; the caller reads `returncode`.
    `wait=True` blocks until the app exits, so pass `timeout=None` with it."""
    if target is None and bundle_id is None:
        raise ValueError("launch_app: a target or a bundle_id is required")
    cmd = launch_argv(target, bundle_id=bundle_id, background=background, new=new, wait=wait,
                      args=args)  # fmt: skip
    return subprocess.run(cmd, capture_output=True, text=True, check=False,
                          stdin=subprocess.DEVNULL, timeout=timeout)  # fmt: skip
