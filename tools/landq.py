#!/usr/bin/env python3
"""goh land: the landing queue (GOH.LANDQUEUE, owner 2026-11-11).

    goh land --enqueue REPO SHA [--item ID] [--ship [--summary TEXT]] [--no-start]
    goh land --enqueue --land WORKTREE [--item ID] [--no-start] [--no-pre REASON]
    goh land --check WORKTREE   # the land's structural guards, in seconds (lib/land_check.py)
    goh land --wait ID|REQUEST [--timeout S]  # block until it finished: exit 0 landed, 1 red
    goh land --drain        # the lander: run every request in order, then exit
    goh land --status       # what is pending, running, and how each finished
    goh land --ruff-bump [--detach]  # a ruff newer than goh's pin: its bump-only land, once

THE REQUEST is one JSON file under `<dir>/pending/` (GOH_LANDQ_DIR, default
~/.local/state/goh/landq), named by enqueue time so a listing is the order. It names the repo, the
SHA, the remote and branch the checkout tracks, the item, and the mode:
    push  `git push REMOTE SHA:refs/heads/BRANCH` -- exactly the enqueued commit, through the repo's
          pre-push gate (which takes the machine-wide gate slot itself, lib/gate_slot.sh)
    ship  `tools/ship.py --wait --repo REPO --item ID` (push, release, install, verify, done); the
          checkout's HEAD must still BE the sha, or the ship would release a different commit
    land  `bash tools/land.sh`, bare (GOH.LANDQLAND), the KIT's, never the lane's own
          (D-0012), on the WORKTREE's HEAD at enqueue, in goh's own checkout of it -- every
          pre-step, stack, rebase and gate (GOH.LANDSNAPSHOT, lib/land_slot.py): the lane's tree
          is never read or moved. Refused: the main checkout, uncommitted tracked changes.
          The outcome's `sha` (and `landed`) is the commit main holds for it (land.sh may have
          rebased it); the enqueued tip is kept as `enqueued`.
A red gate pushes nothing (git refuses the push when its pre-push hook fails), nor lands.

THE ENQUEUE PRE-STEP (GOH.ENQUEUEPRE). `--enqueue --land` runs `--check` in the request's slot (a
refusal recorded red naming its `guards`, in seconds: GOH.LANDCHECK), then land.sh's pre-step
(GOH_LAND_PRE; GOH_LAND_PRE_ONLY=1 stops it before the lock) in the request's slot and refuses
a red tip naming the failing tests: a tree reader the lane never ran met its first run inside THIS
serial queue (GOH.REUSEAUDIT's land: 537 s, red on three). Past its deadline is no verdict and the
tip queues (GOH.PRESTEPDEADLINE). A green verdict is kept on the request (`pre`) for the lander's
pre-step (GOH.PRELANDTWICE, lib/land_prestep.py). `--no-pre REASON` skips the run, saying why.
Without `--wait` or `--foreground` it runs detached (GOH.TOOLTIMEOUT, lib/landq_background.py).

THE WAIT. `goh land --wait ID` (an item, or a request's path or name) blocks until that item's
newest request reached done/, prints its outcome (and a red log's tail), and exits 0 landed, 1
red, 3 past --timeout; a pending request with no lander starts one: no wait sits on an idle queue.

THE LANDER is one process holding an flock on `<dir>/lander.lock` (a second exits at once). It moves
a request to `running/`, runs it under lib/bounded_run.py (GOH_LAND_TIMEOUT, default 3600 s, its
process group swept on expiry), writes `done/<name>.json` and `logs/<name>.log`, and records
`loopctl set ID landing {"state": "landed"|"red", "sha", "log", "at"} --json` on the item, which the
coordinator's tick re-dispatches when red. A land takes up to GOH_LANDQ_BATCH-1 more of its repo,
stacked, ONE land gate on the tip, a red bisected (GOH.LANDBATCH, lib/landq_batch.py), after lands
in their pre-step had GOH_LANDQ_WINDOW s to join (lib/landq_window.py). Between requests it reads
its code's digest (landq.py, land.sh, lib/ at the live checkout's HEAD); changed by a land, it execs
a fresh copy (GOH.LANDERSTALECODE). Out of requests, it frees the lock and looks once more for one
enqueued meanwhile; then, idle, runs the full suite on main's tip when the release needs one and
none is recorded (lib/idle_suite.py, GOH.GATEBUDGET2), holding no lock, stopped by a request.
First, a box ruff newer than goh's pin lands as its own bump-only request (lib/ruff_bump.py,
GOH.RUFFBUMPLAND); `--ruff-bump` is that look on demand, started by a consumer gate's red.

WHERE IT RUNS, AND A REBOOT (D-0006, docs/decisions/). No launchd agent: `--enqueue` starts a
detached lander when none holds the lock, and the coordinator's tick may run `--drain`. A request
is a file, so it survives a reboot: one a dead lander left in `running/` runs again first.

Pinned by tests/test_landq.py (batches: tests/test_landq_batch.py); `--report`: lib/landq_report.py.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout: run as is
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import contextlib
import importlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "lib")]
bounded_run = importlib.import_module("bounded_run")
diffcov_carry = importlib.import_module("diffcov_carry")
gitrun = importlib.import_module("gitrun")
idle_suite = importlib.import_module("idle_suite")
land_attempts = importlib.import_module("land_attempts")
land_check = importlib.import_module("land_check")
land_e2e = importlib.import_module("land_e2e")
lock_order = importlib.import_module("lock_order")
land_prestep = importlib.import_module("land_prestep")
land_slot = importlib.import_module("land_slot")
landq_batch = importlib.import_module("landq_batch")
landq_early = importlib.import_module("landq_early")
landq_window = importlib.import_module("landq_window")
landq_report = importlib.import_module("landq_report")
landq_request = importlib.import_module("landq_request")
landq_look = importlib.import_module("landq_look")
landq_inflight = importlib.import_module("landq_inflight")
landq_background = importlib.import_module("landq_background")
landq_state = importlib.import_module("landq_state")
ruff_bump = importlib.import_module("ruff_bump")
selection_miss = importlib.import_module("selection_miss")
Refused, request = landq_request.Refused, landq_request.request
land_request = landq_request.land_request
_tui = importlib.import_module("tui.lib")
err, info, ok, warn = _tui.err, _tui.info, _tui.ok, _tui.warn

GRACE = 5
DEFAULT_TIMEOUT, WAIT_TIMEOUT = 3600, 3 * 3600
LAND_TIMEOUT = 2 * 3600  # land.sh: a 10 min pre-step, a 90 min gate, and a wait for the lock first
DIRS = ("pending", "running", "done", "logs")


def qdir() -> Path:
    return Path(os.environ.get("GOH_LANDQ_DIR") or Path.home() / ".local/state/goh/landq")


def loopctl() -> Path:
    return Path(os.environ.get("GOH_LOOPCTL") or Path.home() / "Projects/.loop/tools/loopctl.py")


git, now = landq_request.git, landq_request.now


def git_bindings() -> tuple[str, ...]:
    """git's own list of the variables binding it to one repository (checks/_gitutil.py)."""
    sys.path.insert(0, str(ROOT / "checks"))
    from _gitutil import local_env_vars

    return local_env_vars()


def land_script(_wt: Path) -> Path:
    """The land.sh that drives a land in a worktree: main's, never the lane's own (D-0012)."""
    return ROOT / "tools" / "land.sh"


def prestep(req: dict) -> None:
    """GOH.ENQUEUEPRE (lib/land_prestep.py): the pre-step first, then the queue."""
    wt = Path(req["worktree"])
    landq_inflight.mark(qdir(), req["item"] or Path(req["repo"]).name, repo=req["repo"])
    try:  # its seconds: the e2e's pre-step share (lib/land_e2e.py)
        req["pre_s"] = land_prestep.run_kept(req, qdir(), land_script(wt), git_bindings())
    except land_prestep.Red as e:
        land_check.learn(qdir(), req, str(e))  # its guards, for the next check (GOH.LANDCHECK)
        raise Refused(str(e)) from None


def enqueue(req: dict, into: str = "pending") -> Path:
    q = qdir()
    for d in (*DIRS, into):
        (q / d).mkdir(parents=True, exist_ok=True)
    slug = (req["item"] or Path(req["repo"]).name).replace("/", "_")
    path = q / into / f"{time.time_ns():020d}-{slug}.json"
    landq_state.write(path, req)  # one rename: never half a request
    return path


def start_lander() -> None:
    """A detached lander in its own session; one already running makes it exit at once."""
    log = qdir() / "lander.log"
    with log.open("a") as out:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--drain"],
                         stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                         start_new_session=True, env=os.environ.copy())  # fmt: skip


def take_lock(q: Path):
    fd = os.open(q / "lander.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        lock_order.flock(fd, "lander", wait=False)  # never waits: a second drainer exits
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def argv_for(req: dict, log: Path | str = "") -> list[str]:
    env = ["env", *(f"-u{k}" for k in git_bindings())]
    if req["mode"] == "ship":
        ship = [sys.executable, str(ROOT / "tools" / "ship.py"), "--wait", "--repo", req["repo"],
                "--item", req["item"]]  # fmt: skip
        return [*env, *ship, *(["--summary", req["summary"]] if req.get("summary") else [])]
    if req["mode"] == "land":
        wt = Path(req["worktree"])
        return [*env, *land_prestep.lander_env(req), *land_prestep.timings_keep(log), "bash", "-c",
                'cd -- "$1" && exec bash "$2"', "land", str(wt), str(land_script(wt))]  # fmt: skip
    ref = f"{req['sha']}:refs/heads/{req['branch']}"
    return [*env, "git", "-C", req["repo"], "push", req["remote"], ref]


def batch_kit() -> landq_batch.Kit:
    timeout = int(os.environ.get("GOH_LAND_TIMEOUT") or LAND_TIMEOUT)
    return landq_batch.Kit([f"-u{k}" for k in git_bindings()], ROOT / "tools" / "land.sh",
                           land_script, timeout, GRACE, info)  # fmt: skip


def run_one(q: Path, path: Path) -> dict:
    """Run one request already in running/; return its outcome."""
    req = json.loads(path.read_text())
    log = q / "logs" / f"{path.stem}.log"
    outcome = {"sha": req["sha"], "repo": req["repo"], "mode": req["mode"], "log": str(log)}
    land = req["mode"] == "land"
    if land and (why := land_slot.ready(q, req)):  # GOH.LANDSNAPSHOT: the slot at the sha again
        log.write_text(f"{why} -- nothing landed; enqueue again\n")
        return {**outcome, "state": "red", "why": why.split(":")[0]}
    head = git(Path(req["repo"]), "rev-parse", "HEAD")
    if req["mode"] == "ship" and head != req["sha"]:
        log.write_text(
            f"HEAD is {head or '(unreadable)'}, not the enqueued {req['sha']}: a ship releases "
            "HEAD, so it would land a different commit -- enqueue again\n"
        )
        return {**outcome, "state": "red", "why": "HEAD moved"}
    timeout = int(os.environ.get("GOH_LAND_TIMEOUT") or (LAND_TIMEOUT if land else DEFAULT_TIMEOUT))
    info(f"land {Path(req['repo']).name} @ {req['sha'][:8]} ({req['mode']}) -> {log}")
    res = bounded_run.run_step(argv_for(req, log), timeout, GRACE, f"land {req['mode']}", str(log))
    if res.code == 0 and not res.timed_out:
        # GOH.LANDQSHA: land.sh may rebase the tip, and `loopctl done` reads landing.sha, so the
        # sha recorded is the one main now holds; the enqueued tip keeps its own name.
        on_main, main = "", Path(req["repo"])
        try:
            if land:
                on_main = landq_batch.strict_git(main, "rev-parse", "HEAD", tries=3)
        except landq_batch.GitFailed as e:  # GOH.BATCHGITFAIL: landed, so never an empty sha
            why = f"landed, but main's HEAD is unreadable ({e}): check main before enqueueing again"
            return {**outcome, "state": "red", "why": why}
        main_tip = {"sha": on_main, "enqueued": req["sha"], "landed": on_main} if land else {}
        return {**outcome, "state": "landed", **main_tip}
    why = f"timed out after {timeout}s" if res.timed_out else landq_batch.exit_why(res.code)
    return {**outcome, "state": "red", "why": why}


def record(req_path: Path, outcome: dict, item: str) -> None:
    """The outcome to loopctl, done/ and the timing ledger. It names its ITEM and which ATTEMPT
    at it this was (a re-enqueue after a red is attempt n+1, lib/land_attempts.py), and every
    attempt -- red, timed out, landed -- is one `land attempt` row (GOH.GATEBUDGET2)."""
    done_dir = req_path.parent.parent / "done"
    named = land_attempts.item_of(req_path, {"item": item})
    outcome = {**outcome, "at": now()}
    req = landq_batch.read(req_path)
    outcome.update(land_e2e.asked(req))  # the e2e's start (GOH.LANDTARGET)
    outcome.update(land_slot.done(done_dir.parent, req), **landq_report.slot_waits(outcome))
    land_prestep.discard(req, done_dir.parent)  # its verdict, and any orphan (GOH.LANDLEAKS)
    outcome.update(item=named, attempt=land_attempts.attempt_of(done_dir, named))
    outcome.update(land_check.fields(outcome, req))  # its guard; cov_risk (GOH.DIFFCOVLAND)
    # A landing that leaves its own uncovered lines files their fix item now (GOH.DIFFCOVCARRY).
    outcome.update(diffcov_carry.file_item(outcome, req, str(loopctl()), item))
    if item:
        tool = loopctl()
        if tool.is_file():
            done = subprocess.run([sys.executable, str(tool), "set", item, "landing",
                                   json.dumps(outcome), "--json"], capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=120, check=False)  # fmt: skip
            outcome["recorded"] = done.returncode == 0
            if done.returncode:
                warn(f"loopctl set {item} landing: exit {done.returncode}: {done.stderr.strip()}")
        else:
            outcome["recorded"] = False
            warn(f"no loopctl at {tool} (GOH_LOOPCTL): {item}'s outcome is only in done/")
    landq_state.write(done_dir / req_path.name, outcome)
    try:
        land_attempts.record(outcome, req_path.stem)
        land_e2e.backfill(done_dir.parent)  # GOH.LANDTARGET: the item end to end, once it landed
    except Exception as e:  # noqa: BLE001 -- the instrument is never the reason a land fails
        warn(f"{named}: its timing ledger rows were not written: {e}")
    if why := selection_miss.record_land(done_dir.parent, outcome, named):  # GOH.SELECTAUDIT
        warn(why)
    landq_report.announce(outcome, item or Path(outcome["repo"]).name)


def live_root() -> Path:
    """The checkout this lander's code comes from: an export names it in GOH_LIVE_ROOT."""
    return Path(os.environ.get("GOH_LIVE_ROOT") or ROOT)


def code_digest() -> str:
    """The lander's code on the live checkout's HEAD: landq.py, land.sh, lib/ (GOH.LANDERSTALECODE).

    A checkout is read through git, so a land that moves main changes it; a copy outside any
    checkout hashes nothing and never re-execs."""
    return git(live_root(), "rev-parse", "HEAD:tools/landq.py", "HEAD:tools/land.sh", "HEAD:lib")


def live_pythonpath(value: str) -> str:
    """PYTHONPATH as a re-run lander inherits it: each entry once, the ones that exist and are no
    head export (`.goh-head`). The fresh copy's own C4 re-run (gates/_from_head.py) puts ITS export
    first; kept, every earlier one stayed behind it, pruned or not -- the drainer's environment
    grew one export per re-exec and every gate it started inherited them (GOH.VERDICTCARRY3)."""
    out: list[str] = []
    for entry in value.split(os.pathsep):
        export = os.path.isfile(os.path.join(entry, ".goh-head"))
        if entry and entry not in out and os.path.isdir(entry) and not export:
            out.append(entry)
    return os.pathsep.join(out)


def drain_held(
    q: Path,
    digest: str = "",
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> bool:
    """Run every pending request; False when the lander's code changed and it must re-exec.
    CLOCK and SLEEP: the batch window's and the held pause's (a test's fake clock), else time's."""
    landq_batch.resume(q, warn)  # a dead lander's requests, an enqueue's orphan keep
    fails: dict[str, int] = {}  # passes each request has been left pending by a git failure
    while pending := landq_window.pending(q, info, clock, sleep):  # the window (GOH.BATCHWINDOW)
        paths = [q / "running" / p.name for p in landq_batch.pick(pending)]  # GOH.LANDBATCH
        for path in paths:
            (q / "pending" / path.name).replace(path)
        try:
            outcomes = landq_batch.run(q, paths, batch_kit(), run_one)
        except Exception as e:  # noqa: BLE001 -- one broken request must not strand the rest
            red = {"sha": "", "repo": "", "state": "red", "why": f"lander: {e}", "log": ""}
            outcomes = [red] * len(paths)
        held = False
        for path, outcome in zip(paths, outcomes, strict=True):
            if (outcome := landq_batch.hold(q, path, outcome, fails, warn)) is None:
                held = True
                continue
            record(path, outcome, landq_batch.read(path).get("item", ""))
            path.unlink(missing_ok=True)
        landq_early.sweep(q, record, info)  # GOH.EARLYCONFLICT: red now, not at its turn
        if held:
            (sleep or time.sleep)(landq_batch.PAUSE)  # the git failure may be load: no hot retry
        if digest and code_digest() != digest:
            return False
    return True


def home() -> Path:
    """The queue's state dir, made, and the lander's working directory from here on (GOH.LANDERCWD).

    The lander is started from whatever directory its enqueuer was in, often a lane worktree that is
    removed when the lane is cleaned: an inherited directory that is gone breaks every relative path
    and every child that inherits it (`getcwd: cannot access parent directories`). Each land names
    its repo and worktree by absolute path, so nothing needs the directory it started in."""
    q = qdir()
    with contextlib.suppress(OSError):  # the start directory is already gone: an absolute state
        q = q.resolve()  # dir needs no cwd
    for d in DIRS:
        (q / d).mkdir(parents=True, exist_ok=True)
    os.chdir(q)
    return q


def drain() -> int:
    q = home()
    digest = code_digest()  # what this lander runs; a land that changes it re-execs a fresh one
    while True:
        fd = take_lock(q)
        if fd is None:
            info(f"a lander already holds {q / 'lander.lock'}")
            return 0
        try:
            current = drain_held(q, digest)
        finally:
            os.close(fd)
        if not current:
            fresh = live_root() / "tools" / "landq.py"
            info(f"the lander's code changed on {live_root()}: re-running {fresh}")
            sys.stdout.flush()
            sys.stderr.flush()
            env = dict(os.environ, PYTHONPATH=live_pythonpath(os.environ.get("PYTHONPATH", "")))
            os.chdir(q)  # the fresh lander inherits the state dir, whatever happened to it since
            os.execve(sys.executable, [sys.executable, str(fresh), "--drain"], env)
        # Idle (none enqueued after the last look): main's full suite record, so no ship waits on a
        # hand run (lib/idle_suite.py). It holds no lock and yields to the first request that
        # arrives: then drain again.
        idle = not any((q / "pending").glob("*.json"))
        if idle and ruff_bump.run(q, live_root(), info) == "enqueued":
            continue  # the bump's own request (GOH.RUFFBUMPLAND): drain it, or its lander does
        if idle and idle_suite.run(q, live_root(), info) != "yielded":
            return 0


def nudge() -> None:
    """Start the lander when nobody drains (`--wait` on a pending request)."""
    if (fd := take_lock(qdir())) is not None:
        os.close(fd)
        start_lander()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goh land", description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--enqueue", nargs="*", metavar="REPO SHA")
    mode.add_argument("--drain", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--ruff-bump", action="store_true", help="land a newer ruff's pin bump, once")
    mode.add_argument("--check", metavar="WORKTREE", help="its tip's structural land guards")
    landq_report.add_arguments(mode, ap)
    landq_background.add_arguments(ap)  # --land, --no-pre, --no-start, --wait, --foreground
    ap.add_argument("--timeout", type=float, default=WAIT_TIMEOUT, help="--wait's bound, seconds")
    ap.add_argument("--item", default="")
    ap.add_argument("--ship", action="store_true", help="run goh ship --wait, not a bare push")
    ap.add_argument("--summary", default="")
    ap.add_argument("--detach", action="store_true", help="--ruff-bump: look in the background")
    args = ap.parse_args(argv)
    if landq_background.check(ap, args):  # the enqueue's long pre-step, detached (GOH.TOOLTIMEOUT)
        return landq_background.start(args, argv, qdir(), Path(__file__).resolve())
    if args.report:
        return landq_report.run(qdir(), args.since, args.json)
    if args.drain or args.status:
        return drain() if args.drain else landq_look.status(qdir())
    if args.check is not None:
        return land_check.cli(args.check, qdir())
    if args.ruff_bump:  # lib/ruff_bump.py look: the bump-only land, if one is owed
        return ruff_bump.look(home, live_root(), args.detach, Path(__file__).resolve(), info)
    if args.wait and args.enqueue is None:  # THE WAIT
        return landq_look.wait(qdir(), args.wait, args.timeout, nudge)
    req: dict = {}
    try:
        if args.land is not None:
            landq_background.worktree_only(args)
            req = land_request(args.land, args.item, qdir())  # in a slot (GOH.LANDSNAPSHOT)
            if args.no_pre is not None and not args.no_pre.strip():
                raise Refused("--no-pre needs a reason: --no-pre 'why this tip skips the pre-step'")
            if land_check.at_enqueue(qdir(), req, enqueue, record, info):
                return 1  # recorded red, its guards named (GOH.LANDCHECK)
            if args.no_pre is None:
                prestep(req)
            else:
                warn(f"--no-pre: the enqueue's pre-step skipped: {args.no_pre}")
        elif len(args.enqueue) != 2:
            raise Refused("--enqueue takes REPO SHA (or --land WORKTREE)")
        else:
            req = request(*args.enqueue, args.item, args.ship, args.summary)
    except Refused as e:
        land_slot.release_req(qdir(), req)
        err(f"goh land: {e} -- nothing enqueued")
        landq_background.red(args, req, str(e), qdir(), enqueue, record)
        return 2
    if req["mode"] == "land" and (red := landq_early.at_enqueue(qdir(), req, enqueue, record)):
        err(f"goh land: {red.splitlines()[0]} -- recorded red at enqueue, nothing queued")
        return 1
    path = enqueue(req)  # a slot a crash strands is reclaimed: its pid is gone (lib/land_slot.py)
    if not args.no_start:
        start_lander()
    who = req["item"] or Path(req["repo"]).name
    ok(f"enqueued {who} @ {req['sha'][:8]} ({req['mode']}): {path}")
    return 0 if args.wait is None else landq_look.wait(qdir(), str(path), args.timeout, nudge)


if __name__ == "__main__":
    sys.exit(main())
