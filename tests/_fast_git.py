"""`git init` for test fixtures without a git spawn per repo (roadmap 4.1, 2026-10-06).

A suite run made 658 `git init`s, each a process (~18 ms; the template copy inside it is ~1 ms). Each
worker now asks git ONCE per branch argument for an empty repository and copies its `.git` into
every fixture -- byte-for-byte what `git init -q [-b BRANCH]` would make there, because git made it.
A directory that already holds a `.git` (a re-init) goes to git itself.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_TEMPLATES: dict[str, Path] = {}


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def fast_init(repo: Path | str, branch: str | None = None) -> None:
    """`git init -q [-b BRANCH] <repo>`, from a per-process template."""
    repo = Path(repo)
    repo.mkdir(parents=True, exist_ok=True)
    if (repo / ".git").exists():
        subprocess.run(
            ["git", "-C", str(repo), "init", "-q", *(["-b", branch] if branch else [])],
            check=True, capture_output=True, env=_clean_env(),
        )  # fmt: skip
        return
    key = branch or ""
    if key not in _TEMPLATES:
        seed = Path(tempfile.mkdtemp(prefix="goh-test-git."))
        subprocess.run(
            ["git", "init", "-q", *(["-b", branch] if branch else []), str(seed)],
            check=True, capture_output=True, env=_clean_env(),
        )  # fmt: skip
        _TEMPLATES[key] = seed / ".git"
    shutil.copytree(_TEMPLATES[key], repo / ".git", symlinks=True)
