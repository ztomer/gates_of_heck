"""The `goh undo` command a discard refusal prints (GOH.UNDOMINE, 2026-11-21).

The discard guard (`hooks/claude/discard_guard.py`) refuses `git checkout -- <file>`, `git restore`
and `git reset --hard` over uncommitted work, and agents met that refusal 44 times in 7 days while
undoing their OWN edits. The refusal now names the safe way to do exactly that: `goh undo <files>`
(`tools/undo.py`), which restores only the caller's own files in its own worktree and keeps a copy.
"""

from __future__ import annotations

import os
import shlex
import subprocess

SHOWN = 8  # files named in the command; more are `...`


def _git(where: str, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(
            ["git", "-C", where, *args], capture_output=True, text=True, timeout=10,
            env={**env, "GIT_OPTIONAL_LOCKS": "0"}, stdin=subprocess.DEVNULL, check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def hint(where: str, lost: list[str]) -> str:
    """The `goh undo` command that puts back `lost` (repo-relative, as git names them), run from
    `where`, keeping a copy; in a repo's main checkout it says the command refuses there."""
    top = _git(where, "rev-parse", "--show-toplevel") or where
    rel = [os.path.relpath(os.path.join(top, p), where) for p in lost[:SHOWN]]
    files = " ".join(shlex.quote(p) for p in rel) + (" ..." if len(lost) > SHOWN else "")
    dirs = _git(where, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir")
    shared = len(set(dirs.split())) == 1  # the git dir IS the common one: a main checkout
    caveat = " -- in a worktree of your own: it refuses this shared checkout" if shared else ""
    return (
        f"To undo your OWN edits, keeping a copy: `goh undo {files}`{caveat} "
        "(`goh undo --back <file>` steps back)."
    )
