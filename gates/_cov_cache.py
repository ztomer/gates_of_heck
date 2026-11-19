"""The rust coverage build cache's budget, sized from the estate's working set (GOH.COVCACHECHURN).

`~/.cache/goh/llvm-cov/<key>/` holds one instrumented build per repo root (gates/_coverage_rust.sh).
Its budget was 8 GiB, a guess. A push sweep visits the estate's active Rust repos in turn, every
~1.5-2 h, and least-recently-used eviction against a budget smaller than the set a sweep cycles
through is the textbook worst case: each entry is evicted just before its repo comes round again,
so EVERY push built cold (the cache held only builds used in the last ~70 minutes, none of the
pushers of the previous sweep; 2026-11-18). The budget is now the measured working set with
headroom (DEFAULT_GIB, the table in tests/test_cov_cache_budget.py), under two bounds:

* the DISK: the cache never grows into the floor the lanes keep free (GOH_LANE_MIN_FREE_GIB, plus
  RESERVE_GIB for the build in flight); when the volume is short, the budget shrinks to fit;
* ORPHANS go first: an entry whose repo root is gone (a lane's worktree, a private push export)
  can never be hit again, so it is evicted once idle, whatever the budget. A push export's root is
  deleted after every push but its `<run dir>.source` stays beside it (gates/push_gate.sh): that
  entry is live. An entry with no `.goh-root` (from before this rule) is never called an orphan.

Every run appends one row to `.runs.jsonl` in the cache (key, root, hit, bytes): the hit rate per
run is the metric, read by `python3 gates/_cov_cache.py report [CACHE]`. A run HITS when its
entry's build dir existed when it started.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _cl
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _cl.suppress(ModuleNotFoundError):  # a copy outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)

import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _head_cache  # gates/, beside this file

DEFAULT_GIB = 40.0  # the measured working set (26.3 GiB, tests/test_cov_cache_budget.py) + 52 %
RESERVE_GIB = 10.0  # one more build in flight on top of the lanes' disk floor
MIN_FREE_GIB = 30.0  # GOH_LANE_MIN_FREE_GIB's default (lib/capacity.py)
ROOT = ".goh-root"
RUNS = ".runs.jsonl"
RUNS_MAX_BYTES = 256 << 10  # one older generation kept as .runs.jsonl.1
KEEP = 200
GRACE_S = 600
MARKER = ".goh-running"
GIB = 1 << 30


def orphaned(entry: str) -> bool:
    """The entry's repo root is gone and no push export names it: nothing can reuse the build."""
    try:
        with open(os.path.join(entry, ROOT), encoding="utf-8") as handle:
            root = handle.read().strip()
    except OSError:
        return False
    if not root or os.path.isdir(root):
        return False
    return not os.path.exists(os.path.dirname(root) + ".source")


def budget(gib: float, min_free_gib: float, held: int, free: int) -> int | None:
    """Bytes the cache may hold: `gib`, or less when the volume's free space minus the floor
    cannot take it. None when `gib` is 0 (no byte budget)."""
    if gib <= 0:
        return None
    room = held + free - int((min_free_gib + RESERVE_GIB) * GIB)
    return max(0, min(int(gib * GIB), room))


def _orphan_gone(path: str, now: float) -> bool:
    """Evict `path` if it is an orphan and idle (gates/_head_cache.py's rule, under its lock)."""
    if not orphaned(path):
        return False
    used = _head_cache.last_use(path)
    idle = _head_cache._idle(path, used, now, GRACE_S, MARKER)
    return idle and _head_cache._evict(path, MARKER, _head_cache._rmtree)


def _sized(size):
    memo: dict[str, int] = {}

    def get(path: str) -> int:
        if path not in memo:
            memo[path] = size(path)
        return memo[path]

    return get


def prune(
    cache: str,
    current: str,
    gib: float = DEFAULT_GIB,
    min_free_gib: float = MIN_FREE_GIB,
    now: float | None = None,
    size=_head_cache.tree_bytes,
    free: int | None = None,
) -> list[str]:
    """Orphans first, then gates/_head_cache.py's policy against the sized budget."""
    size = _sized(size)
    clock = time.time() if now is None else now
    gone, entries = [], []
    for entry in os.scandir(cache):
        if not entry.is_dir(follow_symlinks=False) or entry.name.startswith("."):
            continue
        if entry.name != current and _orphan_gone(entry.path, clock):
            gone.append(entry.path)
        else:
            entries.append(entry.path)
    held = sum(size(p) for p in entries)
    free = shutil.disk_usage(cache).free if free is None else free
    return gone + _head_cache.prune(
        cache, current, now=now, keep=KEEP, max_bytes=budget(gib, min_free_gib, held, free),
        grace=GRACE_S, marker=MARKER, size=size,
    )  # fmt: skip


def record(cache: str, key: str, root: str, hit: bool) -> None:
    """One row per run; the file rotates to one older generation past RUNS_MAX_BYTES."""
    path = os.path.join(cache, RUNS)
    row = {
        "at": round(time.time(), 1),
        "key": key,
        "root": root,
        "hit": hit,
        "bytes": _head_cache.tree_bytes(os.path.join(cache, key)),
    }
    try:
        if os.path.getsize(path) > RUNS_MAX_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode())
    finally:
        os.close(fd)


def runs(cache: str) -> list[dict]:
    rows = []
    for name in (RUNS + ".1", RUNS):
        try:
            with open(os.path.join(cache, name), encoding="utf-8") as handle:
                for line in handle:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            continue
    return rows


def report(cache: str) -> str:
    rows = runs(cache)
    if not rows:
        return f"no coverage runs recorded in {cache}/{RUNS}"
    hits = sum(1 for r in rows if r.get("hit"))
    sizes: dict[str, int] = {}
    for r in rows:
        sizes[r.get("key", "")] = int(r.get("bytes", 0))
    return (
        f"{hits}/{len(rows)} runs hit ({100 * hits / len(rows):.0f}%); "
        f"{len(sizes)} repos, working set {sum(sizes.values()) / GIB:.1f} GiB"
    )


def main(argv: list[str]) -> int:
    if argv[:1] == ["report"]:
        cache = argv[1] if len(argv) > 1 else os.path.join(
            os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "goh", "llvm-cov"
        )  # fmt: skip
        print(report(cache))
        return 0
    print("usage: _cov_cache.py report [CACHE]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
