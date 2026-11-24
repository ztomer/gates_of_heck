"""lib/db_record.py -- which copy of goh is the CODE OF RECORD, the one that may migrate or create
the shared goh.db and goh-cache.db (lib/db_core.py; GOH.LANEMIGRATE, GOH.HEADCOPYRECORD).

Decided by the copy's COMMIT, never by the shape of its directory. On 2026-11-24 the SHIPSV2 lane's
pre-commit exported its staged tree to ~/.cache/goh/head/7b705d90.. (no `.git`) and ran
lib/commit_stamp.py from it; the rule then read "no .git" as an install, so as of record, and 0009
reached the real goh.db (00:35:25, "by goh unknown") before 0009 was on main.

The answer is kept per process: about 8 us an open once known; the first costs 0.02 ms for the
primary checkout, 0.2 ms for an export of main's tip, about 10 ms for an export of an older commit
(one `git merge-base`), measured 2026-11-24. Pinned by tests/test_db_record.py.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path

MAIN = "refs/heads/main"


def _git_dir(checkout: Path) -> Path | None:
    """CHECKOUT's common git directory (a linked worktree's `.git` file followed to its main's), or
    None. Read from the files, no spawn."""
    dot = checkout / ".git"
    if dot.is_dir():
        return dot
    try:
        line = dot.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not line.startswith("gitdir: "):
        return None
    wt = (checkout / line.removeprefix("gitdir: ")).resolve()
    try:
        return (wt / (wt / "commondir").read_text(encoding="utf-8").strip()).resolve()
    except OSError:
        return wt


def _main_tip(git_dir: Path) -> str:
    """main's tip in GIT_DIR: the loose ref, else packed-refs; "" when there is none."""
    with contextlib.suppress(OSError):
        return (git_dir / MAIN).read_text(encoding="utf-8").strip()
    with contextlib.suppress(OSError):
        for line in (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines():
            sha, _, ref = line.partition(" ")
            if ref == MAIN:
                return sha
    return ""


def _on_main(git_dir: Path, rev: str) -> str | None:
    """Why REV is not a commit on GIT_DIR's main (None when it is: the tip itself costs no spawn,
    an older commit one `git merge-base --is-ancestor`)."""
    tip = _main_tip(git_dir)
    if not tip:
        return f"{git_dir} has no {MAIN}"
    if rev == tip:
        return None
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(["git", "--no-optional-locks", f"--git-dir={git_dir}", "merge-base",
                            "--is-ancestor", rev, tip],
                           capture_output=True, check=False, timeout=10, env=env,
                           stdin=subprocess.DEVNULL)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"asking whether {rev[:12]} is on main failed ({e})"
    if r.returncode == 0:
        return None
    return f"{rev[:12]} is not a commit on main's tip {tip[:12]} (a lane's commit or a staged tree)"


_RECORD: dict[Path, str | None] = {}


def why_not(root: Path) -> str | None:
    """Why ROOT is not the code of record, or None when it is; decided by its COMMIT, once per
    process (GOH.HEADCOPYRECORD).

      a primary checkout (`.git` a directory)   of record when HEAD is main, or a commit on it
      a linked worktree (`.git` a file)         never: a lane, a landq slot, the idle suite's tree
      an export (`.goh-head` names its rev)     of record when the rev is a commit on main's tip in
                                                the checkout `.goh-live` (or GOH_LIVE_ROOT) names
      anything else with no git                 never: nothing shows its commit is main's

    A head copy of a staged tree (a goh commit's pre-commit, GOH.SELFGATE) or of a lane's commit
    is never of record, which is what let 0009 reach the real goh.db on 2026-11-24."""
    root = Path(os.path.realpath(root))
    if root not in _RECORD:
        _RECORD[root] = _why_not(root)
    return _RECORD[root]


def _why_not(root: Path) -> str | None:
    dot = root / ".git"
    if dot.is_file():
        return f"{root} is a linked worktree"
    if dot.is_dir():
        try:
            head = (dot / "HEAD").read_text(encoding="utf-8").strip()
        except OSError:
            return f"{root} has no HEAD"
        if head == f"ref: {MAIN}":
            return None
        if head.startswith("ref: "):
            ref = head.removeprefix("ref: ")
            try:
                head = (dot / ref).read_text(encoding="utf-8").strip()
            except OSError:
                return f"{root} is on {ref}, not main"
        return _on_main(dot, head)
    try:
        rev = (root / ".goh-head").read_text(encoding="utf-8").strip()
    except OSError:
        return f"{root} has no git and names no commit (no .goh-head)"
    live = ""
    with contextlib.suppress(OSError):
        live = (root / ".goh-live").read_text(encoding="utf-8").strip()
    live = live or os.environ.get("GOH_LIVE_ROOT", "")
    git_dir = _git_dir(Path(live)) if live else None
    if git_dir is None:
        return f"{root} is an export of {rev[:12]} naming no checkout to find main in"
    return _on_main(git_dir, rev)
