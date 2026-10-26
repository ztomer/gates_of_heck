"""The suite runs in its OWN home (imported first by conftest.py, before anything reads HOME).

THE CLASS (G11, G12). Every gate cache defaults to `${XDG_CACHE_HOME:-$HOME/.cache}/goh`: the push
logs and worktrees, the head export cache, the shared goh binary store, agent_guard's spawn ledger.
A test that scrubbed the one variable moving its writer fell back to the USER's cache -- a zero-byte
push log at every suite run, and a ledger row would corrupt the model-choice audit. So HOME and
XDG_CACHE_HOME point at a suite-owned home for the whole session, and a new writer is isolated
without anyone remembering it. lib/suite_cache_guard.sh refuses, under this suite, any write that
still lands in the real ~/.cache/goh.

The home lives under the REAL ~/.cache/goh/suite, not the temp directory: a push worktree must be
physical and outside $TMPDIR (SwiftLint's baseline; tests/test_push_gate.py). One per process,
removed at exit.

What the real home still provides is named here, explicitly: the toolchains (CARGO_HOME,
RUSTUP_HOME), the user site packages pytest-xdist lives in (PYTHONUSERBASE), the compile and package
caches (SCCACHE_DIR, UV_CACHE_DIR -- build caches, not goh's), and a MINIMAL git identity. And the
things the gates READ from the user's home by design, linked in by name: the estate the calibration
and corpus checks survey (~/Projects), the skills the structural gate reads (~/.claude/skills), and
cargo's shared advisory database (~/.cargo). Nothing else of the real home is reachable.
"""

from __future__ import annotations

import atexit
import os
import shutil
import site
import tempfile
from pathlib import Path

REAL_HOME = os.path.realpath(os.path.expanduser("~"))
MARKER = "GATES_OF_HECK_SUITE_REAL_HOME"  # not GOH_-prefixed: no env scrub drops it

READ = ("Projects", ".claude/skills", ".cargo")

GITCONFIG = "[user]\n\tname = goh suite\n\temail = suite@goh.test\n[init]\n\tdefaultBranch = main\n"


def _enter() -> None:
    if os.environ.get(
        MARKER
    ):  # a nested run (an xdist worker, a suite a test starts) keeps its own
        return
    real = Path(REAL_HOME)
    keep = {
        "CARGO_HOME": real / ".cargo",
        "RUSTUP_HOME": real / ".rustup",
        "PYTHONUSERBASE": Path(site.getuserbase()),
        "SCCACHE_DIR": real / "Library" / "Caches" / "Mozilla.sccache",
        "UV_CACHE_DIR": real / ".cache" / "uv",
    }
    for name, path in keep.items():
        if name not in os.environ and path.exists():
            os.environ[name] = str(path)
    parent = real / ".cache" / "goh" / "suite"
    parent.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="home.", dir=parent)).resolve()
    atexit.register(shutil.rmtree, home, True)
    (home / ".gitconfig").write_text(GITCONFIG)
    (home / ".cache").mkdir()
    for rel in READ:
        if (real / rel).exists():
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            (home / rel).symlink_to(real / rel)
    os.environ[MARKER] = REAL_HOME
    os.environ["HOME"] = str(home)
    os.environ["XDG_CACHE_HOME"] = str(home / ".cache")


_enter()
