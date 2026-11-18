"""lib/idle_suite.py -- the idle lander runs the full suite on main's tip (GOH.GATEBUDGET2).

OWNER, 2026-11-17 (the OpenClaw CI write-up, "Yes, file them"): of "a record whose carried tests
come from a full record counts as full" and "the lander runs the full suite when idle", take the
second. Since verdict carry a land's suite record is no full run (1727 tests carried, e2915305), so
the release kit (GOH_SHIP_FULL_SUITE=on, `reuse.py full-suite`) refused every ship until somebody
ran the whole suite by hand, 12+ minutes at load, off every path. Here the lander makes that record
when it has nothing to land, so it is on disk before a ship asks for it:

    run(q, live)   after the queue drained: when the main checkout of LIVE (the lander's own
                   repository) declares GOH_SHIP_FULL_SUITE=on and main's tip has no full suite
                   record, run `tools/pytest.sh` (GOH_LAND_VERDICTS_RECORD=/dev/null: the store's
                   record alone, the hand run's form) in a detached worktree at that tip,
                   `<q>/idle-suite/wt` (kept between runs: its build caches are warm).

IT NEVER DELAYS A LAND. The run takes its own gate slot through admission (`tools/load.py 0`, class
`work`: behind every land and ship), holds no lock a land takes (the lander's lock is released
before it starts, so an enqueue starts a lander at once), and is stopped -- its whole process group,
TERM then KILL -- the moment a request is pending: it yields, and the lander drains. One idle run at
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
import gitrun
import killtree
import lock_order
import reuse
import selection_miss

ROOT = Path(__file__).resolve().parent.parent

OFF_ENV = "GOH_LANDQ_IDLE_SUITE"  # "off": the lander never runs it
POLL_S = 5.0  # how often a running suite looks for a pending request
MAX = "90m"  # a land gate's own bound
# A lander started inside a goh suite (its runner's marker, the suite home's): a test's lander
# never starts the real suite, however its queue was driven (tests/suite_home.py MARKER).
IN_SUITE = ("GATES_OF_HECK_SUITE_RUNNER", "GATES_OF_HECK_SUITE_REAL_HOME")


def _git(repo: Path, *args: str, write: bool = False) -> subprocess.CompletedProcess:
    """git in REPO (no inherited GIT_* answers for it): a reader under a timeout, a writer
    (worktree add, checkout, clean) TERMed as a group at its deadline so no lock is stranded."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if not write:
        return gitrun.run_reader(list(args), cwd=repo, timeout=120, env=env)
    done = gitrun.run_writer(list(args), cwd=repo, deadline_s=600, env=env)
    return done or subprocess.CompletedProcess(["git", *args], 124, "", "stopped at its deadline")


def main_checkout(live: Path) -> Path | None:
    """The first `git worktree list` entry of LIVE's repository: its main checkout."""
    out = _git(live, "worktree", "list", "--porcelain")
    first = out.stdout.splitlines()[0] if out.returncode == 0 and out.stdout else ""
    return Path(first.removeprefix("worktree ")) if first.startswith("worktree ") else None


def declares_full(main: Path) -> bool:
    """MAIN's .gatesrc sets GOH_SHIP_FULL_SUITE=on (read in a child, as the release kit does)."""
    if not (main / ".gatesrc").is_file():
        return False
    read = (
        "unset GOH_SHIP_FULL_SUITE; . ./.gatesrc >/dev/null 2>&1 </dev/null; "
        'printf %s "${GOH_SHIP_FULL_SUITE-}"'
    )
    out = subprocess.run(["bash", "-c", read], cwd=main, capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, check=False, timeout=60)  # fmt: skip
    return out.stdout == "on"


def prepare(main: Path, wt: Path, sha: str) -> str:
    """WT checked out at SHA, clean of tracked and untracked changes (ignored build caches kept);
    "" or why it could not be."""
    if (wt / ".git").exists():
        steps = [(wt, "checkout", "-q", "--detach", "-f", sha), (wt, "clean", "-fdq")]
    else:
        wt.parent.mkdir(parents=True, exist_ok=True)
        _git(main, "worktree", "prune", write=True)
        steps = [(main, "worktree", "add", "-q", "--detach", "-f", str(wt), sha)]
    for repo, *args in steps:
        done = _git(repo, *args, write=True)
        if done.returncode:
            return f"git {' '.join(args[:2])}: {done.stderr.strip()[:300]}"
    return ""


def default_argv() -> list[str]:
    return [sys.executable, str(ROOT / "tools" / "load.py"), "--background-ok", "idle full suite",
            "--max", MAX, "0", "--", "bash", "tools/pytest.sh"]  # fmt: skip


def log_of(q: Path, sha: str) -> Path:
    return q / "logs" / f"idle-suite-{sha[:12]}.log"


def pending(q: Path) -> bool:
    """A request is waiting or already running: an enqueue starts a lander of its own, which moves
    the request to running/ within milliseconds, so pending/ alone would miss it."""
    return any(any((q / d).glob("*.json")) for d in ("pending", "running"))


def run(
    q: Path,
    live: Path,
    say: Callable[[str], None] = print,
    argv: list[str] | None = None,
    poll: float = POLL_S,
) -> str:
    """One idle run, if one is owed; its outcome: off, nothing, busy, yielded, green, red, or
    failed (the worktree could not be made)."""
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
    if reuse.full_suite(main, sha)[0] is not None:
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
                if pending(q):  # a land arrived: it goes first, always
                    killtree.stop_group(killtree.session_pgid(proc), proc=proc)
                    proc.wait()
                    say(f"idle full suite on {sha[:8]}: stopped, a land is pending")
                    return "yielded"
    say(f"idle full suite on {sha[:8]}: {'green' if rc == 0 else f'red (exit {rc})'}")
    return "green" if rc == 0 else "red"
