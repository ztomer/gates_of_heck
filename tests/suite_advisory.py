"""The advisory database a run's audits read, made once for every run (GOH.FSEVQUIET).

goh audits against its OWN copy (`~/.cache/goh/advisory-db`, lib/advisory_db.py), seeded from
cargo's. In a run's fresh home the first audit seeded it: 2,800 files and directories checked out,
read once and removed with the home. `link(home)` gives the home that copy, already made: one a
commit of cargo's, beside the suite's homes, kept for the runs after. Never cargo's own, which any
other audit on the machine refreshes under a reader (lib/advisory_db.py).

READ-ONLY: a refresh of it is the next run's copy under a reader, so it is sealed as a template's
git dir is (tests/_tree_clone.py) and an audit that fetches fails. No copy of cargo's, or no git:
the home is as it was, and an audit in it seeds or fetches. A copy whose commit cargo left is
removed when no run has taken it for a day. Used by tests/suite_home.py; pinned by
tests/test_suite_advisory.py.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import shutil
import subprocess
from pathlib import Path

OLD_S = 24 * 3600
SEALED = (".git/objects", ".git/refs", ".git", "")
UPSTREAM = "https://github.com/RustSec/advisory-db.git"  # lib/advisory_db.py's


def _git(*args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    got = subprocess.run(["git", *args], capture_output=True, text=True, check=True, env=env,
                         stdin=subprocess.DEVNULL)  # fmt: skip
    return got.stdout.strip()


def _remove(copy: Path) -> None:
    for part in reversed(SEALED):
        with contextlib.suppress(OSError):
            (copy / part).chmod(0o755)
    shutil.rmtree(copy, ignore_errors=True)


def _make(shared: Path, copy: Path) -> None:
    stage = copy.with_name(f".{copy.name}.build")
    _remove(stage)
    _git("clone", "-q", "--local", str(shared), str(stage))
    _git("-C", str(stage), "remote", "set-url", "origin", UPSTREAM)
    stage.rename(copy)
    for part in SEALED:
        (copy / part).chmod(0o555)


def link(home: Path, parent: Path, shared: Path) -> Path | None:
    """HOME's `~/.cache/goh/advisory-db`, a link to PARENT's copy of SHARED; the copy, or None."""
    try:
        head = _git("-C", str(shared), "rev-parse", "HEAD")
        copy = parent / f"advisory-db.{head[:16]}"
        with (parent / "advisory-db.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not copy.is_dir():
                _make(shared, copy)
            os.utime(copy)  # taken: the day before its removal starts again
            now = copy.stat().st_mtime
            for old in parent.glob("advisory-db.*"):
                if old != copy and old.is_dir() and now - old.stat().st_mtime > OLD_S:
                    _remove(old)
    except (OSError, subprocess.SubprocessError):
        return None
    at = home / ".cache" / "goh" / "advisory-db"
    at.parent.mkdir(parents=True, exist_ok=True)
    at.symlink_to(copy)
    return copy
