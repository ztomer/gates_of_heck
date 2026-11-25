"""`grow(repo, key, build)`: REPO as build(REPO) leaves it, built once a run (GOH.FSEVQUIET);
`built(dst, key, build)`: DST, which is not there, as build(DST) makes it.

A kit writes and commits the same files into the `repo` fixture for each of its tests: a file event
to make each and one to remove it. Where REPO is an untouched clone of a run's template, build() is
run once, on a template of its own, and every later REPO is a clone of that (and pooled,
tests/_tree_pool.py); anywhere else build(REPO) is run. KEY names everything build() depends on: it
must make the same tree wherever REPO is (a template that names its own directory is refused).
Pinned by tests/test_grown.py.
"""

from __future__ import annotations

import functools
import hashlib
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import _tree_pool
from _tree_clone import _clone_part, _is_sealed, _unseal_clone, clone_tree, process_template


def _seed(src: str, build: Callable[[Path], object], tree: Path) -> None:
    _clone_part(src, str(tree))
    _unseal_clone(src, tree)
    build(tree)


def built(dst: Path, key: str, build: Callable[[Path], object]) -> Path:
    """DST as build(DST) makes it: a clone of the one tree build() made this run."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    clone_tree(process_template(key, build, keep_quiet=False), dst)
    return dst


def tracked(dst: Path, origin: Path, key: str, build: Callable[[Path], object]) -> Path:
    """DST, its `main` tracking ORIGIN, a bare clone of it: as build(DST), `git clone --bare`, `git
    remote add`, a fetch and an upstream leave the two, made once a run."""

    def both(tree: Path) -> None:
        repo, bare = tree / "repo", tree / "origin.git"
        build(repo)
        for args in (
            ("clone", "-q", "--bare", str(repo), str(bare)),
            ("--git-dir", str(bare), "remote", "remove", "origin"),  # it names the template
            ("-C", str(repo), "update-ref", "refs/remotes/origin/main", "main"),
            ("-C", str(repo), "config", "branch.main.remote", "origin"),
            ("-C", str(repo), "config", "branch.main.merge", "refs/heads/main"),
        ):
            subprocess.run(["git", *args], check=True, capture_output=True,
                           stdin=subprocess.DEVNULL)  # fmt: skip

    tree = process_template(key, both, keep_quiet=False)
    for part, at in ((tree / "repo", dst), (tree / "origin.git", origin)):
        at.parent.mkdir(parents=True, exist_ok=True)
        clone_tree(part, at)
    with (dst / ".git" / "config").open("a", encoding="utf-8") as config:
        config.write(
            f'[remote "origin"]\n\turl = {origin}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
        )
    return dst


def keyed(name: str, *parts: object) -> str:
    """A key for what build() makes of PARTS."""
    return f"{name}-{hashlib.sha256(repr(parts).encode()).hexdigest()[:16]}"


def grow(repo: Path, key: str, build: Callable[[Path], object]) -> Path:
    src = _tree_pool.whole(_tree_pool.real_of(repo), _is_sealed)
    if not src:
        build(repo)
        return repo
    tree = process_template(
        f"{Path(src).name}-{key}", functools.partial(_seed, src, build), keep_quiet=False
    )
    if not _tree_pool.put(_tree_pool.real_of(repo), _clone_part, _is_sealed):
        shutil.rmtree(repo)
    clone_tree(tree, repo)
    return repo
