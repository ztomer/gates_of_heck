"""The ONE place goh's temp, lock and staging paths come from, from Python (GOH.CLASS.CHECKOUT).

The shell face is lib/run_path.sh, whose header tells the class; the Rust face is
crates/goh/src/run_path.rs. One root and one key in all three, so a lock taken from a shell is the
lock a Python process waits on:

    run_root()        $GOH_RUN_DIR, else $XDG_STATE_HOME/goh/run, else ~/.local/state/goh/run
    run_dir(tree)     <root>/<basename>-<sha256(physical tree)[:16]>, created
    index_tree(repo)  the tree of the index git would commit, written from a private copy under the
                      root, so it never takes the shared `index.lock`

Pinned by tests/test_run_path.py (the three faces agree on the key).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

import gitrun  # git under a deadline that never strands a lock (lib/gitrun.py)

DEADLINE_S = 60.0


def run_root() -> Path:
    """The root every run path lives under; a relative GOH_RUN_DIR is refused (ValueError)."""
    named = os.environ.get("GOH_RUN_DIR", "")
    if named:
        root = Path(named)
    else:
        state = os.environ.get("XDG_STATE_HOME", "")
        base = Path(state) if state.startswith("/") else Path.home() / ".local" / "state"
        root = base / "goh" / "run"
    if not root.is_absolute():
        raise ValueError(f"GOH_RUN_DIR must be an absolute path: {named!r}")
    return root


def run_dir(tree: Path | str) -> Path:
    """TREE's own directory under the root, created: the same one every call, one per tree."""
    phys = Path(os.path.realpath(tree))
    if not phys.is_dir():
        raise FileNotFoundError(f"goh run_dir: no such directory: {tree}")
    hexd = hashlib.sha256(str(phys).encode()).hexdigest()[:16]
    d = run_root() / f"{phys.name}-{hexd}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def index_tree(repo: Path | str, env: dict[str, str] | None = None) -> str:
    """The tree of the index git would commit in REPO (GIT_INDEX_FILE in ENV when set, relative to
    REPO), written from a private copy so the shared index and its lock are never touched."""
    env = dict(os.environ if env is None else env)
    idx = env.get("GIT_INDEX_FILE", "")
    if idx:
        index = Path(repo, idx)
    else:
        done = gitrun.run_reader(("rev-parse", "--path-format=absolute", "--git-path", "index"),
                                 cwd=repo, env=env, timeout=DEADLINE_S)  # fmt: skip
        if done.returncode != 0:
            raise RuntimeError(f"goh index_tree: no index in {repo}: {done.stderr.strip()}")
        index = Path(done.stdout.strip())
    where = run_root() / "index"
    where.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="tree.", dir=where)
    os.close(fd)
    copy = Path(name)
    try:
        if index.is_file():
            shutil.copyfile(index, copy)
        else:
            copy.unlink()  # no index yet is an empty one
        env["GIT_INDEX_FILE"] = str(copy)
        # A writer of the PRIVATE copy only, under a deadline that stops its whole group.
        wrote = gitrun.run_writer(("write-tree",), cwd=repo, env=env, deadline_s=DEADLINE_S)
        if wrote is None or wrote.returncode != 0:
            why = "stopped at its deadline" if wrote is None else wrote.stderr.strip()
            raise RuntimeError(f"goh index_tree: git write-tree in {repo}: {why}")
        return wrote.stdout.strip()
    finally:
        copy.unlink(missing_ok=True)
