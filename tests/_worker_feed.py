"""The pre-step's Mac run hands the Linux worker more of its queue as the worker frees, in the
controller (GOH.REMOTECARRY; the pre-step's side and the rules: lib/remote_feed.py).

lib/remote_feed.py names a directory (ENV, taken out of the environment as this loads, so no session
a test starts reads it) and the controller answers there:

  plan.json   at the session's start: the nodes this tree's carry skips (tests/_land_verdicts.py),
              so the first share is split of what will really run
  ask.json    the pre-step's: the worker is free (`n`, the modules it may take, its pace). Answered
              between two tests (`offer`, around the scheduler's own `mark_test_complete`):
  reply-N.json  `want_s`: the Mac seconds the worker may take and still end when the Mac does, from
              what this run has done in how long and what its queue still holds; `nodes`: the units
              taken OUT of the queue for it, where the pre-step's own pool (`pool_s`) is too small.

A unit leaves the queue only after the reply naming it is on disk: a controller that dies between
the two leaves the tests in both shares, never in neither. A grouped unit, the queue's last two
units a worker, and a test not in the modules named stay here.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import _land_verdicts as lv
import _schedule
import pytest

ENV = "GATES_OF_HECK_LAND_FEED"
SESSION_S = 20.0  # a pytest session's bring-up on the Mac, for a run still to come
POLL_S = 0.5
MOST_BYTES = 60_000  # a share's ids travel as one word of a Linux command (128 KiB, quoted twice)
_dir = os.environ.pop(ENV, "")
_state: dict[str, float] = {}


def put(path: Path, doc: object) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    tmp.replace(path)


@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session) -> None:
    if _dir and lv._controller(session.config):
        plan = lv._state.get("plan") or {}
        put(Path(_dir) / "plan.json", {"skip": sorted(plan.get("skip", ()))})


def want(ask: dict, left_s: float, pace: float) -> float:
    """The Mac seconds (of the duration table) the worker may take: LEFT_S here and `after_s` in the
    run to come at PACE a second, against the worker's `overhead_s` and `k` seconds a Mac second; 0
    when the worker would be done no more than `least_s` sooner."""
    wall = (left_s + ask["after_s"]) / pace + (SESSION_S if ask["after_s"] else 0.0)
    room = wall - ask["overhead_s"]
    return room / (ask["k"] + 1.0 / pace) if room >= ask["least_s"] else 0.0


def pick(queue: dict[str, float], safe: set[str], need_s: float, longest_s: float,
         keep: int) -> list[str]:  # fmt: skip
    """The units of QUEUE (each one test, its seconds) worth NEED_S, costliest first: of the SAFE
    modules, none over LONGEST_S (it would end after the Mac), at least KEEP left behind."""
    out: list[str] = []
    room = MOST_BYTES
    for unit in sorted(queue, key=lambda u: -queue[u]):
        if need_s <= 0 or len(queue) - len(out) <= keep:
            break
        size = len(unit.encode()) + 1
        if queue[unit] <= longest_s and size <= room and _module(unit) in safe:
            out.append(unit)
            need_s, room = need_s - queue[unit], room - size
    return out


def _module(node: str) -> str:
    path = node.split("::")[0]
    return path[6:-3] if path.startswith("tests/") else ""


@pytest.hookimpl(optionalhook=True, wrapper=True, tryfirst=True)
def pytest_xdist_make_scheduler(config, log):
    sched = yield
    if not _dir or sched is None:
        return sched
    complete = sched.mark_test_complete

    def mark_test_complete(node, item_index, duration=0):
        offer(sched, sched.registered_collections[node][item_index])
        return complete(node, item_index, duration)

    sched.mark_test_complete = mark_test_complete
    return sched


def offer(sched, ended: str) -> None:
    """Between two tests (ENDED is done): the pre-step's ask, if there is one, answered."""
    if not _dir:
        return
    now = time.monotonic()
    known = _schedule._read.get(_schedule._KEY, {})
    plain = getattr(sched, "plain", {})  # a grouped test's id without its group

    def secs(node: str) -> float:
        return known.get(plain.get(node, node), _schedule.UNKNOWN_S)

    _state.setdefault("t0", now)
    _state["done"] = _state.get("done", 0.0) + secs(ended)
    if now - _state.get("looked", 0.0) < POLL_S:
        return
    _state["looked"] = now
    try:
        ask = json.loads((Path(_dir) / "ask.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    reply = Path(_dir) / f"reply-{ask['n']}.json"
    if reply.exists():
        return
    queue = {s: sum(map(secs, u)) for s, u in sched.workqueue.items() if list(u) == [s]}
    left = sum(secs(n) for u in sched.workqueue.values() for n in u)
    left += sum(secs(n) for w in sched.assigned_work.values() for u in w.values()
                for n, done in u.items() if not done)  # fmt: skip
    spent = now - _state["t0"]
    pace = max(0.5, _state["done"] / spent) if spent >= SESSION_S else float(ask["mac_width"])
    need = want(ask, left, pace) * ask["part"]
    longest = 0.5 * need * ask["k"] / ask["ratio"]
    units = pick(queue, set(ask["safe"]), need - ask["pool_s"], longest, 2 * len(sched.nodes))
    put(reply, {"want_s": need, "nodes": units})
    for unit in units:  # on disk first (module doc)
        del sched.workqueue[unit]
