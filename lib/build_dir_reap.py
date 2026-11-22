"""The cargo build dirs a checkout owns alone, removed with the checkout (GOH.BUILDDIRSHARE).

~/.cargo/config.toml keys cargo's build dir by workspace path (`{workspace-path-hash}`), so every
linked worktree builds a full copy under the cache root, and removing the worktree left that copy
behind: an orphan only a reclaimer's sweep found, hours later (~/.cargo/build 178 GiB in 255 dirs,
2026-11-17). A checkout's own build dirs are the ones cargo names for it (gates/_push_build_dirs.py,
asked while it exists), less any the main checkout also uses (a repo that names one fixed build dir
shares it) and any inside the tree (they go with it). `owned` is asked BEFORE the removal, cargo
cannot name a missing path's; `remove` after it.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gates"))
from _push_build_dirs import build_dirs

del sys.path[0]


def owned(tree: Path, main: Path) -> list[Path]:
    """TREE's build dirs that neither MAIN uses nor TREE contains."""
    if not tree.is_dir():
        return []
    inside, shared = tree.resolve(), {Path(d).resolve() for d in build_dirs(str(main))}
    mine = (Path(d).resolve() for d in build_dirs(str(tree)))
    return [d for d in mine if d not in shared and not d.is_relative_to(inside)]


def remove(dirs: list[Path]) -> None:
    """Each of DIRS gone (a missing one is fine)."""
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)
