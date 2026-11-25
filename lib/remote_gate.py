"""The land GATE's remote share (GOH.REMOTEGATE): the gate's suite sends the Linux-safe part of
what it would run to the remote worker, beside the Mac's run, and the worker's passes count for the
land.

OWNER, 2026-11-23: "the worker is mostly idle during landing, can't it help somewhat?" (the worker
at load 0.7 on 16 cores while the Mac sat at 72 on 16). Only the land PRE-STEP shared with it
(lib/remote_lane.py); the gate's suite, the slowest step of a land, ran wholly on the Mac.

* ONE PATH. The share is lib/remote_lane.py's: its list (tests/linux_safe.txt), its balance
  (`split` over the measured `Rates`, so a loaded worker takes less), its launcher (`launch`, the
  `goh remote-shard --no-local` the pre-step runs) and its junit reading. Only the unit differs:
  the gate sends whole test FILES, chosen from what the Mac would still run once the land's carry
  (tests/_land_verdicts.py) deselected what passed before; a file whose every known test is
  carried is not sent. The worker's own host is goh remote-shard's (GOH_REMOTE_HOST): a parameter.
* THE VERDICT (coordinator ruling, 2026-11-23: a land is not a release). A file the worker ran is
  deselected on the Mac, and its verdict is the worker's; a test that FAILED there is re-run on the
  Mac: red on both is red, and green on the Mac is a disagreement (its module leaves the list,
  below) -- on 2026-11-23 three linux_safe tests failed on Linux only, and a Linux-only red would
  have failed every land. A fallback (an unreachable or busy worker, a skip mismatch, no junit)
  runs the files on the Mac after its own run (`finish`'s `run_local`), never a red of the
  worker's own making.
* THE SHIP STILL RERUNS EVERYTHING ON THE MAC. The worker's passes never enter the verdict store as
  the Mac's: the run counts them as passed by ANOTHER verdict (tests/_land_verdicts.py `other`),
  so the tree's record is never `full`, and `goh ship` (GOH_SHIP_FULL_SUITE) runs the whole suite
  on the Mac before any tag. A land's verdict may be Linux's; a release's never is.
* THE SHADOW. One gate in SHADOW_EVERY (GOH_REMOTE_SHADOW=1 always, 0 never) deselects nothing:
  the Mac runs the share's files too, its verdict stands, and each node whose pass or fail differs
  between the two platforms puts its module in the disagreement ledger (goh.db's `linux_unsafe`,
  lib/remote_lane.py `unsafe`), which takes it out of tests/linux_safe.txt for every later split,
  by itself.
* WHEN. Only the land gate's whole run (the carry's GOH_LAND_VERDICTS_REUSE named, no narrowing),
  never the coverage step's instrumented run (a remote test leaves no Mac profile), never one shard
  of hosted CI's suite, never under GOH_LAND_REMOTE=off. The glue is tests/_land_remote.py.

The land GATE's remote share (GOH.REMOTEGATE): the gate's whole suite run (tests/_land_remote.py,
its pytest glue) sends the Linux-safe test files it still has to run after the carry to the worker
through `remote_lane`'s own split, launcher and junit reading; their files leave the Mac's run and
the worker's verdict counts for the land (a failure there is re-run on the Mac: red on both is red,
Mac-green is a disagreement; a fallback runs them on the Mac). The worker's passes are counted as
another verdict's, so the store's record is never `full` and the ship reruns everything on the Mac.
One gate in ten is a SHADOW (`GOH_REMOTE_SHADOW`): the Mac runs the files too, and a module the
platforms disagree on joins goh.db's `linux_unsafe` (`durations.py`), which `remote_lane.split`
reads.

`lib/remote_lane.py`: The land pre-step's remote share (GOH.REMOTEWIRE): the chosen tests whose
module `tests/linux_safe.txt` records run on the worker (`goh remote-shard --no-local`) beside the
Mac's, skip parity against `tests/mac_skips.txt`; the share sized each land from both sides'
measured pace (`Rates`, GOH.REMOTESHARE); passes kept apart in the record (`remote`), so the gate
reruns them on the Mac; a fallback or a worker-only failure is run on the Mac, whose verdict
stands. `record LINUX.xml [MAC.xml]` rewrites both lists (the Mac need run only the files the
worker skipped in). Invoked by `lib/pre_reuse.py`.
"""

from __future__ import annotations

import contextlib
import os
import pwd
import random
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from pathlib import Path

from lib import cache_rows, durations, proc_deadline, remote_saved
from lib.remote_lane import (
    FALLBACK,
    LINUX_SAFE,
    Share,
    _cleanup,
    cause,
    cost,
    kinds,
    launch,
    listed,
    measure,
    module,
    split,
    unfinished,
)

SHADOW_EVERY = 10
# THE WAIT IS BOUNDED: the Mac done, it waits for the worker at most this long (GOH_REMOTE_GATE_
# WAIT_S), then kills the share and runs its files itself. The balance means both sides end
# together; a worker far behind it (a hung ssh, a cold build at load 30) never holds a gate for
# remote-shard's own hour-long run timeout.
WAIT_S = 300.0


def off(reuse: bool, whole: bool, instrumented: bool, shard: bool, top: bool = True) -> str:
    """Why this run sends nothing to the worker; "" when it may. `top`: the session is
    tools/pytest.sh's top-level run (it alone is handed the duration table, tests/_schedule.py
    `take`), never a session a test started, which must not reach the real worker."""
    if os.environ.get("GOH_LAND_REMOTE", "on") == "off":
        return "GOH_LAND_REMOTE=off"
    if not top:
        return "a session inside the suite, not its top-level run"
    if not reuse or not whole:
        return "not a land gate's whole suite"
    if instrumented:
        return "the instrumented coverage run needs every profile on the Mac"
    return "one shard of hosted CI's suite" if shard else ""


def shadowed(draw: Callable[[int], int] = random.randrange) -> bool:
    """This gate is a shadow (GOH_REMOTE_SHADOW=1 always, 0 never, else one in SHADOW_EVERY)."""
    said = os.environ.get("GOH_REMOTE_SHADOW", "")
    return said == "1" or (said != "0" and draw(SHADOW_EVERY) == 0)


@contextlib.contextmanager
def table(path: Path | None, kept: str = "") -> Iterator[None]:
    """The suite's real duration table for the call (the session popped it, tests/_schedule.py
    `take`, the goh.db GOH_TEST_DURATIONS names) and the real goh-cache.db beside it (KEPT, popped
    by tests/_kept_store.py), so the balance reads, and `measure` writes, the rates beside it
    (lib/durations.py `duration_kv`) and the tests' seconds (lib/cache_rows.py `rates`)."""
    named = {"GOH_TEST_DURATIONS": str(path), cache_rows.KEPT_ENV: kept} if path else {}
    named = {k: v for k, v in named.items() if v}
    was = {k: os.environ.get(k) for k in named}
    os.environ.update(named)
    try:
        yield
    finally:
        for k, v in was.items():
            os.environ.pop(k, None) if v is None else os.environ.update({k: v})


def choose(root: Path, skip: set[str], durations: dict[str, float]) -> tuple[list[str], float]:
    """(the Linux-safe test files the worker takes, the Mac's estimated seconds meanwhile): the
    files go costliest first while the worker ends by the time the Mac does (`split`), each costed
    at what the Mac would still run of it once `skip` (the carry) is deselected."""
    left = {n: s for n, s in durations.items() if n not in skip}
    known = {n.split("::")[0] for n in durations}
    running = {n.split("::")[0] for n in left}
    files = sorted(
        f"tests/{m}.py"
        for m in listed(root, LINUX_SAFE)
        if (root / "tests" / f"{m}.py").is_file()
        and (f"tests/{m}.py" in running or f"tests/{m}.py" not in known)
    )
    chosen = set(files)
    others = sum(s for n, s in left.items() if n.split("::")[0] not in chosen)
    remote, local = split(root, files, left, mac_else=others, runner=True)
    return remote, others + sum(cost(local, left).values())


def start(root: Path, files: list[str], mac_s: float) -> Share:
    """The share, launched with the real home (the suite runs in its own, tests/suite_home.py)."""
    env = {**os.environ, "HOME": pwd.getpwuid(os.getuid()).pw_dir}
    return launch(root, files, mac_s, env)


def settle(root: Path, share: Share, clock: Callable[[], float] = time.time) -> tuple[str, dict]:
    """(remote|failed|fallback with its cause, the worker's verdict per node), the share's files
    removed; the paces folded into the rates (`measure`) as the pre-step's are."""
    mac_end = clock()
    late = ""
    got_rc = proc_deadline.wait(
        share.proc, float(os.environ.get("GOH_REMOTE_GATE_WAIT_S") or WAIT_S)
    )
    if got_rc is None:  # the house's deadline wait (lib/proc_deadline.py), never Popen's timeout
        share.proc.kill()
        share.proc.wait()
        got_rc, late = FALLBACK, "late"
    rc, share.ended = got_rc, (mac_end, remote_saved.ended(share.junit, clock()))  # both walls
    said = share.log.read_text(errors="replace")
    share.said = said
    got: dict[str, str] = {}
    lost: list[str] = []
    try:
        text = share.junit.read_text() if rc in (0, 1) else ""
        got = kinds(root, text) if text else {}
        if got:
            measure(root, share, text, mac_end)
            lost = unfinished(root, share.nodes, text, got, rc)
    except (OSError, ET.ParseError):
        got = {}
    _cleanup(share)
    if lost:  # its workers died: what did not run there runs on the Mac (GOH.READUNION)
        return "fallback: unfinished", got
    if rc == 1 and any(k == "failed" for k in got.values()):
        return "failed", got
    if rc == 0 and got:
        return "remote", got
    return f"fallback: {late or cause(said)}", got


def disagreements(mac: dict[str, bool], linux: dict[str, str]) -> dict[str, str]:
    """{module: why} for each module with a node that passed on one platform and failed on the
    other (a skip is no verdict)."""
    out: dict[str, str] = {}
    for node, passed in sorted(mac.items()):
        kind = linux.get(node)
        if kind in ("passed", "failed") and (kind == "passed") != passed:
            mac_said, linux_said = ("passed", "failed") if passed else ("failed", "passed")
            out.setdefault(module(node), f"{node} {mac_said} on the Mac, {linux_said} on Linux")
    return out


def record_unsafe(found: dict[str, str]) -> None:
    """The disagreeing modules join the ledger `split` reads (lib/remote_lane.py `unsafe`), in one
    goh.db transaction (lib/durations.py `update`): two shadows ending together keep both."""

    def add(was: object) -> dict:
        return {**(was if isinstance(was, dict) else {}), **found}

    durations.update("linux_unsafe", add)


def finish(root: Path, share: Share, shadow: bool, mac: dict[str, bool],
           run_local: Callable[[list[str]], int],
           clock: Callable[[], float] | None = None) -> tuple[bool, list[str]]:  # fmt: skip
    """The gate's verdict on the share: (green, the lines to say). A shadow's Mac run stands and
    only feeds the ledger; else the worker's failures are red, and a fallback runs the files on
    the Mac (`run_local`), whose exit decides. The last line is the share's figure: what it saved
    (lib/remote_saved.py), which tests/_land_remote.py keeps for the land's log."""
    clock = clock or time.time
    outcome, got = settle(root, share, clock)
    rerun = [0.0]

    def timed(files: list[str]) -> int:
        t = clock()
        rc = run_local(files)
        rerun[0] += clock() - t
        return rc

    green, lines = _verdict(share, shadow, mac, timed, outcome, got)
    est = 0.0 if shadow else share.est_s  # a shadow ran its files on the Mac too: nothing saved
    fig = remote_saved.figure("gate", share.said, len(share.nodes), outcome.split(":")[0],
                              share.started, *share.ended, est, rerun[0])  # fmt: skip
    return green, [*lines, remote_saved.line(fig)]


def _verdict(share: Share, shadow: bool, mac: dict[str, bool],
             run_local: Callable[[list[str]], int], outcome: str,
             got: dict[str, str]) -> tuple[bool, list[str]]:  # fmt: skip
    n = len(share.nodes)
    if shadow:
        found = disagreements(mac, got)
        if found:
            record_unsafe(found)
            return True, [
                f"remote shadow: {m} leaves {LINUX_SAFE} ({why})" for m, why in found.items()
            ]
        return True, [f"remote shadow: {n} files on the Mac and the worker agree ({outcome})"]
    if outcome == "failed":  # the Mac re-runs them: both red is red, Mac-green is a disagreement
        bad = sorted(node for node, kind in got.items() if kind == "failed")
        named = ", ".join(bad[:10])
        if run_local(bad) != 0:
            return False, [f"land gate: failed on the Linux worker and on the Mac: {named}"]
        found = {module(b): f"{b} failed on Linux, passed on the Mac" for b in bad}
        record_unsafe(found)
        return True, [f"land gate: failed only on the Linux worker, green on the Mac: {named} -- "
                      + f"{', '.join(sorted(found))} leave {LINUX_SAFE}"]  # fmt: skip
    if outcome == "remote":
        passed = sum(k == "passed" for k in got.values())
        said = (f"land gate: {n} Linux-safe files passed on the remote worker ({passed} tests): "
                "their verdicts count for this land; the ship reruns them on the Mac")  # fmt: skip
        return True, [said]
    rc = run_local(share.nodes)
    said = (
        f"land gate: the remote share fell back ({outcome}): the Mac ran its {n} files, exit {rc}"
    )
    return rc == 0, [said]
