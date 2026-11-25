#!/usr/bin/env python3
"""The pin sweep's pushes: each repo of one sweep's record, each gate under load.py
(GOH.PINPUSHGATE, GOH.PINSWEEP).

    lib/pin_push.py SWEEP RUN   # what lib/pin_sweep.py's supervisor runs, bounded (not by hand)

A script of its own, so `goh ship` and whatever else reads a sweep's record does not import what
the hold reads (lib/push_hold.py, the step closures): by that import 50 more test files were
judged, and run, as able to run any subcommand of the binary at any change to it
(tests/test_affected_binary_reach.py).
Pinned by tests/test_pin_sweep.py and tests/test_pin_sweep_daily.py.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import os
import sys
import time
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import consumer_pins  # lib/, beside this file
import pin_sweep
import push_hold
import push_sweep_rows as rows
import step_timings
import steps_store

from tui.lib import counted, err, info, ok


def run(path: Path, run_id: str) -> int:
    """The pushes: each repo of the record, the one waiting longest first, one at a time
    (GOH.LOADSOURCE), each outcome written as it ends."""
    rec = rows.read(path)
    if rec.get("run") != run_id or rec.get("status") != "running":
        return 1
    os.environ[step_timings.ENV] = str(pin_sweep.timings_path(path))
    os.environ.pop(step_timings.PARENT_ENV, None)
    consumer_pins.budget(float(rec["budget"]))
    logs = pin_sweep.log_dir(path)
    repos = {w: Path(r["repo"]) for w, r in rec["repos"].items() if r["status"] == "pending"}
    facts: dict[str, dict] = {w: {} for w in repos}

    def log(where: str) -> Path:
        return logs / f"{repos[where].name}.log"

    def job(where: str) -> tuple[str, str]:
        at, repo = time.time(), repos[where]
        rows.outcome(path, where, status="pushing", started=at, at=at)
        last = rows.verdict(repo)
        facts[where]["last"] = last
        hold = partial(push_hold.held, last, repo)
        return consumer_pins.push(repo, "", log(where), hold, facts[where])

    def said(where: str, status: str, why: str) -> None:
        got, repo, steps = facts[where], repos[where], None
        cols = {"status": status, "said": why, "at": time.time(),
                "seconds": got.get("seconds"), "slot_wait_s": got.get("slot_wait_s")}  # fmt: skip
        if status == "refused":
            cols["log"] = str(log(where))
            if got["refusal"].judged:
                cols["tip"] = push_hold.tip(repo)
                steps = push_hold.closures(got["refusal"].steps, repo)
        elif status == "held":
            cols["log"] = (got["last"] or {}).get("log")
        rows.outcome(path, where, steps, **cols)
        entry = {k: cols[k] for k in ("status", "said", "log") if cols.get(k)}
        pin_sweep.ledger(str(repo), where, None if status == "pushed" else
               {**entry, "at": pin_sweep.now(), "sweep": str(path)})  # fmt: skip
        (ok if status == "pushed" else err)(f"{where}: {why}")

    first = sorted(repos, key=lambda w: (consumer_pins.oldest(repos[w]), w))
    done = consumer_pins.sweep([(w, partial(job, w)) for w in first], said, row="push",
                               ok=("pushed", "held"), at_once=1)  # fmt: skip
    os.environ.pop(step_timings.ENV, None)
    # its rows are the table's (GOH.STEPTIMES)
    steps_store.ingest_all([str(pin_sweep.timings_path(path))])
    n = sum(v["status"] == "pushed" for v in done.values())
    held = sum(v["status"] == "held" for v in done.values())
    info(f"pin sweep: {n} of {counted(len(done), 'consumer')} pushed"
         + (f", {held} held" if held else ""))  # fmt: skip
    return 0 if n + held == len(done) else 1


def main(argv: list[str] | None = None) -> int:
    a = sys.argv[1:] if argv is None else argv
    if len(a) != 2:
        err("usage: lib/pin_push.py SWEEP RUN")
        return 2
    return run(Path(a[0]), a[1])


if __name__ == "__main__":
    sys.exit(main())
