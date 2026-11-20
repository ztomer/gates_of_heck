"""`git init` for test fixtures without a git spawn per repo (roadmap 4.1, 2026-10-06).

A suite run made 658 `git init`s, each a process (~18 ms; the template copy inside it is ~1 ms).
Git is asked ONCE per branch argument for an empty repository, whose `.git` is copied into every
fixture -- what `git init -q [-b BRANCH]` would make there, because git made it, less git's inert
`hooks/*.sample` files. A directory that already holds a `.git` (a re-init) goes to git itself.

ONE SEED A RUN, CLONED (GOH.FSCHURN3): inside a pytest session the seed is the RUN's (a sealed
`process_template`, tests/_tree_clone.py) and each fixture's `.git` is a clonefile(2) of it, three
file events where a copy was one per file; the 14 sample hooks git writes into every new repository
are dropped from the seed, since each was one more file to make and remove in every fixture.
"""

from __future__ import annotations

import functools
import os
import subprocess
from pathlib import Path

from _tree_clone import clone_tree, process_template

_TEMPLATES: dict[str, Path] = {}


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def fast_init(repo: Path | str, branch: str | None = None) -> None:
    """`git init -q [-b BRANCH] <repo>`, from the run's (or this process's) seed."""
    repo = Path(repo)
    repo.mkdir(parents=True, exist_ok=True)
    if (repo / ".git").exists():
        subprocess.run(
            ["git", "-C", str(repo), "init", "-q", *(["-b", branch] if branch else [])],
            check=True, capture_output=True, env=_clean_env(), stdin=subprocess.DEVNULL,
        )  # fmt: skip
        return
    key = branch or ""
    if key not in _TEMPLATES:
        _TEMPLATES[key] = (
            process_template(
                f"fast-init-{key or 'default'}", functools.partial(_seed, branch=branch), False
            )
            / ".git"
        )
    clone_tree(_TEMPLATES[key], repo / ".git")


def _seed(tree: Path, branch: str | None) -> None:
    subprocess.run(
        ["git", "init", "-q", *(["-b", branch] if branch else []), str(tree)],
        check=True, capture_output=True, env=_clean_env(), stdin=subprocess.DEVNULL,
    )  # fmt: skip
    for sample in (tree / ".git" / "hooks").glob("*.sample"):
        sample.unlink()
