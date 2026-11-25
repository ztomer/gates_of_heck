"""The goh every gate a test spawns will resolve, built once by the controller (GOH.PYHARNESS).

Beside the run, never ahead of it: the resolver is 0.3-0.5 s of git and shell even when there is
nothing to build (2026-11-25), and a one-file run waited it out before it collected. It is STARTED
as pytest configures and JOINED before the first test is handed out -- `pytest_runtestloop`, which
under xdist is the controller's, where every test is scheduled. A build that fails ends the run
there, a usage error with the build's own words. A run that never reaches its tests (a collection
error, `--collect-only`, an interrupt) starts none or stops the one it started.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
BUILD_S = 900
ASK = (
    'source "$1/gates/_goh_bin.sh"; goh_resolve_native; [ -n "$goh_native" ] '
    '|| { echo "$goh_native_why" >&2; false; }'
)


class Prebuild:
    """One resolve, running; `join` is its verdict."""

    def __init__(self, root: Path) -> None:
        self.proc = subprocess.Popen(
            ["bash", "-c", ASK, "_", str(root)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            env=dict(os.environ, DRIFT_BUILD_OK="1"), start_new_session=True,
        )  # fmt: skip
        self.joined = False

    def join(self) -> None:
        from timing_bounds import patience  # here: it imports the drift guard, which imports this

        self.joined = True
        try:
            _, said = self.proc.communicate(timeout=patience(BUILD_S))
        except subprocess.TimeoutExpired as e:
            self.stop()
            raise pytest.UsageError(f"the goh the gates resolve took over {BUILD_S} s") from e
        if self.proc.returncode != 0:
            raise pytest.UsageError(f"the goh the gates resolve did not build:\n{said}")

    def stop(self) -> None:
        """Its whole group (a cargo under it), and its zombie."""
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.proc.pid, signal.SIGKILL)
        self.proc.wait()
        self.proc.stderr.close()

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtestloop(self, session) -> None:
        # An answer here would be the loop's own (firstresult): join says None or raises.
        self.join()

    def pytest_unconfigure(self, config) -> None:
        if not self.joined:
            self.stop()


def prebuild_live(config, root: Path | None = None) -> Prebuild | None:
    """Start the one resolve of this run, registered to be joined before its tests; None for a
    process that needs none.

    Under GOH_LIVE with the working tree's Rust ahead of HEAD that is the tree's build
    (`goh_live_binary`); otherwise HEAD's (bin/goh, or the store). Only the first half used to be
    done here, so a checkout whose bin/goh was not HEAD's build -- every fresh worktree, every
    commit whose crates moved -- left the FIRST gate a test ran to build it, which the cargo shim
    refuses: 9 tests of test_rust_gate.py failed in any new worktree (G2c). `goh_resolve_native`
    is the one resolver the gates call, so the controller asks exactly what they will.
    """
    if hasattr(config, "workerinput") or not os.environ.get("GOH_LIVE"):
        return None  # a worker: the controller already did it
    if os.environ.get("GOH_STAGED_GUARD_RUN"):
        # A commit's source guard (gates/staged_guards.sh) runs tests that read the tree's text and
        # spawn no gate; inside a fixture's commit there is not even a cargo to build with.
        return None
    if config.getoption("collectonly", False):
        return None  # no test runs, so no gate does
    build = Prebuild(root or _ROOT)
    config.pluginmanager.register(build, "goh-prebuild")
    return build
