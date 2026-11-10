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

A KEPT VARIABLE'S DEFAULT IS THE SAME PLACE (HOME_DEFAULTS). A test that hands its child only `PATH`
and `HOME` drops RUSTUP_HOME and SCCACHE_DIR with everything else, and rustup and sccache fall back
to their defaults under HOME -- this throwaway home. There rustup found no toolchain and INSTALLED
stable into it on every run: a download and a brand-new compiler for sccache to hash inside a timed
build, which turned GOH.EA2's first land red (sccache exit 254, "could not parse/generate dep
info"; test_goh_build_publish.py). So each kept variable's default path under HOME links to what
the variable names, and a scrubbed child reaches the same toolchain and caches as an unscrubbed one.
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
# Where each kept variable's tool looks under HOME when the variable is unset.
HOME_DEFAULTS = {
    "CARGO_HOME": ".cargo",
    "RUSTUP_HOME": ".rustup",
    "SCCACHE_DIR": "Library/Caches/Mozilla.sccache",
    "UV_CACHE_DIR": ".cache/uv",
}

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
    for name, rel in HOME_DEFAULTS.items():
        kept = os.environ.get(name)
        if kept and Path(kept).exists() and not os.path.lexists(home / rel):
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            (home / rel).symlink_to(kept)
    os.environ[MARKER] = REAL_HOME
    # The user's XDG_STATE_HOME, kept by name so real_state_guard.py can watch it; the suite's own
    # state (goh's estate registry, the compact-nudge state) lives in the suite home instead.
    if os.environ.get("XDG_STATE_HOME"):
        os.environ["GATES_OF_HECK_SUITE_REAL_XDG_STATE_HOME"] = os.environ["XDG_STATE_HOME"]
    os.environ["HOME"] = str(home)
    os.environ["XDG_CACHE_HOME"] = str(home / ".cache")
    os.environ["XDG_STATE_HOME"] = str(home / ".local" / "state")


_enter()
import suite_tmp  # noqa: F401  (and the suite's own TMPDIR: tests/suite_tmp.py)
