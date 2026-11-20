"""An enqueue in its pre-step, visible to the lander and to `--wait` (GOH.IDLEYIELDPRE,
GOH.WAITUNFILED).

`goh land --enqueue --land` runs the land pre-step (lib/land_prestep.py, 8-10 min under load)
BEFORE it files a request, so for those minutes the queue held nothing: the idle full suite
(lib/idle_suite.py) kept running beside three lanes' pre-steps at load 49-57 (2026-11-17 22:59),
and a `--wait ID` started beside its own enqueue exited 2, "no request".

    mark(q, item)   at the pre-step's start: `<q>/enqueuing/<pid>-<item>.json`, removed when the
                    enqueuing process exits (after the request is filed, so a reader sees one or
                    the other, never neither)
    live(q, item)   the markers whose enqueue is still running; ITEM narrows it to one item

STALE-SAFE. A marker counts only while its pid is alive and it is younger than its own `until`
(the pre-step's bound plus a margin): a killed enqueue, or a pid the system reused, leaves a
marker every reader treats as gone, and the reader deletes it. Pinned by
tests/test_landq_inflight.py.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import landq_state  # lib/, beside this file: one writer and one reader of every state record
import pid_owner

DIR = "enqueuing"
MARGIN_S = 600  # past the pre-step's own bound: the enqueue files or refuses well within it
PRE_TIMEOUT_S = 30 * 60  # lib/land_prestep.py TIMEOUT (GOH_ENQUEUE_PRE_TIMEOUT overrides both)


def slug(item: str) -> str:
    """The name part a request and a marker share (tools/landq.py enqueue)."""
    return item.replace("/", "_")


def mark(q: Path, item: str, pid: int | None = None, repo: str = "") -> Path:
    """Record that PID (this process) is enqueuing ITEM of REPO; removed when this process exits
    (REPO: the lander's batch window waits only for lands it could batch, lib/landq_window.py)."""
    pid = pid or os.getpid()
    bound = int(os.environ.get("GOH_ENQUEUE_PRE_TIMEOUT") or PRE_TIMEOUT_S) + MARGIN_S
    (q / DIR).mkdir(parents=True, exist_ok=True)
    path = q / DIR / f"{pid}-{slug(item)}.json"
    marker = {
        "pid": pid,
        "pid_stamp": pid_owner.stamp_line(pid),
        "item": item,
        "repo": repo,
        "at": time.time(),
        "until": time.time() + bound,
    }
    landq_state.write(path, marker, indent=None)
    atexit.register(clear, path)
    return path


def clear(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


def alive(pid: int, stamp: str | None = None) -> bool:
    """The enqueuer runs: by the start time its marker carries beside the pid (GOH.STRAYSIGNAL2),
    so a pid handed to another process once the enqueue died is no enqueue to wait for. A marker
    without one (an older goh's) is judged by its pid as it stands now."""
    return pid_owner.alive_line(stamp or str(pid))


def live(q: Path, item: str | None = None) -> list[dict]:
    """The enqueues still in their pre-step (all, or ITEM's); a stale marker is deleted."""
    out: list[dict] = []
    d = q / DIR
    for path in sorted(d.glob("*.json")) if d.is_dir() else []:
        m = landq_state.read(path) or {}
        try:
            pid, until = int(m["pid"]), float(m["until"])
        except (ValueError, KeyError, TypeError):
            continue  # being written, or not ours: never a reason to wait, never deleted here
        if not alive(pid, m.get("pid_stamp")) or time.time() > until:
            clear(path)
            continue
        if item is None or path.stem.partition("-")[2] == slug(item):
            out.append(m)
    return out
