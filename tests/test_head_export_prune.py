"""The HEAD export cache stays bounded, and an export in use is never pruned (roadmap 1.2).

`gates/_from_head.sh` exports HEAD once per commit into `~/.cache/goh/head/<sha>/`. The prune it had
removed exports by their CREATION time after a week, so a long-lived worktree's export could go
while that worktree still ran from it, and nothing bounded the count: 55 exports, 226 MB on
2026-10-06. Now every use stamps the export's `.goh-used` (a truncate: the export's own files and
directory are never rewritten), and each NEW export prunes the others: unused for a week, or
beyond the 20 most recently used and unused for an hour.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from _fast_git import fast_init
from _tree_clone import from_template
from conftest import git
from source_closure import copy_closure
from test_gates_from_head import _env
from timing_bounds import patience

HOUR, DAY = 3600, 86400
ENTRY = "gates/entry.sh"


def _build_gates(root: Path) -> None:
    """A checkout of the one entry point's closure: an export of it is ~20 files, not 600."""
    copy_closure(
        root, "gates/_from_head.sh", extra=("gates/_head_cache.py", "gates/_export_bin_goh.sh")
    )
    (root / ENTRY).write_text(
        '#!/usr/bin/env bash\n. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"\n'
        'goh_from_head "${BASH_SOURCE[0]}" "$@"\necho "ran from $GOH_DIR"\n'
    )
    fast_init(root, "main")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the entry point")


@pytest.fixture
def gates(tmp_path_factory, tmp_path: Path) -> Path:
    return from_template(tmp_path_factory, "prune-gates", _build_gates, tmp_path / "gates")


def _use(gates: Path, env: dict) -> None:
    r = subprocess.run(["bash", str(gates / ENTRY)], cwd=gates, env=env, capture_output=True,
                       text=True, timeout=patience(120), check=False,
                       stdin=subprocess.DEVNULL)  # fmt: skip
    assert r.returncode == 0 and f"ran from {env['GOH_HEAD_CACHE']}" in r.stdout, (
        r.stdout + r.stderr
    )


def _fake(cache: Path, name: str, used_ago: float, stamp: bool = True) -> Path:
    d = cache / name
    d.mkdir(parents=True)
    (d / ".goh-head").write_text(name + "\n")
    then = time.time() - used_ago
    if stamp:
        (d / ".goh-used").write_text("")
        os.utime(d / ".goh-used", (then, then))
    os.utime(d / ".goh-head", (then, then))
    return d


def _exports(cache: Path) -> set[str]:
    return {p.name for p in cache.iterdir() if not p.name.startswith(".")}


@pytest.mark.load_sensitive
def test_every_use_stamps_the_export(gates, tmp_path) -> None:
    env = _env(tmp_path)
    _use(gates, env)
    cache = tmp_path / "head-cache"
    (export,) = [p for p in cache.iterdir() if not p.name.startswith(".")]
    os.utime(export / ".goh-used", (1, 1))
    dir_mtime = export.stat().st_mtime_ns
    _use(gates, env)
    assert (export / ".goh-used").stat().st_mtime > time.time() - HOUR, "a use left no stamp"
    assert export.stat().st_mtime_ns == dir_mtime, "the stamp rewrote the export's directory"


@pytest.mark.load_sensitive
def test_an_export_unused_for_a_week_goes_and_a_recent_one_stays(gates, tmp_path) -> None:
    cache = tmp_path / "head-cache"
    _fake(cache, "old", 8 * DAY)
    _fake(cache, "old-unstamped", 8 * DAY, stamp=False)  # an export from before the stamp
    _fake(cache, "recent", 2 * HOUR)
    _use(gates, _env(tmp_path))  # a NEW export for HEAD: the prune runs
    left = _exports(cache)
    assert "old" not in left and "old-unstamped" not in left, left
    assert "recent" in left and len(left) == 2, left


@pytest.mark.load_sensitive
def test_beyond_the_count_the_least_recently_used_go(gates, tmp_path) -> None:
    cache = tmp_path / "head-cache"
    for i in range(25):
        _fake(cache, f"e{i:02d}", (2 + i) * HOUR)  # e00 most recently used
    _use(gates, _env(tmp_path))
    left = _exports(cache)
    assert len(left) == 20, sorted(left)  # the new one + the 19 most recently used
    assert {f"e{i:02d}" for i in range(19)} <= left, sorted(left)


@pytest.mark.load_sensitive
def test_an_export_used_within_the_hour_is_never_pruned(gates, tmp_path) -> None:
    """In use, as far as anything can tell: a gate may be running from it right now."""
    cache = tmp_path / "head-cache"
    for i in range(25):
        _fake(cache, f"hot{i:02d}", 10 * 60)
    _use(gates, _env(tmp_path))
    assert len(_exports(cache)) == 26, "an export used ten minutes ago was pruned"


def _prune(cache: Path, current: str, **kw) -> list[str]:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gates"))
    import _head_cache

    return _head_cache.prune(str(cache), current, **kw)


def _sized(cache: Path, name: str, used_ago: float, parts: int) -> Path:
    d = _fake(cache, name, used_ago)
    (d / "blob").write_bytes(b"\1" * (parts << 16))
    os.utime(d / ".goh-used", (time.time() - used_ago,) * 2)
    return d


@pytest.mark.load_sensitive
def test_past_the_byte_budget_the_least_recently_used_go(tmp_path) -> None:
    """A count bounds entries, not bytes: 334 coverage builds inside their week held 11 GiB."""
    cache = tmp_path / "cov"
    _sized(cache, "cur", 0, 1)
    for i in range(5):
        _sized(cache, f"b{i}", (2 + i) * HOUR, 1)  # b0 most recently used
    _prune(cache, "cur", keep=200, max_bytes=(3 << 16) + (32 << 10))
    assert _exports(cache) == {"cur", "b0", "b1"}, sorted(_exports(cache))


@pytest.mark.load_sensitive
def test_the_byte_budget_never_takes_one_used_within_the_hour(tmp_path) -> None:
    cache = tmp_path / "cov"
    _sized(cache, "cur", 0, 1)
    for i in range(3):
        _sized(cache, f"hot{i}", 10 * 60, 1)
    _prune(cache, "cur", keep=200, max_bytes=1)
    assert _exports(cache) == {"cur", "hot0", "hot1", "hot2"}, sorted(_exports(cache))


@pytest.mark.load_sensitive
def test_without_a_budget_bytes_are_not_counted(tmp_path) -> None:
    cache = tmp_path / "cov"
    for i in range(3):
        _sized(cache, f"b{i}", (2 + i) * HOUR, 1)
    _prune(cache, "b0", keep=200)
    assert len(_exports(cache)) == 3
