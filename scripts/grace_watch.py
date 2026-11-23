#!/usr/bin/env python3
"""grace_watch.py -- a grace end is judged on a schedule, never only at a release (GOH.GRACEWATCH).

The release canary refuses a release within 7 days of a grace end that would turn a consumer red
(GOH.CANARYMISS, lib/canary_grace.py), but it runs only at a release: with no goh release in a
grace's last week, consumers still go red at its end with nobody told. This is that pass with no
release: the goh consumers run NOW (this checkout's HEAD), over every consumer of the estate, once
under the day and once at GOH_GRACE=over, whenever any grace end of the tree (every
`pub const OVER_FROM` under crates/goh/src: the shared one and each scanner's own) falls within
`canary_grace.WINDOW_S`. A step red only at the end is a finding, named per consumer with what the
step found -- an undeclared floor reads `GOH_<X>_MIN_FILES=N` there. Out of the window it sweeps
nothing and says which end is next.

The record is `grace_watch` in the estate state file; hooks/claude/estate_advisories.py says it at
every session start. scripts/estate_audit.py runs this after its own audit, so the daily launchd
job is its schedule.

    python3 scripts/grace_watch.py            # judge, record, print; exit 1 when the end is red

`--bin` names the binary outright and `--over-from` the grace end (fixtures, in tests); the binary's
GOH_DIR is then the `--goh` tree.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_canary as canary  # the sweep, the sides and the consumers: one implementation

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tui.lib import counted, err, info, ok

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import canary_grace
import canary_memo
import estate_registry
from canary_sides import export, resolve

KEY = "grace_watch"


def _side(args: argparse.Namespace, tmp: Path) -> tuple[canary.Side | None, str]:
    if args.bin:
        return canary.Side("installed", args.bin, args.goh, args.goh), ""
    native, why = resolve(args.goh, "HEAD")
    tree = tmp / "head"
    problem = why or export(args.goh, "HEAD", tree)
    return (None, problem) if problem else (canary.Side("installed", native, tree, args.goh), "")


def run(args: argparse.Namespace, now: float) -> tuple[dict, int]:
    rec: dict = {"ran_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
    end = args.over_from if args.over_from is not None else canary_grace.next_end(args.goh, now)
    rec["over_from"] = canary_grace.day(end)
    if end is None or end - now > canary_grace.WINDOW_S:
        rec.update(ran=False, why="no grace end within 7 days" + (f" (next {rec['over_from']})"
                   if end else ""))  # fmt: skip
        return rec, 0
    repos, problems = canary.consumers(args.root, args.state, args.goh)
    rec.update(consumers=len(repos), problems=problems)
    if not repos:
        rec.update(ran=False, why=f"no consumer in the estate (root {args.root})")
        return rec, 0
    with tempfile.TemporaryDirectory(prefix="goh-grace-watch-") as tmp:
        side, why = _side(args, Path(tmp))
        if side is None:
            rec.update(ran=True, count=None, turned_red={}, errors=[f"the installed goh: {why}"])
            return rec, 1
        over = replace(side, label=f"{side.label} at GOH_GRACE=over", grace="over")
        old = canary_memo.records(args.state, KEY)
        memo = canary_memo.Memo(old, {"ran_at": rec["ran_at"]}, reuse=True)
        canary_memo.key_sides((side, over), lambda _d: end)
        got = canary.sweep(repos, args.root, side, side, args.jobs, over, memo)
    g = got["grace_over"]
    rec.update(ran=True, count=g["count"], turned_red=g["turned_red"], errors=g["errors"])
    rec["memo_records"] = got.get("memo_records", {})
    return rec, 1 if g["turned_red"] or g["errors"] else 0


def say(rec: dict) -> None:
    if not rec.get("ran"):
        info(f"grace watch: {rec.get('why')}")
        return
    when = rec.get("over_from") or "its date"
    for e in rec.get("errors") or []:
        err(f"grace watch could not judge at the grace end: {e}")
    turned = rec.get("turned_red") or {}
    if not turned:
        if not rec.get("errors"):
            ok(f"grace watch: the grace end ({when}) turns no consumer red")
        return
    err(
        f"grace watch: the grace end ({when}) turns {counted(len(turned), 'consumer')} red at push,"
    )
    err("  and no release has to happen first:")
    for where, flips in sorted(turned.items()):
        for f in flips:
            err(f"  {where}: {f['step']} -- seed or fix it before {when}")
            for line in f["found"]:
                info(f"      found: {line}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("GOH_ESTATE_ROOT", Path.home() / "Projects")),
    )
    ap.add_argument("--state", type=Path, default=estate_registry.state_file())
    ap.add_argument("--goh", type=Path, default=canary.ROOT, help="the goh consumers run")
    ap.add_argument("--bin", type=Path, help="that goh's binary (a fixture, in tests)")
    ap.add_argument("--jobs", type=int, default=canary.JOBS)
    ap.add_argument(
        "--over-from", type=int, help="the grace end, epoch seconds (a fixture, in tests)"
    )
    args = ap.parse_args()
    args.root, args.goh = args.root.resolve(), args.goh.resolve()
    rec, rc = run(args, time.time())
    if "memo_records" not in rec:
        rec["memo_records"] = canary_memo.records(args.state, KEY)
    estate_registry.update_state(args.state, lambda old: {**old, KEY: rec})
    say(rec)
    return rc


if __name__ == "__main__":
    sys.exit(main())
