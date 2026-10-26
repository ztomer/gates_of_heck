"""The machine-wide locks, kept out of every test's reach (registered by conftest.py's import)."""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

# The machine-wide locks, by the variable that moves each one. A test that reached the REAL
# lock waited on whatever the host was doing: two bench_lock tests ran `quiet.sh`, which took the
# real GPU lock while another session's model sweep held it, and failed after 60 s at every load
# (2026-10-10). Only the GPU lock's own tests had redirected it.
HOST_LOCKS = (
    "ZTOOLS_GPU_LOCK_DIR",
    "GOH_BENCH_LOCK_DIR",
    "GOH_BENCH_DESKTOP_LOCK_DIR",
    "GOH_GATE_SLOT_DIR",
)

# The user's caches the gates write, by the variable that moves each one. A test that ran
# push_gate.sh with its GOH_* scrubbed left a zero-byte `repo-<sha>-<time>.log` in the REAL
# ~/.cache/goh/push-logs at every suite run (G11, 2026-10-26). Private by default, like the locks;
# push_gate.sh refuses to run under this suite without them, so a new test cannot forget.
CACHE_ROOTS = (
    "GOH_PUSH_LOGS",
    "GOH_PUSH_WORKTREES",
)

# A push worktree must be PHYSICAL and outside the temp directory (SwiftLint's baseline matches
# nothing under $TMPDIR; tests/test_push_gate.py), so the suite's worktree root is its OWN directory
# under ~/.cache/goh/suite -- one per process, removed when it exits -- never the user's push root.
_SUITE_HOME = Path(os.path.expanduser("~/.cache/goh/suite"))
_SUITE_HOME.mkdir(parents=True, exist_ok=True)
_WORKTREES = Path(tempfile.mkdtemp(prefix="push.", dir=_SUITE_HOME)).resolve()
atexit.register(shutil.rmtree, _WORKTREES, True)

# Every variable an env scrub must KEEP: each names a private location, not an opt-in.
PRIVATE_ROOTS = HOST_LOCKS + CACHE_ROOTS


def scrubbed(environ, prefixes: tuple[str, ...], keep: tuple[str, ...] = ()) -> dict[str, str]:
    """`environ` without the variables starting with `prefixes`, but never without PRIVATE_ROOTS:
    the one way a test drops a prefix, so no scrub sends a child to the user's locks or caches."""
    kept = (*PRIVATE_ROOTS, *keep)
    return {k: v for k, v in environ.items() if not k.startswith(prefixes) or k in kept}


@pytest.fixture(autouse=True)
def no_test_reaches_a_host_lock(tmp_path_factory, monkeypatch):
    """Every test, and every process it starts, gets private copies of the machine's locks."""
    root = tmp_path_factory.mktemp("host-locks")
    for name in HOST_LOCKS + ("GOH_PUSH_LOGS",):
        monkeypatch.setenv(name, str(root / name.lower()))
    monkeypatch.setenv("GOH_PUSH_WORKTREES", tempfile.mkdtemp(dir=_WORKTREES))
    monkeypatch.delenv("ZTOOLS_GPU_LOCK_OWNER", raising=False)
    # A slot the suite's own push holds is not one a fixture gate is inside (lib/gate_slot.py).
    monkeypatch.delenv("GOH_GATE_SLOT", raising=False)
