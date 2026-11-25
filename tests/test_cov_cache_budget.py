"""The coverage cache's budget holds the estate's working set (GOH.COVCACHECHURN, 2026-11-18).

The budget was 8 GiB. A push sweep visits the estate's active Rust repos in turn every ~1.5-2 h,
and least-recently-used eviction against a budget smaller than the set the sweep cycles through
evicts each build just before its repo comes round again: on 2026-11-18 the cache held only builds
used in the last ~70 minutes, none of the previous sweep's pushers, so every push built cold. The
budget is now sized from the entries below, measured: an instrumented build (`du -sm` of its
`~/.cache/goh/llvm-cov/<key>`) left by a real run, either in the live cache or by running the repo's
own coverage command at its HEAD into an empty cache. A sweep through them twice hits on every
push of the second pass under the default; under the old 8 GiB it hits on none (the calibration:
this test can fail).
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "gates"))
import _cov_cache

MIB = 1 << 20

# MiB per instrumented build, measured 2026-11-18: every repo with a Rust coverage step that pushed
# 2026-11-16..18 (the timing ledger's push rows), goh's three land slots, and eight goh lanes live
# at once (the most a day of lanes held, docs/config/GOH_COV_CACHE_MAX_GIB.md): a lane's
# worktree is its own key. 14.7 GiB of repos plus 11.6 GiB of lanes.
ESTATE_MIB = {
    "monitor": 2684,
    "divoom-control": 2541,
    "antiknob": 1375,
    "ztools": 1358,
    "routines": 1201,
    "token-pool": 1159,
    "goh slot 0": 893,
    "goh slot 1": 893,
    "goh slot 2": 893,
    "app_updates": 797,
    "sys_updater": 482,
    "app-mcp-bridge": 395,
    "webcam_tune": 172,
    "agent-feed": 152,
    "house-lib": 41,
    **{f"goh lane {i}": 1483 for i in range(8)},
}


DEAD_PID = 1 << 30  # past any pid_max: os.kill says no such process, and no process is spawned


def _sweep(cache: Path, gib: float, cycles: int = 2) -> list[float]:
    """Each repo of the estate runs once per cycle, in order; returns each cycle's hit rate."""
    cache.mkdir()
    names = {f"k{i:02d}": n for i, n in enumerate(ESTATE_MIB)}
    sizes = defaultdict(int, {str(cache / k): ESTATE_MIB[n] * MIB for k, n in names.items()})
    clock = 1_800_000_000.0
    rates = []
    for _cycle in range(cycles):
        hits = 0
        for key in names:
            clock += 600  # ten minutes a push
            entry = cache / key
            hits += (entry / "build").is_dir()
            (entry / "build").mkdir(parents=True, exist_ok=True)
            (entry / ".goh-running").write_text(f"{DEAD_PID}\n")  # the run has ended
            (entry / ".goh-used").write_text("")
            os.utime(entry / ".goh-used", (clock, clock))
            _cov_cache.prune(
                str(cache),
                key,
                gib=gib,
                now=clock,
                size=sizes.__getitem__,
                free=1 << 50,
            )
        rates.append(hits / len(names))
    return rates


def test_the_default_budget_holds_the_estates_working_set(tmp_path) -> None:
    held = sum(ESTATE_MIB.values()) * MIB
    assert 1.2 * held <= _cov_cache.DEFAULT_GIB * _cov_cache.GIB, (
        f"the default {_cov_cache.DEFAULT_GIB} GiB is under 1.2x the measured working set "
        f"{held / _cov_cache.GIB:.1f} GiB"
    )
    assert _sweep(tmp_path / "cov", _cov_cache.DEFAULT_GIB) == [0.0, 1.0]


def test_the_old_budget_evicted_every_build_before_its_next_push(tmp_path) -> None:
    """The calibration: under 8 GiB the same sweep misses on every push, both passes."""
    assert _sweep(tmp_path / "cov", 8) == [0.0, 0.0]


def test_the_disk_floor_shrinks_the_budget() -> None:
    gib = _cov_cache.GIB
    roomy = _cov_cache.budget(48, 30, held=5 * gib, free=130 * gib)
    assert roomy == 48 * gib
    short = _cov_cache.budget(48, 30, held=20 * gib, free=50 * gib)  # 70 - (30 + 10 reserve)
    assert short == 30 * gib
    assert _cov_cache.budget(48, 30, held=0, free=10 * gib) == 0
    assert _cov_cache.budget(0, 30, held=0, free=0) is None


def _entry(cache: Path, key: str, root: str | None) -> Path:
    entry = cache / key
    (entry / "build").mkdir(parents=True)
    (entry / ".goh-running").write_text(f"{DEAD_PID}\n")
    (entry / ".goh-used").write_text("")
    if root is not None:
        (entry / ".goh-root").write_text(root + "\n")
    return entry


def test_an_orphan_goes_first_and_a_push_export_is_not_one(tmp_path) -> None:
    cache = tmp_path / "cov"
    live = tmp_path / "repo"
    live.mkdir()
    run_dir = tmp_path / "push" / "repo-abc"  # deleted after the push; its .source stays
    run_dir.parent.mkdir()
    Path(f"{run_dir}.source").write_text(f"{live}\n")
    _entry(cache, "cur", str(live))
    _entry(cache, "lane", str(tmp_path / "gone" / "wt"))
    _entry(cache, "push", str(run_dir / "repo"))
    _entry(cache, "here", str(live))
    _entry(cache, "old", None)  # from before the root was recorded
    gone = _cov_cache.prune(str(cache), "cur", gib=48, free=1 << 50)
    assert [Path(p).name for p in gone] == ["lane"]
    assert sorted(p.name for p in cache.iterdir()) == ["cur", "here", "old", "push"]


def test_the_run_log_reads_as_a_hit_rate(tmp_path) -> None:
    cache = tmp_path / "cov"
    _entry(cache, "a", "/r/a")
    _cov_cache.record(str(cache), "a", "/r/a", hit=False)
    _cov_cache.record(str(cache), "a", "/r/a", hit=True)
    assert _cov_cache.report(str(cache)).startswith("1/2 runs hit (50%); 1 repos")
    assert _cov_cache.main(["nope"]) == 2
