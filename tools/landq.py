#!/usr/bin/env python3
"""goh land: the landing queue (GOH.LANDQUEUE, owner 2026-11-11).

    goh land --enqueue REPO SHA [--item ID] [--ship [--summary TEXT]] [--no-start]
    goh land --drain        # the lander: run every request in order, then exit
    goh land --status       # what is pending, running, and how each finished

WHY. Every agent ran its own push gate, so its lifetime was the machine's queue: pushes measured
in this session's transcripts took 1197 s, 1196 s, 1298 s and 1523 s (the gate itself, run under
three other gates' load: a private consumer's `make ci` hook alone was 1276 s of its 1298 s),
and one waited 1308 s for a gate slot before it began. The agent sat idle through all of it.
Now an agent ends at "committed and enqueued"; what follows is nobody's turn.

THE REQUEST is one JSON file under `<dir>/pending/` (GOH_LANDQ_DIR, default
~/.local/state/goh/landq), named by enqueue time so a listing is the order. It names the repo, the
SHA, the remote and branch the checkout tracks, the item, and the mode:
    push  `git push REMOTE SHA:refs/heads/BRANCH` -- exactly the enqueued commit, through the repo's
          pre-push gate (which takes the machine-wide gate slot itself, lib/gate_slot.sh)
    ship  `tools/ship.py --wait --repo REPO --item ID` (push, release, install, verify, done); the
          checkout's HEAD must still BE the sha, or the ship would release a different commit
A red gate pushes nothing: git refuses the push when its pre-push hook fails.

THE LANDER is one process: it holds an flock on `<dir>/lander.lock`, so a second exits at once. It
moves a request to `running/`, runs it under lib/bounded_run.py (GOH_LAND_TIMEOUT, default 3600 s,
its whole process group swept on expiry), writes `done/<name>.json` and the log under `logs/`, and
records the outcome on the item: `loopctl set ID landing {"state": "landed"|"red", "sha", "log",
"at"} --json`, which the coordinator reads on its tick and re-dispatches a red one with the log.
After the last request it releases the lock and looks once more: a request enqueued in that
window either sees the lock free and starts its own lander, or is seen here.

WHERE IT RUNS, AND A REBOOT (decided:
docs/decisions/D-0006-a-landing-queue-ends-the-agent-at-enqueue.md). Not a launchd agent:
`--enqueue` starts a detached lander (own session) when none holds the lock, and the
coordinator's tick may run `goh land --drain` to restart one. A request survives a reboot because
it is a file: one a dead lander left in `running/` is moved back to the front of `pending/` by the
next lander and run again (a push of a sha the remote already has is a no-op, and lands).

Pinned by tests/test_landq.py.
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
import datetime as dt
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
lock_order = importlib.import_module("lock_order")
_tui = importlib.import_module("tui.lib")
err, info, ok, warn = _tui.err, _tui.info, _tui.ok, _tui.warn

GRACE = 5
DEFAULT_TIMEOUT = 3600
DIRS = ("pending", "running", "done", "logs")


class Refused(Exception):
    pass


def qdir() -> Path:
    return Path(os.environ.get("GOH_LANDQ_DIR") or Path.home() / ".local/state/goh/landq")


def loopctl() -> Path:
    return Path(os.environ.get("GOH_LOOPCTL") or Path.home() / "Projects/.loop/tools/loopctl.py")


def git(repo: Path, *args: str) -> str:
    done = gitrun.run_reader(args, repo=repo, timeout=60)
    return done.stdout.strip() if done.returncode == 0 else ""


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_bindings() -> tuple[str, ...]:
    """git's own list of the variables binding it to one repository (checks/_gitutil.py)."""
    sys.path.insert(0, str(ROOT / "checks"))
    from _gitutil import local_env_vars

    return local_env_vars()


def request(repo_arg: str, sha_arg: str, item: str, ship: bool, summary: str) -> dict:
    """The request for SHA in REPO, refused when the checkout cannot land it."""
    top = git(Path(repo_arg), "rev-parse", "--show-toplevel")
    if not top:
        raise Refused(f"{repo_arg} is not a git checkout")
    repo = Path(top)
    found = gitrun.run_reader(
        ("rev-parse", "--verify", f"{sha_arg}^{{commit}}"), repo=repo, timeout=60
    )
    sha = found.stdout.strip() if found.returncode == 0 else ""
    if not sha:
        raise Refused(
            f"{repo}: no commit {sha_arg} in this checkout ({found.stderr.strip()}) -- "
            "commit it, then enqueue"
        )
    upstream = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if not upstream:
        raise Refused(f"{repo}: the branch tracks no remote branch; nothing to push to")
    remote, _, branch = upstream.partition("/")
    if ship and not item:
        raise Refused("--ship needs --item: the ship records that item done")
    return {"repo": str(repo), "sha": sha, "remote": remote, "branch": branch, "item": item,
            "mode": "ship" if ship else "push", "summary": summary,
            "enqueued_at": now()}  # fmt: skip


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
    ref = f"{req['sha']}:refs/heads/{req['branch']}"
    return [*env, "git", "-C", req["repo"], "push", req["remote"], ref]


def run_one(q: Path, path: Path) -> dict:
    """Run one request already in running/; return its outcome."""
    req = json.loads(path.read_text())
    log = q / "logs" / f"{path.stem}.log"
    outcome = {"sha": req["sha"], "repo": req["repo"], "mode": req["mode"], "log": str(log)}
    head = git(Path(req["repo"]), "rev-parse", "HEAD")
    if req["mode"] == "ship" and head != req["sha"]:
        log.write_text(
            f"HEAD is {head or '(unreadable)'}, not the enqueued {req['sha']}: a ship releases "
            "HEAD, so it would land a different commit -- enqueue again\n"
        )
        return {**outcome, "state": "red", "why": "HEAD moved"}
    timeout = int(os.environ.get("GOH_LAND_TIMEOUT") or DEFAULT_TIMEOUT)
    info(f"land {Path(req['repo']).name} @ {req['sha'][:8]} ({req['mode']}) -> {log}")
    res = bounded_run.run_step(argv_for(req), timeout, GRACE, f"land {req['mode']}", str(log))
    if res.code == 0 and not res.timed_out:
        return {**outcome, "state": "landed"}
    why = f"timed out after {timeout}s" if res.timed_out else f"exit {res.code}"
    return {**outcome, "state": "red", "why": why}


def record(req_path: Path, outcome: dict, item: str) -> None:
    outcome = {**outcome, "at": now()}
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
    write_json(req_path.parent.parent / "done" / req_path.name, outcome)
    mark = ok if outcome["state"] == "landed" else err
    mark(f"{item or Path(outcome['repo']).name}: {outcome['state']} @ {outcome['sha'][:8]}")


def drain_held(q: Path) -> None:
    # A request a dead lander was running goes back to the front: its name keeps its place.
    for stale in sorted((q / "running").glob("*.json")):
        stale.replace(q / "pending" / stale.name)
        warn(f"{stale.name}: its lander died mid-request -- running it again")
    while batch := sorted((q / "pending").glob("*.json")):
        path = q / "running" / batch[0].name
        batch[0].replace(path)
        item = json.loads(path.read_text()).get("item", "")
        try:
            outcome = run_one(q, path)
        except Exception as e:  # noqa: BLE001 -- one broken request must not strand the rest
            outcome = {"sha": "", "repo": "", "state": "red", "why": f"lander: {e}", "log": ""}
        record(path, outcome, item)
        path.unlink(missing_ok=True)


def drain() -> int:
    q = qdir()
    for d in DIRS:
        (q / d).mkdir(parents=True, exist_ok=True)
    while True:
        fd = take_lock(q)
        if fd is None:
            info(f"a lander already holds {q / 'lander.lock'}")
            return 0
        try:
            drain_held(q)
        finally:
            os.close(fd)
        if not any((q / "pending").glob("*.json")):  # enqueued after the last look: ours to run
            return 0


def status() -> int:
    q = qdir()
    for d in ("pending", "running"):
        for f in sorted((q / d).glob("*.json")) if (q / d).is_dir() else []:
            r = json.loads(f.read_text())
            info(
                f"{d}: {r.get('item') or '-'} {Path(r['repo']).name} @ {r['sha'][:8]} ({r['mode']})"
            )
    for f in sorted((q / "done").glob("*.json"))[-10:] if (q / "done").is_dir() else []:
        r = json.loads(f.read_text())
        info(f"done: {f.stem} {r['state']} @ {r['sha'][:8]} {r.get('log', '')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goh land", description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--enqueue", nargs=2, metavar=("REPO", "SHA"))
    mode.add_argument("--drain", action="store_true")
    mode.add_argument("--status", action="store_true")
    ap.add_argument("--item", default="")
    ap.add_argument("--ship", action="store_true", help="run goh ship --wait, not a bare push")
    ap.add_argument("--summary", default="")
    ap.add_argument("--no-start", action="store_true", help="enqueue only; start no lander")
    args = ap.parse_args(argv)
    if args.drain:
        return drain()
    if args.status:
        return status()
    try:
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
