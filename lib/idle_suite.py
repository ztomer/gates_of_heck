"""lib/idle_suite.py -- the idle lander runs the full suite on main's tip (GOH.GATEBUDGET2).

OWNER, 2026-11-17 (the OpenClaw CI write-up, "Yes, file them"): of "a record whose carried tests
come from a full record counts as full" and "the lander runs the full suite when idle", take the
second. Since verdict carry a land's suite record is no full run (1727 tests carried, e2915305), so
the release kit (GOH_SHIP_FULL_SUITE=on, `reuse.py full-suite`) refused every ship until somebody
ran the whole suite by hand, 12+ minutes at load, off every path. Here the lander makes that record
when it has nothing to land, so it is on disk before a ship asks for it:

    run(q, live)   after the queue drained: when the main checkout of LIVE (the lander's own
                   repository) declares GOH_SHIP_FULL_SUITE=on and main's tip has no full suite
                   record, run the suite INSTRUMENTED (`INSTRUMENTED`: the coverage gate's Rust
                   mode, its external suite against the instrumented binary; GOH.COVCARRY) with
                   GOH_LAND_VERDICTS_RECORD=/dev/null (the store's record alone) in a detached
                   worktree at that tip,
                   `<q>/idle-suite/wt` (kept between runs: its build caches are warm).

IT NEVER DELAYS A LAND. The run takes its own gate slot through admission (`tools/load.py 0`, class
`work`: behind every land and ship), holds no lock a land takes (the lander's lock is released
before it starts, so an enqueue starts a lander at once), and is stopped -- its whole process group,
TERM then KILL -- the moment a request is pending: it yields, and the lander drains. An enqueue
still in its pre-step (lib/landq_inflight.py, GOH.IDLEYIELDPRE: at 22:59 on 2026-11-17 the suite ran
beside three lanes' 8-10 min pre-steps at load 49-57) stops it the same way, `deferred`: the lander
exits, and the enqueue starts a fresh one when it files. One idle run at
a time (`<q>/idle-suite/lock`); a tip already judged here (green or red, `last.json`) is not run
again until main moves. Off with GOH_LANDQ_IDLE_SUITE=off, and inside a goh suite (IN_SUITE). The
run's own `goh suite` row is the timing ledger's (tools/pytest.sh); its log is
`<q>/logs/idle-suite-<sha>.log`. Each green or red run is measured against the selection for the
diff since the previous run's tip (lib/selection_miss.py's shadow, GOH.SELECTAUDIT): never a skip.

Pinned by tests/test_idle_suite.py.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import killtree
import landq_inflight
import lock_order
import selection_miss
import ship_suite
from full_run import INSTRUMENTED, MAX, _git, declares_full, prepare  # lib/: shared, lock-free

ROOT = Path(__file__).resolve().parent.parent

OFF_ENV = "GOH_LANDQ_IDLE_SUITE"  # "off": the lander never runs it
POLL_S = 5.0  # how often a running suite looks for a pending request
# A lander started inside a goh suite (its runner's marker, the suite home's): a test's lander
# never starts the real suite, however its queue was driven (tests/suite_home.py MARKER).
IN_SUITE = ("GATES_OF_HECK_SUITE_RUNNER", "GATES_OF_HECK_SUITE_REAL_HOME")


def main_checkout(live: Path) -> Path | None:
    """The first `git worktree list` entry of LIVE's repository: its main checkout."""
    out = _git(live, "worktree", "list", "--porcelain")
    first = out.stdout.splitlines()[0] if out.returncode == 0 and out.stdout else ""
    return Path(first.removeprefix("worktree ")) if first.startswith("worktree ") else None


def default_argv() -> list[str]:
    return [sys.executable, str(ROOT / "tools" / "load.py"), "--background-ok", "idle full suite",
            "--max", MAX, "0", "--", "bash", "-c", INSTRUMENTED]  # fmt: skip


def log_of(q: Path, sha: str) -> Path:
    return q / "logs" / f"idle-suite-{sha[:12]}.log"


def pending(q: Path) -> bool:
    """A request is waiting or already running: an enqueue starts a lander of its own, which moves
    the request to running/ within milliseconds, so pending/ alone would miss it."""
    return any(any((q / d).glob("*.json")) for d in ("pending", "running"))


def enqueuing(q: Path) -> bool:
    """An enqueue is in its pre-step: a land on its way that has filed nothing yet."""
    return bool(landq_inflight.live(q))


def run(
    q: Path,
    live: Path,
    say: Callable[[str], None] = print,
    argv: list[str] | None = None,
    poll: float = POLL_S,
) -> str:
    """One idle run, if one is owed; its outcome: off, nothing, busy, yielded (a request is
    pending), deferred (an enqueue is in its pre-step), green, red, or failed (no worktree)."""
    if os.environ.get(OFF_ENV) == "off" or any(os.environ.get(k) for k in IN_SUITE):
        return "off"
    main = main_checkout(live)
    if main is None or not declares_full(main):
        return "nothing"
    sha = _git(main, "rev-parse", "HEAD").stdout.strip()
    state = q / "idle-suite"
    last_path = state / "last.json"
    try:
        last = json.loads(last_path.read_text())
    except (OSError, json.JSONDecodeError):
        last = {}
    if not sha or (last.get("sha") == sha and last.get("outcome") in ("green", "red")):
        return "nothing"
    if ship_suite.full_suite(main, sha)[0] is not None:
        return "nothing"
    state.mkdir(parents=True, exist_ok=True)
    fd = os.open(state / "lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            lock_order.flock(fd, "idle_suite", wait=False)  # never waits: one idle run
        except BlockingIOError:
            return "busy"
        outcome = _run_locked(q, main, state, sha, say, argv or default_argv(), poll)
        prev = str(last.get("sha") or "")  # GOH.SELECTAUDIT: the shadow since the last run
        if outcome in ("green", "red") and (
            why := selection_miss.shadow(q, main, prev, sha, log_of(q, sha))
        ):
            say(why)
    finally:
        os.close(fd)
    record = {"sha": sha, "outcome": outcome, "at": dt.datetime.now(dt.UTC).isoformat()}
    tmp = state / ".last.json.tmp"
    tmp.write_text(json.dumps(record) + "\n")
    tmp.replace(last_path)
    return outcome


def _run_locked(q: Path, main: Path, state: Path, sha: str, say: Callable[[str], None],
                argv: list[str], poll: float) -> str:  # fmt: skip
    if pending(q):
        return "yielded"
    if enqueuing(q):
        return "deferred"
    wt = state / "wt"
    why = prepare(main, wt, sha)
    if why:
        say(f"idle full suite: no worktree at {sha[:8]}: {why}")
        return "failed"
    log = log_of(q, sha)
    log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, GOH_LAND_VERDICTS_RECORD=os.devnull, GOH_GATE_CLASS="work")
    say(f"idle full suite on main {sha[:8]} in {wt} -> {log}")
    with log.open("ab") as out:
        proc = subprocess.Popen(argv, cwd=wt, env=env, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)  # fmt: skip
        while True:
            try:
                rc = proc.wait(timeout=poll)
                break
            except subprocess.TimeoutExpired:
                why = "yielded" if pending(q) else "deferred" if enqueuing(q) else ""
                if why:  # a land arrived, or is in its pre-step: it goes first, always
                    killtree.stop_group(killtree.session_pgid(proc), proc=proc)
                    proc.wait()
                    what = "a land is pending" if why == "yielded" else "an enqueue's pre-step runs"
                    say(f"idle full suite on {sha[:8]}: stopped, {what}")
                    return why
    say(f"idle full suite on {sha[:8]}: {'green' if rc == 0 else f'red (exit {rc})'}")
    return "green" if rc == 0 else "red"
