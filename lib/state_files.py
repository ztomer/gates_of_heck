"""lib/state_files.py -- the single append-only files under goh's state root, each bounded or
excused (GOH.LEDGERBOUND); lib/state_bounds.py's table for directories, beside it for files.

    FILES               each file, relative to the state root (`d/*`: every file in d): a
                        `FileBound` (rows older than DAYS by their STAMP field and past the newest
                        LINES go) or a `FileKept` naming the writer that bounds it. Each bound
                        names the readers whose window it keeps.
    trim(rule, path)    PATH bounded, under its writers' lock (lib/state_append.py's, or the
                        writer's own), a try-lock: a held one waits for the next sweep. A file
                        its writer holds open as stdout is rewritten in place.
    unbounded_in(r, d)  D's own `*.jsonl`/`*.log` files with no bound: unlisted and not empty, or
                        past it. lib/state_bounds.py sweep/unbounded call both.

On 2026-11-19 six of them grew with no bound (timing/ledger.jsonl 1544 rows in 2.5 days,
reuse/audit/shadow.jsonl 6478). Pinned by tests/test_state_files_bound.py.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lock_order  # the one acquire path
import state_append  # a ledger's writers' lock, the one a trim holds

HISTORY_DAYS = 14  # lib/state_bounds.py's: how long the land and ship sources live

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
    "landq/guards.jsonl": FileBound(
        HISTORY_DAYS,
        5000,
        "at",
        "state_file",
        "lib/land_check.py learn, one row per pre-step refusal naming a test; read by land "
        "--check's history and count beside done/ (HISTORY_DAYS)",
    ),
    "lane-starts.jsonl": FileBound(
        28,
        20000,
        "at",
        "state_file",
        "hooks/claude/lane_room.py; read by the weekly usage review only",
    ),
    "flakes/register.jsonl": FileBound(
        LEDGER_DAYS,
        20000,
        "epoch",
        "state_file",
        "lib/flakes.py record, one row per pass-on-retry; read by goh flakes and by a test's "
        "filing (its records of the last 14 days)",
    ),
    "flakes/by-test/*": FileBound(
        LEDGER_DAYS,
        500,
        "epoch",
        "state_file",
        "lib/flakes.py record, one test's record ids and filings; read by its 2-in-14-days rule",
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


def unbounded_in(root: Path, d: Path, now: float) -> list[str]:
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
