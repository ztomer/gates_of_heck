"""The retired Python checkers, as the SPEC the native ports are pinned to (Phase N2).

Phase N3 deleted the ported `checks/*.py` from the tree: no gate runs them. But a parity test whose
reference is deleted pins nothing, and the reference's measured behaviour IS the spec -- every port
was proven identical to it on the estate. So the reference is not deleted from history: it is read,
whole, from `REFERENCE_COMMIT` (the last commit that shipped it), exported once per machine into
the cache, and run from there. Its files never change again, so neither does the spec.

    from reference_kit import load_reference, reference_path
    CHECKER = reference_path("checks/check_no_emoji.py")   # run it
    md = load_reference("_md_text")                         # or import it

A frozen spec is the point: a native change that moves a verdict goes red against a reference no
edit can reach. To CHANGE the spec on purpose, write the new expectation into the native test that
owns it; the reference is never edited and never re-pinned to a newer commit.

Needs this repository's history (a full clone, or the push gate's export worktree of it). Without
the commit it refuses, by name -- never a skip, which would read as a pass.
"""

from __future__ import annotations

import fcntl
import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent

# The last commit whose tree carries every ported Python checker, after their own fixes.
REFERENCE_COMMIT = "96018bdb89a6eb4d57e912b065ce7061dc381e18"
# What the reference needs to run: its checkers, `_gitutil`, the TUI and the C4 helper they import.
_PARTS = ("checks", "gates", "lib", "tui")


def _cache() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "goh" / "reference" / REFERENCE_COMMIT


def reference_root() -> Path:
    """The reference checkout, exported once (under a lock, published by rename)."""
    root = _cache()
    if (root / ".complete").is_file():
        return root
    root.parent.mkdir(parents=True, exist_ok=True)
    with open(root.parent / f"{REFERENCE_COMMIT}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (root / ".complete").is_file():
            return root
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        have = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{REFERENCE_COMMIT}^{{commit}}"],
            env=env,
            capture_output=True,
        )
        if have.returncode != 0:
            raise RuntimeError(
                f"the Python reference lives at commit {REFERENCE_COMMIT[:12]}, which this clone "
                "does not have -- fetch the full history (a shallow clone cannot run the spec)"
            )
        staging = root.with_name(root.name + f".staging-{os.getpid()}")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        archive = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "archive", REFERENCE_COMMIT, *_PARTS],
            env=env,
            capture_output=True,
            check=True,
        ).stdout
        subprocess.run(["tar", "-x", "-C", str(staging)], input=archive, check=True)
        (staging / ".complete").write_text(REFERENCE_COMMIT + "\n")
        shutil.rmtree(root, ignore_errors=True)
        staging.rename(root)
    return root


def reference_path(rel: str) -> Path:
    """`rel` (e.g. `checks/check_no_emoji.py`) inside the frozen reference."""
    return reference_root() / rel


def load_reference(name: str) -> ModuleType:
    """The reference's `checks/<name>.py`, imported for a PARITY test, leaving no trace.

    The import runs with the reference's `checks/` first on `sys.path` (a shim under some retired
    names would exec the native the moment it is imported), and afterwards `sys.path` is restored
    and every module the import pulled in from the reference is dropped from `sys.modules`. A
    reference left importable leaks into every later test in the worker: a test of a retired module
    that nobody converted then passes by accident instead of failing as the to-do list it is."""
    checks = str(reference_root() / "checks")
    root = str(reference_root())
    before, saved = set(sys.modules), list(sys.path)
    sys.path.insert(0, checks)
    try:
        module = importlib.import_module(name)
    finally:
        sys.path[:] = saved  # the reference inserts its own dir at import, too
        for key in set(sys.modules) - before:
            if (getattr(sys.modules[key], "__file__", None) or "").startswith(root):
                del sys.modules[key]
    return module
