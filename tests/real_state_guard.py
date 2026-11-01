"""No test writes the machine's real goh state (the class behind 76 gone registry entries).

The real estate registry (`~/.local/state/goh/installed_repos`) filled with `tmp.*/{boxa,pib}`
paths: scratch repos an install recorded into the machine's state, each one a "registered but gone"
problem at every session start. The suite runs in its own HOME (`suite_home.py`) and its own
`GOH_ESTATE_STATE` (conftest.py), and install.sh refuses a scratch repo into an unnamed registry;
this is the backstop that makes a NEW writer red instead of a slow leak: an autouse session fixture
snapshots the REAL state directory and fails the session if a test added a line naming a scratch
path (the temp directory, or the suite's own home). Pinned by tests/test_real_state_guard.py.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

STATE_FILES = ("installed_repos",)


def real_state_dir(real_home: str) -> Path:
    """The machine's goh state directory, as the user's own shell resolves it."""
    base = os.environ.get("GATES_OF_HECK_SUITE_REAL_XDG_STATE_HOME") or str(
        Path(real_home) / ".local" / "state"
    )
    return Path(base) / "goh"


def scratch_roots(real_home: str) -> list[str]:
    roots = {os.path.realpath(tempfile.gettempdir()), os.path.realpath("/tmp")}
    roots.add(os.path.realpath(os.path.join(real_home, ".cache", "goh", "suite")))
    return sorted(roots)


def snapshot(state_dir: Path) -> dict[str, set[str]]:
    out = {}
    for name in STATE_FILES:
        try:
            out[name] = set((state_dir / name).read_text(encoding="utf-8").splitlines())
        except OSError:
            out[name] = set()
    return out


def scratch_lines_added(
    before: dict[str, set[str]], after: dict[str, set[str]], roots: list[str]
) -> list[str]:
    """`<file>: <line>` for every line a test added that names a scratch path."""
    bad = []
    for name, lines in after.items():
        for line in sorted(lines - before.get(name, set())):
            path = os.path.realpath(line.strip()) if line.strip().startswith("/") else ""
            if path and any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots):
                bad.append(f"{name}: {line}")
    return bad


@pytest.fixture(scope="session", autouse=True)
def no_test_writes_the_real_goh_state():
    """A scratch path added to the machine's real goh state during the session fails it, naming
    the line (imported by conftest.py as an autouse fixture)."""
    real = os.environ["GATES_OF_HECK_SUITE_REAL_HOME"]
    state = real_state_dir(real)
    before = snapshot(state)
    yield
    bad = scratch_lines_added(before, snapshot(state), scratch_roots(real))
    if bad:
        pytest.fail(f"a test wrote the machine's real goh state ({state}): {bad}", pytrace=False)
