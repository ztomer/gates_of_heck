#!/usr/bin/env python3
"""goh land: the landing queue (GOH.LANDQUEUE, owner 2026-11-11).

    goh land --enqueue REPO SHA [--item ID] [--ship [--summary TEXT]] [--no-start]
    goh land --enqueue --land WORKTREE [--item ID] [--no-start] [--no-pre REASON]
    goh land --wait ID|REQUEST [--timeout S]  # block until it finished: exit 0 landed, 1 red
    goh land --drain        # the lander: run every request in order, then exit
    goh land --status       # what is pending, running, and how each finished

WHY. An agent ran its own push gate (1197-1523 s under load); now it ends at "committed, enqueued".

THE REQUEST is one JSON file under `<dir>/pending/` (GOH_LANDQ_DIR, default
~/.local/state/goh/landq), named by enqueue time so a listing is the order. It names the repo, the
SHA, the remote and branch the checkout tracks, the item, and the mode:
    push  `git push REMOTE SHA:refs/heads/BRANCH` -- exactly the enqueued commit, through the repo's
          pre-push gate (which takes the machine-wide gate slot itself, lib/gate_slot.sh)
    ship  `tools/ship.py --wait --repo REPO --item ID` (push, release, install, verify, done); the
          checkout's HEAD must still BE the sha, or the ship would release a different commit
    land  `bash tools/land.sh`, bare, in the WORKTREE (GOH.LANDQLAND): the land lock, the land
          gate, the fast-forward of main, as a lane ran it by hand -- the worktree's own
          tools/land.sh when it has one (a goh lane gates with its tip's gates), else the kit's.
          The sha is the worktree's HEAD at enqueue; a tip that moved since is refused unrun,
          and the enqueue refuses the main checkout and a tree with uncommitted tracked changes.
          The outcome's `sha` (and `landed`) is the commit main holds for it (land.sh may have
          rebased it); the enqueued tip is kept as `enqueued`.
A red gate pushes nothing (git refuses the push when its pre-push hook fails), nor lands.

THE ENQUEUE PRE-STEP (GOH.ENQUEUEPRE). `--enqueue --land` first runs land.sh's pre-step
(GOH_LAND_PRE; GOH_LAND_PRE_ONLY=1 stops it before the lock) in the caller's worktree and refuses
a red tip naming the failing tests: a tree reader the lane never ran met its first run inside THIS
serial queue (GOH.REUSEAUDIT's land: 537 s, red on three). Past its deadline is no verdict and the
tip queues (GOH.PRESTEPDEADLINE). The lander's own pre-step is still the authority after a
rebase. `--no-pre REASON` skips the run, printing the reason.

THE WAIT. `goh land --wait ID` (an item, or a request's path or name) blocks until that item's
newest request reached done/, prints its outcome (and a red log's tail), and exits 0 landed, 1
red, 3 past --timeout; a pending request with no lander starts one: no wait sits on an idle queue.

THE LANDER is one process holding an flock on `<dir>/lander.lock` (a second exits at once). It
moves a request to `running/`, runs it under lib/bounded_run.py (GOH_LAND_TIMEOUT, default 3600 s,
its process group swept on expiry), writes `done/<name>.json` and `logs/<name>.log`, and records
`loopctl set ID landing {"state": "landed"|"red", "sha", "log", "at"} --json` on the item, which the
coordinator's tick re-dispatches when red. A land request takes up to GOH_LANDQ_BATCH-1 more of its
repo with it: stacked, ONE land gate on the tip, a red bisected (GOH.LANDBATCH, lib/landq_batch.py).
Between requests it re-reads its code's digest (tools/landq.py and lib/ on the live checkout's
HEAD) and, changed by a land, releases the lock and execs a fresh copy (GOH.LANDERSTALECODE). After
the last request it releases the lock and looks once more for one enqueued meanwhile. Then, idle,
it runs the full suite on main's tip when the repo's release needs one and none is recorded
(lib/idle_suite.py, GOH.GATEBUDGET2): in its own gate slot, holding no lock, stopped the moment a
request is pending, after which the lander drains again.

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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "lib")]
bounded_run = importlib.import_module("bounded_run")
gitrun = importlib.import_module("gitrun")
idle_suite = importlib.import_module("idle_suite")
land_attempts = importlib.import_module("land_attempts")
lock_order = importlib.import_module("lock_order")
land_prestep = importlib.import_module("land_prestep")
landq_batch = importlib.import_module("landq_batch")
landq_report = importlib.import_module("landq_report")
landq_request = importlib.import_module("landq_request")
landq_look = importlib.import_module("landq_look")
selection_miss = importlib.import_module("selection_miss")
Refused, request, land_request = (
    landq_request.Refused,
    landq_request.request,
    landq_request.land_request,
)
_tui = importlib.import_module("tui.lib")
err, info, ok, warn = _tui.err, _tui.info, _tui.ok, _tui.warn

GRACE = 5
DEFAULT_TIMEOUT = 3600
LAND_TIMEOUT = 2 * 3600  # land.sh: a 10 min pre-step, a 90 min gate, and a wait for the lock first
WAIT_TIMEOUT = 3 * 3600
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


def land_script(wt: Path) -> Path:
    """The land.sh a lane runs by hand from WT: the worktree's own, else the kit's."""
    own = wt / "tools" / "land.sh"
    return own if own.is_file() else ROOT / "tools" / "land.sh"


def prestep(req: dict) -> None:
    """GOH.ENQUEUEPRE (lib/land_prestep.py): the pre-step first, then the queue."""
    wt = Path(req["worktree"])
    try:
        land_prestep.run(wt, req["sha"], land_script(wt), git_bindings())
    except land_prestep.Red as e:
        raise Refused(str(e)) from None


def write_json(path: Path, data: dict) -> None:
    """One rename, so a reader never sees half a request."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n")
    tmp.replace(path)


def enqueue(req: dict) -> Path:
    q = qdir()
    for d in DIRS:
        (q / d).mkdir(parents=True, exist_ok=True)
    slug = (req["item"] or Path(req["repo"]).name).replace("/", "_")
    path = q / "pending" / f"{time.time_ns():020d}-{slug}.json"
    write_json(path, req)
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


def argv_for(req: dict) -> list[str]:
    env = ["env", *(f"-u{k}" for k in git_bindings())]
    if req["mode"] == "ship":
        ship = [sys.executable, str(ROOT / "tools" / "ship.py"), "--wait", "--repo", req["repo"],
                "--item", req["item"]]  # fmt: skip
        return [*env, *ship, *(["--summary", req["summary"]] if req.get("summary") else [])]
    if req["mode"] == "land":
        wt = Path(req["worktree"])
        return [*env, "bash", "-c", 'cd -- "$1" && exec bash "$2"', "land", str(wt),
                str(land_script(wt))]  # fmt: skip
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
    head = git(Path(req["worktree"] if land else req["repo"]), "rev-parse", "HEAD")
    if req["mode"] == "ship" and head != req["sha"]:
        log.write_text(
            f"HEAD is {head or '(unreadable)'}, not the enqueued {req['sha']}: a ship releases "
            "HEAD, so it would land a different commit -- enqueue again\n"
        )
        return {**outcome, "state": "red", "why": "HEAD moved"}
    if land and head != req["sha"]:
        log.write_text(
            f"{req['worktree']} is at {head or '(unreadable)'}, not the enqueued {req['sha']}: "
            "the tip moved after enqueue, so land.sh would gate a commit nobody enqueued -- "
            "nothing landed; enqueue again\n"
        )
        return {**outcome, "state": "red", "why": "tip moved after enqueue"}
    timeout = int(os.environ.get("GOH_LAND_TIMEOUT") or (LAND_TIMEOUT if land else DEFAULT_TIMEOUT))
    info(f"land {Path(req['repo']).name} @ {req['sha'][:8]} ({req['mode']}) -> {log}")
    res = bounded_run.run_step(argv_for(req), timeout, GRACE, f"land {req['mode']}", str(log))
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
    why = f"timed out after {timeout}s" if res.timed_out else f"exit {res.code}"
    return {**outcome, "state": "red", "why": why}


def record(req_path: Path, outcome: dict, item: str) -> None:
    """The outcome to loopctl, done/ and the timing ledger. It names its ITEM and which ATTEMPT
    at it this was (a re-enqueue after a red is attempt n+1, lib/land_attempts.py), and every
    attempt -- red, timed out, landed -- is one `land attempt` row (GOH.GATEBUDGET2)."""
    done_dir = req_path.parent.parent / "done"
    named = land_attempts.item_of(req_path, {"item": item})
    outcome = {**outcome, "at": now()}
    outcome.update(item=named, attempt=land_attempts.attempt_of(done_dir, named))
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
    write_json(done_dir / req_path.name, outcome)
    try:
        land_attempts.record(outcome, req_path.stem)
    except Exception as e:  # noqa: BLE001 -- the instrument is never the reason a land fails
        warn(f"{named}: its attempt row was not written to the timing ledger: {e}")
    if why := selection_miss.record_land(done_dir.parent, outcome, named):  # GOH.SELECTAUDIT
        warn(why)
    mark = ok if outcome["state"] == "landed" else err
    mark(f"{item or Path(outcome['repo']).name}: {outcome['state']} @ {outcome['sha'][:8]}")


def live_root() -> Path:
    """The checkout this lander's code comes from: an export names it in GOH_LIVE_ROOT."""
    return Path(os.environ.get("GOH_LIVE_ROOT") or ROOT)


def code_digest() -> str:
    """The lander's code on the live checkout's HEAD: tools/landq.py and lib/ (GOH.LANDERSTALECODE).

    A checkout is read through git, so a land that moves main changes it; a copy outside any
    checkout hashes nothing and never re-execs."""
    return git(live_root(), "rev-parse", "HEAD:tools/landq.py", "HEAD:lib")


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


def drain_held(q: Path, digest: str = "") -> bool:
    """Run every pending request; False when the lander's code changed and it must re-exec."""
    # A request a dead lander was running goes back to the front: its name keeps its place.
    for stale in sorted((q / "running").glob("*.json")):
        stale.replace(q / "pending" / stale.name)
        warn(f"{stale.name}: its lander died mid-request -- running it again")
    fails: dict[str, int] = {}  # passes each request has been left pending by a git failure
    while pending := sorted((q / "pending").glob("*.json")):
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
            if (
                outcome := landq_batch.hold(q, path, outcome, fails, warn)
            ) is None:  # GOH.BATCHGITFAIL
                held = True
                continue
            record(path, outcome, landq_batch.read(path).get("item", ""))
            path.unlink(missing_ok=True)
        if held:
            time.sleep(landq_batch.PAUSE)  # the git failure may be load: not a hot retry loop
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
        if not any((q / "pending").glob("*.json")):  # enqueued after the last look: ours to run
            audit_drain()
            # Idle: main's full suite record, so no ship waits on a hand run (lib/idle_suite.py).
            # It holds no lock and yields to the first request that arrives: then drain again.
            if idle_suite.run(q, live_root(), info) != "yielded":
                return 0


def audit_drain() -> None:
    """The idle lander reads the reuse audit's queued CI shadows (lib/reuse_audit.py drain,
    GOH.REUSEAUDIT): no gate is waiting, and a read is one `loopctl ci` per shadow, never a poll."""
    sys.path.insert(0, str(live_root() / "lib"))
    try:
        import reuse  # the store's root, as the audit names it
    except ImportError:
        return
    finally:
        sys.path.pop(0)
    script = live_root() / "lib" / "reuse_audit.py"
    if any((reuse.root() / "audit" / "pending").glob("*.json")) and script.is_file():
        subprocess.run([sys.executable, str(script), "drain"], check=False, timeout=600,
                       stdin=subprocess.DEVNULL)  # fmt: skip


def nudge() -> None:
    """Start the lander when nobody drains (`--wait` on a pending request)."""
    if (fd := take_lock(qdir())) is not None:
        os.close(fd)
        start_lander()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goh land", description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--enqueue", nargs="*", metavar="REPO SHA")
    mode.add_argument("--drain", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--wait", metavar="ID|REQUEST", help="block until it finished (THE WAIT)")
    landq_report.add_arguments(mode, ap)
    ap.add_argument("--land", metavar="WORKTREE", help="run tools/land.sh there (mode land)")
    ap.add_argument("--timeout", type=float, default=WAIT_TIMEOUT, help="--wait's bound, seconds")
    ap.add_argument("--item", default="")
    ap.add_argument("--ship", action="store_true", help="run goh ship --wait, not a bare push")
    ap.add_argument("--summary", default="")
    ap.add_argument("--no-pre", metavar="REASON", nargs="?", const="",
                    help="skip the enqueue's pre-step run; a reason is required")  # fmt: skip
    ap.add_argument("--no-start", action="store_true", help="enqueue only; start no lander")
    args = ap.parse_args(argv)
    if args.report:
        return landq_report.run(qdir(), args.since, args.json)
    if args.drain:
        return drain()
    if args.status:
        return landq_look.status(qdir())
    if args.wait is not None:
        return landq_look.wait(qdir(), args.wait, args.timeout, nudge)
    try:
        if args.land is not None:
            if args.enqueue or args.ship:
                raise Refused("--land takes the worktree only: no REPO SHA, no --ship")
            req = land_request(args.land, args.item)
            if args.no_pre is None:
                prestep(req)
            elif not args.no_pre.strip():
                raise Refused("--no-pre needs a reason: --no-pre 'why this tip skips the pre-step'")
            else:
                warn(f"--no-pre: the enqueue's pre-step skipped: {args.no_pre}")
        elif len(args.enqueue) != 2:
            raise Refused("--enqueue takes REPO SHA (or --land WORKTREE)")
        else:
            req = request(*args.enqueue, args.item, args.ship, args.summary)
    except Refused as e:
        err(f"goh land: {e} -- nothing enqueued")
        return 2
    path = enqueue(req)
    if not args.no_start:
        start_lander()
    who = req["item"] or Path(req["repo"]).name
    ok(f"enqueued {who} @ {req['sha'][:8]} ({req['mode']}): {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
