#!/usr/bin/env python3
"""lib/timing_ledger.py -- the ONE timing record: one row per measured run (GOH.GATEBUDGET), in
goh.db's `spans` (GOH.STEPTIMES).

    timing_ledger.py record --kind push --repo NAME --wall 412.5 [--cpu 980.2] [--load 6.1]
                            [--ok 1] [--ref ID] [--timings FILE] [--field key=value ...]
    timing_ledger.py path                  # where the rows are
    timing_ledger.py tail [N]              # the newest N rows

WHY. goh timed its work in five places, each in its own shape (GOH_TIMINGS jsonl per gate, a ship
result's step seconds, the load registry, landq's done/ records, tests/_schedule durations), so
"is the push gate still under ten minutes" had no answer and a slow gate was found by a person who
waited for it. This ledger is the one record of a SUBJECT's run -- a push gate, a land, a ship, its
release step -- and `lib/budget.py` (`goh budget`) is its reader. GOH.ONETIMING moves the other
writers onto this row shape: this file is the first writer, so it does not wait for that item.

A SUBJECT is `<repo> <kind>` (`goh push`, `koffee_big ship`) or a machine-wide `<kind>`. Every
writer defines its subject's START and END (lib/budget.py KINDS carries the sentence), so two
writers of one kind measure the same span.

THE ROW. v, at (UTC ISO), epoch, subject, repo, kind, wall_s, cpu_s (user+sys of the run's reaped
tree, absent when the writer could not read it), load1 (the box's 1-min load at the START),
load_end, ok (the run passed), ref (the run's identity, so a backfill never doubles a live row),
control_drift (the same-kind control's drift across the run, lib/step_timings.py; absent without
one), top_steps ([label, seconds] of the costliest steps, so a breach names the step that moved)
and any writer fields (`wait_lock_s`, `sha`, `item`).

WHERE. goh.db's `spans` and `span_steps` (lib/span_store.py). A `ship` and a `land attempt` are rows
of `ships` and `lands` already and are read from there (lib/timing_derived.py). `ledger.jsonl` is
the SPOOL alone: the line a writer leaves when goh.db is not there, is busy, or is one its code may
not write, and where older copies of goh still append. The next row that reaches goh.db takes the
spool's with it, commits, then empties it: a kill in between is drained again, adding nothing.

CONCURRENCY AND FAILURE. One transaction, or one `os.write` of one line under O_APPEND, under
PIPE_BUF. A write that fails both ways is dropped ON PURPOSE (the instrument is never the reason a
gate goes red), and `record` returns None so a test can see it.

The spool's root: GOH_TIMING_DIR (absolute), else $XDG_STATE_HOME/goh/timing, else
~/.local/state/goh/timing. A named one is its own ledger, in no goh.db but one GOH_DB names too.
Pinned by tests/test_timing_ledger.py, test_span_store.py.

GOH.GATEBUDGET: the ONE timing record (the first writer of the shape GOH.ONETIMING moves the others
onto): one row per measured run of a subject (`<repo> push`, `<repo> land`, `<repo> ship`,
`<repo> release`) in goh.db's `spans` (GOH.STEPTIMES; `ledger.jsonl` is its spool), with wall_s,
cpu_s (the run's reaped tree), load1, ok, ref, the gate's own control drift and its costliest
steps. A write that fails is dropped (the instrument is never the reason a gate goes red).
`record` is the shell's door (`timing_ledger.sh`), `row_for`/`append`/`read` Python's;
`timings_facts` reads control drift and top steps from a `GOH_TIMINGS` file
(`step_timings.control_drifts`).
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

import argparse
import contextlib
import datetime as dt
import json
import math
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # lib/, on the path from the line above
import span_store
import state_append  # the spool's lock, the one lib/state_bounds.py trims under
import state_paths
import timing_derived

DIR_ENV = "GOH_TIMING_DIR"
SCHEMA = span_store.SCHEMA
MAX_LINE = 4000  # under PIPE_BUF (4096 on macOS and Linux): an append is atomic
GOH_NAME = span_store.GOH_NAME
DAYS = 35  # read's window: a month for a review, and the land sources' 14 days twice over
FAULTS = (sqlite3.Error, OSError, RuntimeError)  # RuntimeError: lib/lock_order.py's refusal


def ledger_dir() -> Path:
    explicit = os.environ.get(DIR_ENV)
    if explicit:
        return Path(explicit)
    return state_paths.state_root() / "timing"


def ledger_path() -> Path:
    return ledger_dir() / "ledger.jsonl"


def db() -> Path | None:
    """The goh.db the rows are in; None when there is none, or a named spool stands alone."""
    at = goh_db.path()
    named = os.environ.get(DIR_ENV) and not os.environ.get("GOH_DB")
    return None if named or not at.is_file() else at


def where() -> Path:
    return db() or ledger_path()


repo_name = span_store.repo_name


def subject_of(repo: str, kind: str) -> str:
    return f"{repo} {kind}" if repo else kind


def _num(v: float | None, places: int = 1) -> float | None:
    return None if v is None else round(float(v), places)


def row_for(
    kind: str,
    repo: str,
    wall_s: float,
    *,
    cpu_s: float | None = None,
    load1: float | None = None,
    ok: bool = True,
    ref: str = "",
    control_drift: float | None = None,
    top_steps: list[list] | None = None,
    at: dt.datetime | None = None,
    load_end: float | None = None,
    read_load: bool = True,
    **extra: object,
) -> dict:
    """One ledger row; nothing is read from the box except the time and, unless given, the load."""
    now = at or dt.datetime.now(dt.UTC)
    if load_end is None and read_load:
        try:
            load_end = os.getloadavg()[0]
        except OSError:
            load_end = None
    row: dict = {
        "v": SCHEMA,
        "at": now.isoformat(timespec="seconds"),
        "epoch": round(now.timestamp(), 1),
        "subject": subject_of(repo, kind),
        "repo": repo,
        "kind": kind,
        "wall_s": _num(wall_s),
        "ok": bool(ok),
    }
    for key, val, places in (
        ("cpu_s", cpu_s, 1),
        ("load1", load1, 2),
        ("load_end", load_end, 2),
        ("control_drift", control_drift, 4),
    ):
        if val is not None:
            row[key] = _num(val, places)
    if ref:
        row["ref"] = ref
    if top_steps:
        row["top_steps"] = [[str(a)[:80], _num(b)] for a, b in top_steps[:5]]
    row.update({k: v for k, v in extra.items() if v is not None and k not in row})
    return row


def spooled(path: Path | None = None) -> list[dict]:
    """The spool's rows (PATH's), as written; a truncated or foreign line is skipped, counted by
    no one."""
    rows: list[dict] = []
    try:
        with (path or ledger_path()).open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("v") == SCHEMA and "subject" in row:
                    rows.append(row)
    except OSError:
        pass
    return rows


def _same(row: dict) -> tuple:
    return tuple(row.get(k) for k in ("kind", "repo", "epoch", "ref", "wall_s", "sha"))


def read(
    path: Path | None = None, kinds: tuple[str, ...] = (), repo: str | None = None
) -> list[dict]:
    """Every row of the last DAYS days, oldest first (PATH: that file's rows instead); KINDS' alone
    and REPO's (its subject name) alone when given."""
    if path is not None:
        return spooled(path)
    rows = spooled()  # before the spans: a row drained between the two reads is in both, not none
    if db() is not None:
        since = time.time() - DAYS * 86400
        kept: list[dict] = []
        with contextlib.suppress(*FAULTS), goh_db.known() as con:  # none in a file from before 0014
            kept = span_store.rows(con, since, kinds, repo)
        asked = tuple(k for k in timing_derived.KINDS if not kinds or k in kinds)
        made = timing_derived.rows(asked, since)
        seen = {_same(r) for r in kept}
        own = {(r["kind"], r["repo"], r["ref"]) for r in made}
        rows = [*(r for r in [*kept, *(r for r in rows if _same(r) not in seen)]
                  if (r["kind"], r["repo"], r.get("ref")) not in own), *made]  # fmt: skip
        rows.sort(key=lambda r: r["epoch"])
    return [r for r in rows if (not kinds or r.get("kind") in kinds)
            and (repo is None or r.get("repo") == repo)]  # fmt: skip


def p95(kind: str, repo: str, rows: list[dict] | None = None) -> tuple[float, int] | None:
    """REPO's KIND runs' 95th percentile, nearest rank, in seconds as its caller sat through them
    (the run plus its wait for a gate slot or the land lock), and how many runs it read; None with
    none (GOH.TOOLTIMEOUT: the push gate says how long it runs before it does)."""
    name = repo_name(repo)
    waits = sorted(
        float(r.get("wall_s") or 0) + float(r.get("slot_wait_s") or 0)
        + float(r.get("wait_lock_s") or 0)
        for r in (read(kinds=(kind,), repo=name) if rows is None else rows)
        if r.get("kind") == kind and r.get("repo") == name
    )  # fmt: skip
    if not waits:
        return None
    return waits[max(0, math.ceil(0.95 * len(waits)) - 1)], len(waits)


def _spool(row: dict, path: Path | None = None) -> bool:
    target = path or ledger_path()
    line = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8", "replace"
    )
    if len(line) > MAX_LINE:  # the optional detail goes first, never the measurement
        row = {k: v for k, v in row.items() if k != "top_steps"}
        line = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8", "replace"
        )[:MAX_LINE]
    try:
        state_append.append(target, line)
    except (OSError, RuntimeError):
        return False
    return True


def _put(rows: list[dict]) -> None:
    with goh_db.transaction() as con:
        for row in rows:
            if not timing_derived.has(con, row):
                span_store.put(con, row)


def drain() -> int:
    """The spool's rows into goh.db, then the spool empty; how many lines it held. Held alone
    throughout (a try-lock: a writer there now leaves it to the next row). Raises FAULTS."""
    spool = ledger_path()
    if db() is None or not spool.is_file() or not spool.stat().st_size:
        return 0
    with state_append.trimming(spool) as held:
        rows = spooled(spool) if held else []
        if held:
            _put(rows)
            os.truncate(spool, 0)  # after the commit: a kill between the two drains them again
    return len(rows)


def append(row: dict) -> bool:
    """ROW kept: in goh.db, else in the spool; False when neither took it (never raises)."""
    if db() is None:
        return _spool(row)
    try:
        drain()
        _put([row])
    except FAULTS:
        return _spool(row)
    return True


def known_refs(kind: str, repo: str) -> set[str]:
    return {r["ref"] for r in read(kinds=(kind,), repo=repo) if r.get("ref")}


def record(kind: str, repo: str, wall_s: float, **kw: object) -> dict | None:
    """Keep one run of `repo`'s `kind`; the row, or None when it was not written. A row whose
    `ref` is already in the ledger for this subject is not written twice (a resumed ship)."""
    ref = str(kw.get("ref") or "")
    if ref and ref in known_refs(kind, repo):
        return None
    return _kept(kind, repo, wall_s, **kw)


def _kept(kind: str, repo: str, wall_s: float, **kw: object) -> dict | None:
    row = row_for(kind, repo, wall_s, **kw)  # type: ignore[arg-type]
    return row if append(row) else None


def timings_facts(path: str) -> tuple[float | None, list[list]]:
    """(the worst same-kind control drift across the run, the costliest steps) read from a
    GOH_TIMINGS file (lib/step_timings.py); (None, []) for a missing or empty one."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import step_timings

    try:
        rows = step_timings.load(path)
    except OSError:
        return None, []
    drift = step_timings.worst_control_drift(rows)
    steps = [
        [r["label"], r["ms"] / 1000]
        for r in sorted(rows, key=lambda r: -r["ms"])
        if not r.get("control")
    ][:5]
    return drift, steps


TEXT_FIELDS = frozenset({"sha", "item", "slot_class", "pre_why", "suite_why"})  # never numbers


def _fields(pairs: list[str]) -> dict[str, object]:
    """`--field K=V` pairs; a number reads as one, except a TEXT_FIELDS value: a 12-character sha
    prefix of all digits is an identity, not 152013349298 (GOH.BUDGETSHAINT)."""
    out: dict[str, object] = {}
    for p in pairs:
        key, _, val = p.partition("=")
        if not key:
            continue
        if key in TEXT_FIELDS:
            out[key] = val
            continue
        try:
            out[key] = float(val) if "." in val else int(val)
        except ValueError:
            out[key] = val
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="timing_ledger.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record", help="append one run")
    rec.add_argument("--kind", required=True)
    rec.add_argument("--repo", default="", help="a path or a name; the gates checkout reads as goh")
    rec.add_argument("--wall", type=float, required=True, help="seconds, start to end")
    rec.add_argument("--cpu", type=float, default=None)
    rec.add_argument("--load", type=float, default=None, help="1-min load at the start")
    rec.add_argument("--ok", type=int, default=1)
    rec.add_argument("--ref", default="")
    rec.add_argument("--spool", default="", help="the kept GOH_TIMINGS file: its run is the row's")
    rec.add_argument("--timings", default="", help="a GOH_TIMINGS file: control drift, top steps")
    rec.add_argument("--field", action="append", default=[], metavar="K=V")
    sub.add_parser("path")
    pc = sub.add_parser(
        "p95", help="print `SECONDS RUNS` for a repo's kind, nothing when unmeasured"
    )
    pc.add_argument("--kind", required=True)
    pc.add_argument("--repo", required=True, help="a path or a name")
    tl = sub.add_parser("tail")
    tl.add_argument("n", nargs="?", type=int, default=10)
    a = ap.parse_args(argv)
    if a.cmd == "path":
        print(where())
        return 0
    if a.cmd == "p95":
        if got := p95(a.kind, a.repo):
            print(f"{got[0]:.0f} {got[1]}")
        return 0
    if a.cmd == "tail":
        for r in read()[-a.n :]:
            print(json.dumps(r, ensure_ascii=False))
        return 0
    drift, steps = timings_facts(a.timings) if a.timings else (None, [])
    if a.spool:  # a batch's gates are one run's spans, each kept (lib/steps_store.py named)
        import steps_store

        a.ref, write = steps_store.named(a.spool)[0], _kept
    else:
        write = record
    write(
        a.kind,
        repo_name(a.repo) if a.repo else "",
        a.wall,
        cpu_s=a.cpu,
        load1=a.load,
        ok=bool(a.ok),
        ref=a.ref,
        control_drift=drift,
        top_steps=steps,
        **_fields(a.field),
    )
    return 0  # the instrument is never the reason a gate goes red


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
