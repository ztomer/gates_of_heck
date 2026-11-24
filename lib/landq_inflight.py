"""An enqueue in its pre-step, visible to the lander and to `--wait` (GOH.IDLEYIELDPRE,
GOH.WAITUNFILED).

`goh land --enqueue --land` runs the land pre-step (lib/land_prestep.py, 8-10 min under load)
BEFORE it files a request, so for those minutes the queue held nothing: the idle full suite
(lib/idle_suite.py) kept running beside three lanes' pre-steps at load 49-57 (2026-11-17 22:59),
and a `--wait ID` started beside its own enqueue exited 2, "no request".

    mark(q, item)   at the pre-step's start: a goh.db `requests` row in state enqueuing, named
                    `<q>/enqueuing/<pid>-<item>.json` (GOH.LANDQDB); the request the enqueue
                    files replaces it in one transaction (`mine`, tools/landq.py enqueue), so a
                    reader sees one or the other, never both and never neither; removed when the
                    enqueuing process exits without filing
    live(q, item)   the markers whose enqueue is still running; ITEM narrows it to one item

SUPERSEDED (GOH.REENQUEUECANCEL, lib/landq_supersede.py). A marker names what stops its enqueue:
the enqueuer's pid and start (`pid_stamp`), the pre-step's process group as it was stamped at its
spawn (`pre`, `note_child`), and the checkout slot it claimed (`slot`). A newer enqueue of the item,
or `goh land --cancel`, marks the marker superseded (`supersede_mark`) before it stops them;
`bail` ends an enqueuer that finds its own before it files anything.

STALE-SAFE. A marker counts only while its pid is alive and it is younger than its own `until`
(the pre-step's bound plus a margin): a killed enqueue, or a pid the system reused, leaves a
marker every reader treats as gone, and the reader deletes it. Pinned by
tests/test_landq_inflight.py.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import enqueue_witness  # lib/: a signalled enqueue names its sender (GOH.ENQUEUEVANISH)
import land_reds  # lib/: the land records store, the one door to done/ here (GOH.DBSEAM)
import landq_state  # lib/, beside this file: one writer and one reader of every state record
import pid_owner

DIR = "enqueuing"
MARGIN_S = 600  # past the pre-step's own bound: the enqueue files or refuses well within it
PRE_TIMEOUT_S = 45 * 60  # lib/land_prestep.py TIMEOUT, pinned equal (GOH_ENQUEUE_PRE_TIMEOUT: both)
_MINE: list[Path] = []  # the markers this process wrote


def slug(item: str) -> str:
    """The name part a request and a marker share (tools/landq.py enqueue)."""
    return item.replace("/", "_")


def mark(q: Path, item: str, pid: int | None = None, repo: str = "", slot: dict | None = None
         ) -> Path:  # fmt: skip
    """Record that PID (this process) is enqueuing ITEM of REPO; removed when this process exits
    (REPO: the lander's batch window waits only for lands it could batch, lib/landq_window.py).
    SLOT, the request: the checkout slot it claimed (`worktree`, `slot`), freed when superseded."""
    pid = pid or os.getpid()
    bound = int(os.environ.get("GOH_ENQUEUE_PRE_TIMEOUT") or PRE_TIMEOUT_S) + MARGIN_S
    run = f"{pid}-{slug(item)}"
    marker = {
        "pid": pid,
        "pid_stamp": pid_owner.stamp_line(pid),
        "item": item,
        "repo": repo,
        "at": time.time(),
        "until": time.time() + bound,
    }
    if slot and slot.get("slot"):
        marker["slot"] = {"worktree": slot["worktree"], "token": slot["slot"]}
    landq_state.drop(path := landq_state.name(q, DIR, run))  # a marker this pid left before
    path = landq_state.put(q, run, marker, DIR)
    atexit.register(clear, path)
    _MINE.append(path)
    return path


def note_child(pid: int) -> None:
    """The pre-step this process just spawned in its own session (PID leads its group), stamped
    onto this process's markers: what a superseding enqueue stops, by birth, never by name."""
    if _MINE:  # an enqueuer, mid pre-step: a signal that ends it says who sent it
        enqueue_witness.install()
    got = pid_owner.stamp(pid)
    for path in _MINE if got else ():
        if m := landq_state.get(path):
            m["pre"] = {"number": got.number, "born": list(got.born), "group": got.group}
            landq_state.update(path, m)


def bail() -> None:
    """End this enqueue, filing nothing, when a newer enqueue of its item (or --cancel) superseded
    it: the stop signal may not have landed yet, and a request for the replaced sha must never be
    filed (GOH.BINREACH's c36145ad)."""
    if any(superseded(p) for p in _MINE):
        sys.stderr.write("goh land: superseded by a newer enqueue (or --cancel): nothing filed\n")
        raise SystemExit(3)


def mine(q: Path) -> str | None:
    """The run of this process's marker in queue Q, which the request it files replaces."""
    for path in _MINE:
        if landq_state.queue_key(path.parent.parent) == landq_state.queue_key(q):
            return path.stem
    return None


def superseded(path: Path) -> bool:
    """A newer enqueue (or --cancel) stops the enqueue whose marker PATH names."""
    return bool((landq_state.get(path) or {}).get("superseded"))


def supersede_mark(path: Path) -> bool:
    """The marker PATH superseded, by this process: its enqueue must file nothing (`bail`)."""
    m = landq_state.get(path)
    return m is not None and landq_state.update(path, {**m, "superseded": os.getpid()})


def clear(path: Path) -> None:
    with contextlib.suppress(OSError, sqlite3.Error):
        landq_state.drop(path)


def alive(pid: int, stamp: str | None = None) -> bool:
    """The enqueuer runs: by the start time its marker carries beside the pid (GOH.STRAYSIGNAL2),
    so a pid handed to another process once the enqueue died is no enqueue to wait for. A marker
    without one (an older goh's) is judged by its pid as it stands now."""
    return pid_owner.alive_line(stamp or str(pid))


def live(q: Path, item: str | None = None) -> list[dict]:
    """The enqueues still in their pre-step (all, or ITEM's); a stale marker is deleted."""
    out: list[dict] = []
    for path, m in landq_state.rows(q, DIR):
        try:
            pid, until = int(m["pid"]), float(m["until"])
        except (ValueError, KeyError, TypeError):
            continue  # being written, or not ours: never a reason to wait, never deleted here
        if not alive(pid, m.get("pid_stamp")) or time.time() > until:
            if not m.get("superseded"):  # a supersede stops it on purpose
                died(q, m)
            clear(path)
            continue
        if item is None or path.stem.partition("-")[2] == slug(item):
            out.append(m)
    return out


def died(q: Path, m: dict) -> None:
    """A red done record for marker M's enqueue, gone from its pre-step with nothing filed: SIGKILL
    runs no cleanup, so the land used to vanish (no request, no record, an empty log;
    GOH.ENQUEUEVANISH). Written once, by the reader that finds it dead; never over a record of the
    item filed after the marker (the enqueue settled, then exited)."""
    item = str(m.get("item") or "")
    gone = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    how = "overran its bound" if alive(int(m.get("pid") or 0), m.get("pid_stamp")) else "died"
    stamp = m.get("pid_stamp") or "no stamp"
    why = (f"enqueue {how} in its pre-step: pid {m.get('pid')} ({stamp}) seen gone by {gone} "
           "with no verdict and no request filed; its pre-step log may be empty "
           "(GOH.ENQUEUEVANISH) -- enqueue again")  # fmt: skip
    rec = {"item": item, "repo": m.get("repo", ""), "mode": "land", "state": "red", "why": why,
           "at": time.time(), "recorded": gone, "sha": ""}  # fmt: skip
    land_reds.file_vanished(q, rec, slug(item), since=float(m.get("at") or 0))
