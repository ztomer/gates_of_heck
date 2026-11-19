"""lib/state_bounds.py -- every directory goh keeps under its state root, each bounded or excused
(GOH.LANDLOGPRUNE).

THE CLASS. A directory that every run writes into and nothing prunes grows without bound:
`landq/logs` held 198 files (3.1 MB) on 2026-11-19 with no pruner anywhere in lib/ or tools/, and
since GOH.LANDLEAKS each land keeps its `<stem>.timings.jsonl` there as well. The same sweep found
`landq/done` (one record per attempt), `ship/<repo>` (607 entries for goh alone) and `run/` (2121
per-tree directories in four days) with no bound either.

    REGISTRY        every directory under the state root, relative to it: a `Bound` (this module
                    prunes it, by age and by count) or a `Kept` (not written per run, or bounded at
                    its own writer, which it names). `ship/*` is each repo's directory.
    prune(b, d)     D bounded by B; the paths removed. A RUN is a group of files (a land's log, its
                    batch gate's logs and their timings go together); a run touched within IDLE_S,
                    or a directory whose `*.lock` someone holds, is live and never removed.
    sweep(root)     every Bound in the registry pruned under ROOT. lib/land_prestep.py `discard`
                    runs it once per recorded land (about 48 a day), the queue's housekeeping.
    unbounded(root) the check: each directory with no bound -- unregistered and holding GROWTH or
                    more entries (one per run, growing), or registered as Bound and past it (its
                    pruner is not running). hooks/claude/estate_advisories.py names them at every
                    SessionStart, so a new per-run directory is seen the session it appears.

THE BOUNDS ARE MEASURED (2026-11-19): 49 lands a day on 11-17 and 11-18, 75 log groups and 72 done
records on 11-18 (1.5 runs per land). HISTORY_DAYS covers what the readers need: `goh land-profile`
reads done/ and logs/ for its stage table and splits it before/after a `--normal-since` time (a
week either side); `goh budget`'s median5 judges ledger rows, which lib/land_e2e.py backfills at
each record while the logs are fresh, so its history lives in timing/ledger.jsonl, never here; a
retry chain (land_e2e's) spans hours. The reuse audit reads its own store (reuse/), not these.

FILES (GOH.LEDGERBOUND) is the same table for the single append-only files: a `FileBound` (rows
older than DAYS by their STAMP field and past the newest LINES go, trimmed under the writer's lock
by `trim`) or a `FileKept` naming the writer that bounds it. Each bound names the readers whose
window it keeps. On 2026-11-19 six of them grew with no bound (timing/ledger.jsonl 1544 rows in 2.5
days, reuse/audit/shadow.jsonl 6478); `unbounded` names any unlisted `*.jsonl`/`*.log` beside them.

Pinned by tests/test_state_bounds.py and tests/test_state_files_bound.py.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import contextlib
import datetime as dt
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lock_order  # the one acquire path: a try-lock on a run directory's tree lock
import prune_kept  # its live-entry probe and its remover, one spelling
import state_append  # a ledger's writers' lock, the one a trim holds

LANDS_PER_DAY = 48  # measured: 49 on 2026-11-17 and on 11-18
RUNS_PER_LAND = 1.6  # measured: 75 log groups, 72 done records for 49 lands (11-18)
SHIPS_PER_DAY = 11  # measured: goh's 80 ship results in 7.6 days, the busiest repo
HISTORY_DAYS = 14
LAND_RUNS = math.ceil(LANDS_PER_DAY * RUNS_PER_LAND * HISTORY_DAYS)
IDLE_S = 3600  # a run that moved within the hour is in flight
GROWTH = 50  # an unregistered directory holding this many entries is written per run


@dataclass(frozen=True)
class Bound:
    days: float  # a run older than this goes
    keep: int  # the newest KEEP runs stay (within DAYS)
    run: str  # the regex whose match is a file's run; a name it does not match is never pruned
    why: str


@dataclass(frozen=True)
class Kept:
    why: str  # not written per run, or bounded where it is written (named)


@dataclass(frozen=True)
class Container:
    why: str  # its children are registered one by one


REGISTRY: dict[str, Bound | Kept | Container] = {
    "landq": Container("the landing queue (tools/landq.py)"),
    "landq/logs": Bound(
        HISTORY_DAYS,
        LAND_RUNS,
        r"^(\d{10,}-|idle-suite-[0-9a-f]+)",
        "a land's log, its batch gate's logs and their timings",
    ),
    "landq/done": Bound(HISTORY_DAYS, LAND_RUNS, r"^\d{10,}-.+\.json$", "one record per attempt"),
    "landq/pending": Kept("requests the lander drains into running/ and done/"),
    "landq/running": Kept("the request in flight, moved to done/ when recorded"),
    "landq/enqueuing": Kept("an enqueue in flight, filed into pending/"),
    "landq/pre": Kept("kept verdicts, reaped per record (lib/land_prestep.py reap)"),
    "landq/slots": Kept("one checkout per repo, reused (lib/land_slot.py)"),
    "landq/idle-suite": Kept("one worktree and last.json, reused (lib/idle_suite.py)"),
    "ship": Container("one directory per repo (tools/ship.py default_out)"),
    "ship/*": Bound(
        HISTORY_DAYS,
        SHIPS_PER_DAY * HISTORY_DAYS,
        r"^(\d{8}T\d{6}Z-[0-9a-f]+|v\d+\.\d+\.\d+)",
        "a ship's result and its step logs; suite-wt is reused",
    ),
    "run": Bound(
        3,
        2000,
        r"^.+-[0-9a-f]{16}$",
        "one directory per tree (lib/run_path.py): locks and staging, live while a run holds "
        "them; 506 made on 11-18",
    ),
    "reuse": Container("the verdict store (lib/reuse.py)"),
    "reuse/carry": Kept("pruned by TTL at each write batch (lib/reuse.py prune)"),
    "reuse/gate": Kept("pruned by TTL at each write batch (lib/reuse.py prune)"),
    "reuse/pushed": Kept("pruned by TTL at each write batch (lib/reuse.py prune)"),
    "reuse/profcarry": Kept("pruned by TTL at each write batch (lib/reuse.py prune)"),
    "reuse/profstore": Kept("the newest keys only (lib/prof_store.py prune)"),
    "reuse/audit": Kept("one record per reuse path (lib/reuse_audit.py)"),
    "load_sources": Kept("one file per day, pruned at write (lib/load_sources.py prune)"),
    "compact_nudge": Kept("one marker per session, pruned (hooks/claude/compact_nudge.py)"),
    "seeds": Kept("fixed names, one per seed"),
    "timing": Kept("one ledger file (lib/timing_ledger.py), bounded in FILES"),
}

LEDGER_DAYS = 35  # > HISTORY_DAYS + 1: a backfilled row outlives its source; a month for reviews
APPEND_SUFFIXES = (".jsonl", ".log")


@dataclass(frozen=True)
class FileBound:
    days: float | None  # a row older than this goes (by STAMP); None: by count only
    lines: int  # the newest LINES stay
    stamp: str  # the JSON field dating a row: epoch seconds, ISO time or YYYY-MM-DD; '': a text log
    lock: str  # "state_file" (lib/state_append.py), "reuse_audit" (its writer's), "" (in place)
    why: str  # its writer, and the readers whose window it keeps


@dataclass(frozen=True)
class FileKept:
    why: str  # bounded at its writer (named)


FILES: dict[str, FileBound | FileKept] = {
    "timing/ledger.jsonl": FileBound(
        LEDGER_DAYS,
        60000,
        "epoch",
        "state_file",
        "lib/timing_ledger.py, ~620 rows a day; read by goh budget (median of 5), goh land-profile "
        "(a week either side), and the backfills (land_e2e, land_attempts, budget_ships, "
        "cip_history) deduping on refs whose sources live HISTORY_DAYS",
    ),
    "landq/selection_misses.jsonl": FileBound(
        LEDGER_DAYS,
        20000,
        "day",
        "state_file",
        "lib/selection_miss.py; goh land --report per day, and replay() from logs/ (HISTORY_DAYS) "
        "dedupes against it",
    ),
    "lane-starts.jsonl": FileBound(
        28,
        20000,
        "at",
        "state_file",
        "hooks/claude/lane_room.py; read by the weekly usage review only",
    ),
    "reuse/audit/shadow.jsonl": FileBound(
        14,
        30000,
        "epoch",
        "reuse_audit",
        "lib/reuse_audit.py compare, ~3000 rows a day; the verdicts live in its per-path records, "
        "this is their evidence for a review",
    ),
    "landq/lander.log": FileBound(
        None,
        20000,
        "",
        "",
        "tools/landq.py's lander stdout (~300 lines a day), trimmed in place by the lander itself "
        "(the sweep runs in it); read by people: each land's own log is in logs/",
    ),
    "estate_audit.log": FileBound(
        None,
        5000,
        "",
        "",
        "the estate audit's launchd stdout (scripts/estate_audit.py, daily), trimmed in place; "
        "read by people: its verdicts are estate_audit.json",
    ),
    "reuse/profstore/ledger.jsonl": FileKept(
        "the newest ROWS kept at each write (lib/prof_store.py note)"
    ),
    "load_sources/*": FileKept("one file per day, pruned at write (lib/load_sources.py prune)"),
}


def _file_rule(rel: str) -> FileBound | FileKept | None:
    if rel in FILES:
        return FILES[rel]
    return FILES.get(f"{rel.rpartition('/')[0]}/*") if "/" in rel else None


def _dated(line: bytes, stamp: str) -> float | None:
    """LINE's time by its STAMP field (epoch seconds), or None when it has none."""
    try:
        v = json.loads(line).get(stamp)
    except (ValueError, AttributeError):
        return None
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str):
        with contextlib.suppress(ValueError):
            t = dt.datetime.fromisoformat(v)
            return (t if t.tzinfo else t.replace(tzinfo=dt.UTC)).timestamp()
    return None


def _kept(rule: FileBound, lines: list[bytes], now: float) -> list[bytes]:
    if rule.days is not None and rule.stamp:
        cut = now - rule.days * 86400
        lines = [ln for ln in lines if (t := _dated(ln, rule.stamp)) is None or t >= cut]
    return lines[-rule.lines :]


def _rewrite(rule: FileBound, path: Path, now: float) -> int:
    """PATH's kept tail renamed over it (its lock held); the lines removed."""
    try:
        lines = path.read_bytes().splitlines(keepends=True)
    except OSError:
        return 0
    keep = _kept(rule, lines, now)
    if len(keep) == len(lines):
        return 0
    tmp = path.with_name(f".{path.name}.trim-{os.getpid()}")
    tmp.write_bytes(b"".join(keep))
    os.replace(tmp, path)
    return len(lines) - len(keep)


def trim(rule: FileBound, path: Path, now: float | None = None) -> int:
    """PATH bounded by RULE, under its writers' lock (a try-lock: a held one is left for the next
    sweep); the lines removed. A file held open by its writer (lock '') is rewritten in place."""
    now = time.time() if now is None else now
    if rule.lock == "state_file":
        with state_append.trimming(path) as got:
            return _rewrite(rule, path, now) if got else 0
    if rule.lock == "reuse_audit":
        with open(path.parent / ".lock", "a") as lk:
            try:
                lock_order.flock(lk, "reuse_audit", wait=False)
            except OSError:
                return 0
            return _rewrite(rule, path, now)
    try:
        with open(path, "r+b") as fh:
            lines = fh.read().splitlines(keepends=True)
            keep = _kept(rule, lines, now)
            if len(keep) == len(lines):
                return 0
            fh.seek(0)
            fh.write(b"".join(keep))
            fh.truncate()
    except OSError:
        return 0
    return len(lines) - len(keep)


def _file_over(rule: FileBound, path: Path, now: float) -> str:
    """Why PATH is past its bound (its trim is not running), or ''."""
    try:
        lines = path.read_bytes().splitlines()
    except OSError:
        return ""
    if len(lines) > rule.lines * 1.25:
        return f"{len(lines)} lines, past its bound of {rule.lines}"
    if rule.days is not None and rule.stamp and lines:
        t = _dated(lines[0], rule.stamp)
        if t is not None and now - t > (rule.days + 1) * 86400:
            return f"a row {(now - t) / 86400:.0f} days old, past its bound of {rule.days:g}"
    return ""


def state_root() -> Path:
    """$XDG_STATE_HOME/goh, else ~/.local/state/goh (the root lib/run_path.py's run/ lives in)."""
    state = os.environ.get("XDG_STATE_HOME", "")
    return (Path(state) if state.startswith("/") else Path.home() / ".local" / "state") / "goh"


def _held(path: Path) -> bool:
    """Whether someone holds a `*.lock` file in directory PATH (a lock's mtime does not move while
    it is held, so only asking the lock tells). A run directory's lock is its tree's
    (lib/tree_lock.sh), asked by a try-lock through lib/lock_order.py: it never waits."""
    with contextlib.suppress(OSError):
        for lock in path.glob("*.lock"):
            try:
                fd = os.open(lock, os.O_RDONLY)
            except OSError:
                continue
            try:
                lock_order.flock(fd, "tree", wait=False)
                lock_order.unlock(fd)
            except OSError:
                return True
            finally:
                os.close(fd)
    return False


def _moved(path: Path) -> float | None:
    """PATH's newest activity (a directory's top levels, prune_kept's probe); None when gone."""
    try:
        st = path.lstat()
    except OSError:
        return None
    isdir = path.is_dir() and not path.is_symlink()
    return prune_kept._activity(str(path), st.st_mtime) if isdir else st.st_mtime


def prune(bound: Bound, directory: Path, now: float | None = None) -> list[Path]:
    """DIRECTORY bounded by BOUND: every run older than its days or past its newest `keep` removed,
    a run's files together; never a live one. The paths removed."""
    now = time.time() if now is None else now
    runs: dict[str, list[tuple[Path, float]]] = {}
    with contextlib.suppress(OSError):
        for entry in directory.iterdir():
            m = re.match(bound.run, entry.name)
            moved = _moved(entry) if m else None
            if m and moved is not None:
                runs.setdefault(m.group(0), []).append((entry, moved))
    order = sorted(runs.values(), key=lambda fs: -max(t for _, t in fs))
    removed: list[Path] = []
    for i, files in enumerate(order):
        newest = max(t for _, t in files)
        if now - newest < IDLE_S or any(p.is_dir() and _held(p) for p, _ in files):
            continue
        if i < bound.keep and now - newest < bound.days * 86400:
            continue
        removed += [p for p, _ in files if prune_kept._remove(str(p))]
    return removed


def _dirs(root: Path, rel: str) -> list[Path]:
    """The directories REL names under ROOT (`ship/*`: each repo's)."""
    if not rel.endswith("/*"):
        return [root / rel] if (root / rel).is_dir() else []
    parent = root / rel[:-2]
    with contextlib.suppress(OSError):
        return sorted(p for p in parent.iterdir() if p.is_dir() and not p.is_symlink())
    return []


def sweep(root: Path | None = None, now: float | None = None) -> list[Path]:
    """Every Bound in the registry pruned under ROOT (the state root); the paths removed.
    Housekeeping: never raises."""
    root = state_root() if root is None else root
    removed: list[Path] = []
    for rel, rule in REGISTRY.items():
        if isinstance(rule, Bound):
            for d in _dirs(root, rel):
                with contextlib.suppress(OSError):
                    removed += prune(rule, d, now)
    for rel, frule in FILES.items():
        if isinstance(frule, FileBound) and (root / rel).is_file():
            with contextlib.suppress(OSError, RuntimeError):
                if trim(frule, root / rel, now):
                    removed.append(root / rel)
    return removed


def _rule(rel: str) -> Bound | Kept | Container | None:
    if rel in REGISTRY:
        return REGISTRY[rel]
    return REGISTRY.get(f"{rel.rpartition('/')[0]}/*") if "/" in rel else None


def _over(rule: Bound, d: Path, now: float) -> str:
    """Why D is past its bound (its pruner is not running), or ''."""
    runs: dict[str, float] = {}
    with contextlib.suppress(OSError):
        for e in d.iterdir():
            m = re.match(rule.run, e.name)
            t = _moved(e) if m else None
            if m and t is not None:
                runs[m.group(0)] = max(runs.get(m.group(0), 0.0), t)
    if len(runs) > rule.keep:
        return f"{len(runs)} runs, past its bound of {rule.keep}"
    oldest = min(runs.values(), default=now)
    if now - oldest > (rule.days + 1) * 86400:
        return f"a run {(now - oldest) / 86400:.0f} days old, past its bound of {rule.days:g}"
    return ""


def unbounded(root: Path | None = None, now: float | None = None) -> list[str]:
    """Each directory or append-only file under ROOT with no bound, one line each: an unregistered
    directory with GROWTH or more entries, an unlisted `*.jsonl`/`*.log` file, or either past its
    bound. A registered Kept directory's entries are not counted; its own files are."""
    root = state_root() if root is None else root
    now = time.time() if now is None else now
    out: list[str] = _files(root, root, now)
    level = [root / n for n in _children(root)]
    while level:
        nxt = []
        for d in level:
            rel = d.relative_to(root).as_posix()
            rule = _rule(rel)
            if isinstance(rule, Container | Kept):
                out += _files(root, d, now)
            if isinstance(rule, Container):
                nxt += [d / n for n in _children(d)]
            elif isinstance(rule, Bound):
                if why := _over(rule, d, now):
                    out.append(f"{rel}: {why}")
            elif rule is None and len(_children(d, every=True)) >= GROWTH:
                out.append(f"{rel}: {len(_children(d, every=True))} entries and no bound")
        level = nxt
    return out


def _files(root: Path, d: Path, now: float) -> list[str]:
    """D's own append-only files with no bound: unlisted and not empty, or a FileBound past it
    (a Bound directory's files are its runs, pruned whole: never asked here)."""
    out = []
    with contextlib.suppress(OSError):
        for e in sorted(os.scandir(d), key=lambda e: e.name):
            if not e.name.endswith(APPEND_SUFFIXES) or not e.is_file(follow_symlinks=False):
                continue
            rel = Path(e.path).relative_to(root).as_posix()
            rule = _file_rule(rel)
            if rule is None and e.stat().st_size:
                out.append(f"{rel}: an append-only file and no bound")
            elif isinstance(rule, FileBound) and (why := _file_over(rule, Path(e.path), now)):
                out.append(f"{rel}: {why}")
    return out


def _children(d: Path, every: bool = False) -> list[str]:
    """D's subdirectories' names (EVERY: all its entries'); [] when D is unreadable."""
    try:
        return sorted(e.name for e in os.scandir(d)
                      if every or e.is_dir(follow_symlinks=False))  # fmt: skip
    except OSError:
        return []


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tui.lib import err, info, ok

    args = sys.argv[1:]
    where = Path(args[1]) if len(args) > 1 else None
    if args[:1] == ["sweep"]:
        for p in sweep(where):
            info(f"removed {p}")
        sys.exit(0)
    if args[:1] == ["check"]:
        lines = unbounded(where)
        for line in lines:
            err(line)
        if not lines:
            ok("every per-run state directory and append-only file is bounded")
        sys.exit(1 if lines else 0)
    print("usage: state_bounds.py sweep|check [ROOT]", file=sys.stderr)
    sys.exit(2)
