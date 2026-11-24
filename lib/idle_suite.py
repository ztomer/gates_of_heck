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

IT IS THE SHIP'S RUN, NEVER BESIDE IT (GOH.IDLESUITERETIRE). On v0.149.2 (2026-11-22) this run began
38 s before the ship's run of the same tree; both ran at once and the ship's release land waited
388 s for the slot this one held. So it judges as the ship does (`full_run.judge_env`: the diff
coverage based at the last version tag, kept as `last.json` `base`), a ship adopts a finished green
run of its tip and base (`full_run.adoptable`, lib/ship_full_run.py), and it starts none while a
ship of the repository is in flight and stops the moment one starts (`shipping`).

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
diff since the last green run's tip (lib/selection_miss.py's shadow, GOH.SELECTAUDIT): never a skip.

THE BACKSTOP OF A BASE-CODE CHANGE (GOH.CARRYBASECODE). The land gate judges a change to a file
pytest loads first by its readers, not the whole suite (tests/_carry_shadow.py `choose`), so this
run and the ship's full run are where a fault only an unchosen test sees turns red. A red run files
a fault item naming the lands since the last green full run (lib/idle_red.py; `last.json` keeps
`green`, the tip of the last green run), and the shadow records the tests F missed.

A READING OF MAIN (GOH.MAINREDWAIVE). Each green or red run is kept in goh.db as a reading of main
at its tip (`reading`, lib/main_reading.py): the only evidence a land may waive a red by.

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
import full_run  # lib/: shared, lock-free
import idle_red
import killtree
import landq_inflight
import landq_state
import lock_order
import main_reading
import main_red
import selection_miss
import ship_suite
from full_run import INSTRUMENTED, MAX, _git, declares_full, prepare

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
    return main_red.log_of(q, sha)  # the one path, so the notice reads the log this run wrote


def pending(q: Path) -> bool:
    """A request is waiting or already running: an enqueue starts a lander of its own, which moves
    the request to running within milliseconds, so pending alone would miss it."""
    return landq_state.any_in(q, "pending", "running")


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
    pending), deferred (an enqueue is in its pre-step), shipping (a ship of the repository is in
    flight), green, red, or failed (no worktree)."""
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
    if full_run.ships_in_flight(main.name):  # the ship makes this very run: never beside it
        return "shipping"
    state.mkdir(parents=True, exist_ok=True)
    fd = os.open(state / "lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            lock_order.flock(fd, "idle_suite", wait=False)  # never waits: one idle run
        except BlockingIOError:
            return "busy"
        base = full_run.last_ship(main, sha)
        outcome = _run_locked(q, main, state, sha, base, say, argv or default_argv(), poll)
        # GOH.SELECTAUDIT: the shadow since the last GREEN run (GOH.CARRYBASECODE), else the last
        green = str(last.get("green") or "")
        since = green or str(last.get("sha") or "")
        if outcome in ("green", "red") and (
            why := selection_miss.shadow(q, main, since, sha, log_of(q, sha))
        ):
            say(why)
        if outcome == "red":  # the backstop of a base-code change: its fault item
            filed = idle_red.file_red(main, green, sha, log_of(q, sha))
            say(f"idle full suite on {sha[:8]}: {filed}")
            if moved := idle_red.sweep(main, sha, log_of(q, sha)):
                say(f"idle full suite on {sha[:8]}: {moved}")
        if outcome in ("green", "red"):  # main's reading at SHA, a land's waiver's evidence
            say(reading(main, sha, outcome, log_of(q, sha)))
    finally:
        os.close(fd)
    record = {
        "sha": sha,
        "outcome": outcome,
        "base": base,  # its diff coverage's base: the ship of SHA adopts a green run (full_run)
        "at": dt.datetime.now(dt.UTC).isoformat(),
        "green": sha if outcome == "green" else str(last.get("green") or ""),
        "red": main_red.carry(last, sha, outcome),
    }  # every enqueue says it (MAINREDNOTICE)
    tmp = state / ".last.json.tmp"
    tmp.write_text(json.dumps(record) + "\n")
    tmp.replace(last_path)
    return outcome


def reading(main: Path, sha: str, outcome: str, log: Path) -> str:
    """The run as a reading of main at SHA (lib/main_reading.py, GOH.MAINREDWAIVE): its red nodes
    by the log; a red run that names none is no reading (it waives nothing). Never raises."""
    try:
        red = main_red.nodes_of(log.read_text(errors="replace")) if outcome == "red" else []
        if outcome == "red" and not red:
            return f"idle full suite on {sha[:8]}: no reading kept (red, naming no test)"
        rid = main_reading.record(main, sha, "idle-suite", "*", red)
    except Exception as e:  # noqa: BLE001 -- the reading never breaks the lander
        return f"idle full suite on {sha[:8]}: no reading kept ({e.__class__.__name__}: {e})"
    return f"idle full suite on {sha[:8]}: reading {rid} of main, {len(red)} red"


def _stop_for(q: Path, main: Path) -> str:
    """Why the idle run must not run now: a land is pending (yielded), an enqueue is in its
    pre-step (deferred), a ship of the repository is in flight (shipping); else ""."""
    if pending(q):
        return "yielded"
    if enqueuing(q):
        return "deferred"
    return "shipping" if full_run.ships_in_flight(main.name) else ""


STOPPED = {"yielded": "a land is pending", "deferred": "an enqueue's pre-step runs",
           "shipping": "a ship is in flight, and makes this run itself"}  # fmt: skip


def _run_locked(q: Path, main: Path, state: Path, sha: str, base: str,
                say: Callable[[str], None], argv: list[str], poll: float) -> str:  # fmt: skip
    if why := _stop_for(q, main):
        return why
    wt = state / "wt"
    why = prepare(main, wt, sha)
    if why:
        say(f"idle full suite: no worktree at {sha[:8]}: {why}")
        return "failed"
    log = log_of(q, sha)
    log.parent.mkdir(parents=True, exist_ok=True)
    # The ship's own judgement (full_run.judge_env), so a green run of the tip is the ship's run.
    env = full_run.judge_env(base, "work")
    (state / full_run.PROFILES).unlink(missing_ok=True)
    env["GOH_PROF_REUSE_OUT"] = str(state / full_run.PROFILES)
    say(f"idle full suite on main {sha[:8]} in {wt} -> {log}")
    with log.open("ab") as out:
        proc = subprocess.Popen(argv, cwd=wt, env=env, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)  # fmt: skip
        while True:
            try:
                rc = proc.wait(timeout=poll)
                break
            except subprocess.TimeoutExpired:
                if why := _stop_for(q, main):  # a land, an enqueue or a ship: it goes first
                    killtree.stop_group(killtree.session_pgid(proc), proc=proc)
                    proc.wait()
                    say(f"idle full suite on {sha[:8]}: stopped, {STOPPED[why]}")
                    return why
    say(f"idle full suite on {sha[:8]}: {'green' if rc == 0 else f'red (exit {rc})'}")
    return "green" if rc == 0 else "red"
