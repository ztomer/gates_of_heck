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
import time
from pathlib import Path

from test_gates_from_head import _env, _structural, consumer, gates  # noqa: F401  (fixtures)

HOUR, DAY = 3600, 86400


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


def test_every_use_stamps_the_export(gates, consumer, tmp_path) -> None:
    env = _env(tmp_path)
    _structural(gates, consumer, env)
    cache = tmp_path / "head-cache"
    (export,) = [p for p in cache.iterdir() if not p.name.startswith(".")]
    os.utime(export / ".goh-used", (1, 1))
    dir_mtime = export.stat().st_mtime_ns
    _structural(gates, consumer, env)
    assert (export / ".goh-used").stat().st_mtime > time.time() - HOUR, "a use left no stamp"
    assert export.stat().st_mtime_ns == dir_mtime, "the stamp rewrote the export's directory"


def test_an_export_unused_for_a_week_goes_and_a_recent_one_stays(gates, consumer, tmp_path) -> None:
    cache = tmp_path / "head-cache"
    _fake(cache, "old", 8 * DAY)
    _fake(cache, "old-unstamped", 8 * DAY, stamp=False)  # an export from before the stamp
    _fake(cache, "recent", 2 * HOUR)
    _structural(gates, consumer, _env(tmp_path))  # a NEW export for HEAD: the prune runs
    left = _exports(cache)
    assert "old" not in left and "old-unstamped" not in left, left
    assert "recent" in left and len(left) == 2, left


def test_beyond_the_count_the_least_recently_used_go(gates, consumer, tmp_path) -> None:
    cache = tmp_path / "head-cache"
    for i in range(25):
        _fake(cache, f"e{i:02d}", (2 + i) * HOUR)  # e00 most recently used
    _structural(gates, consumer, _env(tmp_path))
    left = _exports(cache)
    assert len(left) == 20, sorted(left)  # the new one + the 19 most recently used
    assert {f"e{i:02d}" for i in range(19)} <= left, sorted(left)


def test_an_export_used_within_the_hour_is_never_pruned(gates, consumer, tmp_path) -> None:
    """In use, as far as anything can tell: a gate may be running from it right now."""
    cache = tmp_path / "head-cache"
    for i in range(25):
        _fake(cache, f"hot{i:02d}", 10 * 60)
    _structural(gates, consumer, _env(tmp_path))
    assert len(_exports(cache)) == 26, "an export used ten minutes ago was pruned"
