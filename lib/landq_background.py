"""`goh land --enqueue --land` in the background (GOH.TOOLTIMEOUT, 2026-11-21).

WHY. The enqueue's pre-step (lib/land_prestep.py) runs up to 45 min, and the Bash tool's timeout
is 10 min at most: 78 Bash calls in 7 days sat past its then-120 s default and were moved to the
background unannounced. So without `--wait` or `--foreground` the enqueue

    1. refuses what refuses at once: REPO SHA or --ship beside --land, the main checkout,
       uncommitted tracked changes (lib/landq_request.py, its checks alone: no slot, no pre-step);
    2. re-runs itself detached with `--detached` (its own session, log `<q>/logs/enqueue-*.log`),
       where a red pre-step is recorded red like an early conflict (done/, loopctl), so the lane's
       `goh land --wait ITEM` reads it, exit 1 with the failing tests named;
    3. returns 0 once the child's in-flight marker is up (lib/landq_inflight.py), so a `--wait`
       started next follows the pre-step instead of finding nothing, and says all of this.

`--foreground` is the enqueue as it was (the pre-step here, its verdict the exit: lib/ruff_bump.py
reads it); `--enqueue ... --wait` runs here too, then blocks on the request it filed.
Pinned by tests/test_landq_background.py.
"""

from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys
import time
from pathlib import Path

land_prestep = importlib.import_module("land_prestep")  # lib/ and the root: tools/landq.py's path
landq_early = importlib.import_module("landq_early")
landq_inflight = importlib.import_module("landq_inflight")
landq_request = importlib.import_module("landq_request")
landq_supersede = importlib.import_module("landq_supersede")
_tui = importlib.import_module("tui.lib")
err, info = _tui.err, _tui.info
# A re-enqueue stops the item's earlier pre-step; --cancel on demand (GOH.REENQUEUECANCEL).
supersede, cancel = landq_supersede.supersede, landq_supersede.cancel

MARK_WAIT_S = 60  # the child imports, claims a slot and marks; far under this on a loaded box


def add_arguments(ap: argparse.ArgumentParser) -> None:
    """The enqueue-of-a-land flags, `--wait` (a mode alone, or the foreground enqueue's own wait)
    and `--foreground`; `--detached` is the background child's."""
    ap.add_argument("--land", metavar="WORKTREE", help="run tools/land.sh there (mode land)")
    ap.add_argument("--no-pre", metavar="REASON", nargs="?", const="",
                    help="skip the enqueue's pre-step run; a reason is required")  # fmt: skip
    ap.add_argument("--no-start", action="store_true", help="enqueue only; start no lander")
    ap.add_argument("--wait", metavar="ID|REQUEST", nargs="?", const="",
                    help="block until it finished (THE WAIT); with --enqueue, enqueue here and "
                         "block until that request finished")  # fmt: skip
    ap.add_argument("--foreground", action="store_true",
                    help="--enqueue --land: the pre-step here, its verdict the exit")  # fmt: skip
    ap.add_argument("--detached", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--full", action="store_true", help="a red's whole log (GOH_FULL=1, terse.py)")


def check(ap: argparse.ArgumentParser, args: argparse.Namespace) -> bool:
    """Refuse a malformed mix (argparse's exit 2); True when this enqueue goes to the background."""
    if args.full:  # GOH.TERSEOUT: every printer reads it, the detached child's env included
        os.environ["GOH_FULL"] = "1"
    modes = (args.enqueue is not None, args.drain, args.status, args.ruff_bump, args.report,
             args.check is not None, args.cancel is not None)  # fmt: skip
    if args.wait is not None and any(modes[1:]):
        ap.error("--wait goes alone (ID|REQUEST) or with --enqueue")
    if args.wait == "" and args.enqueue is None:
        ap.error("--wait needs ID|REQUEST (or comes with --enqueue)")
    if args.wait is None and not any(modes):
        ap.error(
            "one of --enqueue --drain --status --ruff-bump --wait --report --check --cancel is "
            "required"
        )
    quiet = args.wait is not None or args.foreground or args.detached
    return args.land is not None and args.no_pre is None and not quiet


def worktree_only(args: argparse.Namespace, q: Path | None = None) -> None:
    """Refuse REPO SHA or --ship beside --land; given the queue Q, then stop the item's earlier
    enqueue in its pre-step, before this one claims a slot (GOH.REENQUEUECANCEL), and mark this
    one in flight NOW: its slot, conflict check and guards come before the pre-step, and a
    `--wait ITEM` in those seconds returned the PREVIOUS attempt's record (GOH.WAITSTALE2)."""
    if args.enqueue or args.ship:
        raise landq_request.Refused("--land takes the worktree only: no REPO SHA, no --ship")
    if q is not None:
        supersede(q, args.item)
        if args.item:  # the pre-step's own mark rewrites this one with its repo and slot
            landq_inflight.mark(q, args.item)


def red(args: argparse.Namespace, req: dict, why: str, q: Path, enqueue, record) -> None:
    """The detached child's refusal, recorded red like an early conflict (lib/landq_early.py), so
    the lane's `--wait ITEM` reads it: nobody reads this process's exit."""
    if args.detached and req:
        out = {"sha": req["sha"], "repo": req["repo"], "mode": "land", "state": "red",
               "why": f"enqueue pre-step: {why.splitlines()[0]}"}  # fmt: skip
        landq_early.settle(q, enqueue(req, "early"), out, f"{why}\n", record)


def start(args: argparse.Namespace, given: list[str] | None, q: Path, landq: Path) -> int:
    """Refuse at once or start the detached enqueue (module doc); the exit the caller gets."""
    given = sys.argv[1:] if given is None else given
    try:
        worktree_only(args)
        req = landq_request.land_request(args.land, args.item, None)
    except landq_request.Refused as e:
        err(f"goh land: {e} -- nothing enqueued")
        return 2
    landq_supersede.supersede(q, req["item"])  # its old marker is no mark of this one's
    who = req["item"] or Path(req["repo"]).name
    log = q / "logs" / f"enqueue-{time.time_ns():020d}-{landq_inflight.slug(who)}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as out:
        child = subprocess.Popen([sys.executable, str(landq), *given, "--detached"],
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                 start_new_session=True, env=os.environ.copy())  # fmt: skip
    give_up = time.monotonic() + MARK_WAIT_S
    while child.poll() is None and not landq_inflight.live(q, who) and time.monotonic() < give_up:
        time.sleep(0.2)
    if child.poll() not in (None, 0):
        err(f"goh land: the enqueue exited {child.returncode} before its pre-step; log {log}")
        return child.returncode or 1
    info(
        f"goh land: the enqueue's pre-step runs long (up to {land_prestep.TIMEOUT // 60} min): "
        f"running in the background, log {log}; `goh land --wait {who}` blocks on its outcome "
        "(--wait here runs it in the foreground)"
    )
    return 0
