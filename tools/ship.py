#!/usr/bin/env python3
"""goh ship: the detached, time-bounded LOCAL landing pipeline (GOH.SHIP, owner 2026-11-08).

    goh ship [--repo DIR] [--item ID [--progress]] [--summary TEXT]
             [--version X.Y.Z | --bump patch|minor|major|none]
             [--wait] [--out PATH]
    goh ship --status RESULT.json

An agent's contract ends at "committed, local gate green, goh ship started". What it used to do
after its first push -- re-run the gate, release, install, read the claim card, wait -- was 49% of
every agent's context (GOH.SPENDWATCH). Here it is a pipeline no turn watches:

    suite    the fresh INSTRUMENTED full run on the tip the ship releases (the owner's term,
             GOH.COVCARRY), when `.gatesrc` GOH_SHIP_FULL_SUITE=on: lib/ship_full_run.py, run
             holding the land lock, so main cannot move past the run before the release kit
             checks it (GOH.SHIPSTARVE). It judges every coverage floor over what landed since the
             last ship -- a land measures none (GOH.COVOFFLAND) -- and a red run releases nothing:
             a diff coverage red names each uncovered line's land (`diffcov_red`); any other red
             is bisected to its first red land, a fault filed (`bisect`), the record `bisecting`
             meanwhile; a held `.ship.lock` names its holder and state (GOH.SHIPBISECTCOV)
    push     `git push REMOTE HEAD:BRANCH` (the upstream the branch tracks), through the pre-push
             gate; skipped when `.gatesrc` GOH_SHIP_RELEASE_PUSHES=1 and the release step runs: that
             kit pushes the hand-off with its release commit (a repo whose release note may only
             land in the release commit has a hand-off its own gate refuses alone)
    release  the repo's release kit, `.gatesrc` GOH_SHIP_RELEASE, run with GOH_SHIP_VERSION set
             (also `{version}` in the command); skipped when HEAD already carries a version tag; a
             repo that tags none is refused first; then lib/ship_pushed.py: HEAD and its tag pushed
    install  `.gatesrc` GOH_SHIP_INSTALL; skipped (said) when undeclared (a kit may install)
    verify   first, when the remote is GitHub's, the CI verdict on HEAD, read ONCE through the
             throttle-aware door (`loopctl ci SHA --repo OWNER/NAME`), never waited on: hosted CI is
             ADVISORY (owner 2026-11-11; .loop LOOP.CIADVISORY, GOH.CIWAIT). Red (or unread) fails
             here, a finding; queued, running or throttled on a checkout whose pre-push hook is
             goh's push gate passes, said as advisory (`loopctl done` records the run and `loopctl
             tick` re-reads it, naming a red one); with no push-gate hook it fails at once, as done
             would refuse it. Then `loopctl verify REPO`, the claim card (GOH_LOOPCTL; REPO relative
             to LOOPCTL_ROOT or ~/Projects), whose only failing line may be that advisory CI one;
             skipped without loopctl and without --item. A ship that failed HERE resumes here:
             `goh ship --verify RESULT` by hand re-runs verify and, green, the done step, and the
             result reads ok (GOH.SHIPREVERIFY: a transient on the card left a round hand-recorded)
    done     with --item: `loopctl done ID --shipped RESULT`, which records the item from this
             result and the card through done's own gates (CI on what landed), so no agent turn;
             --progress (a slice of a multi-release item): `loopctl progress`, the item left open

PREFLIGHT (GOH.SHIPPREFLIGHT). The kit runs after the push, so a precondition it could read before
(token-pool's "no '## Unreleased' section", another kit's "no row for v5.18.0") was found after.
When the release step will run, plan() first runs goh's CHANGELOG.md reading (the contract
GOH_SHIP_CHANGELOG declares), then `.gatesrc` GOH_SHIP_PREFLIGHT ({version} substituted), in the
repo under the ship's bound with GOH_SHIP_VERSION/SUMMARY/ITEM set; a non-zero exit refuses, nothing
pushed. A new check is a line in the repo's preflight, never a case here (ship_preflight_audit).
Before those, what the LAST steps refused (lib/ship_item.py, GOH.SHIPITEM): an --item loopctl
refuses for this repo (unknown, done, another repo's), and an OLDER stock hook in the repo.
And lines a land left uncovered, unless --allow-diffcov REASON (lib/diffcov_carry.py).

Started without --wait it validates, writes the result JSON (status `running`), detaches a worker
in its own session and prints one line naming the result; the worker prints a ✓/✗/· line per step
to `<result>.log`. Each step runs under `lib/bounded_run.py`: own session, GOH_SHIP_TIMEOUT seconds
(default 3600), then its whole process group TERM'd and KILL'd -- nothing it started is left
running with PPID=1. A failed step stops the pipeline; the result names it (`failed_step`) and
carries the last 40 lines of its log (`tail`), which is all a resumed agent needs. A failed release
takes back the commits and tags it made that no remote holds (`undo`, lib/ship_undo.py).

The next version, when neither --version nor --bump is given, is the maintained tool's answer,
`git-cliff --bumped-version` over the conventional commits since the newest v-tag (a `!` or BREAKING
CHANGE is major, a feat minor; a repo's own cliff.toml [bump] is honoured); a span git-cliff finds
nothing to bump in (no conventional commit) is a patch; with no v-tag there is nothing to bump from,
so a first release with a kit is refused unless --version names it (GOH.FIRSTTAG). No git-cliff on
PATH refuses the ship, naming the install; --version or --bump needs none (required_tools.tsv).
`--bump none` ships with no release: a repo that declares no version (no v-tag), or a HEAD that
already carries its tag; a tagging repo past its newest tag is refused, naming --version X.Y.Z (the
version a commit already bumped, which the kit then tags) and the bumps (GOH.CIWAIT).

THE SHIP'S OWN TIME (GOH.SHIPSTARVE). `land_wait_s` is the seconds the worker waited for the land
lock (the queue's time, not the ship's); `own_s` is the lock taken to `finished`, every step, its
own full run included: the owner's ship budget (`goh budget`, lib/budget.py) judges `own_s`.
`sha` in the result is the LANDED commit: HEAD after the release step, which may commit a version
bump the hand-off never had; `handoff_sha` is the commit the agent handed off.
Exit: 0 started (or, with --wait, shipped), 1 a step failed, 2 refused.
"""

from __future__ import annotations

# ONE GOH PER SHIP (GOH.SHIPPINNED). Run from a checkout, this re-runs from the export of its HEAD
# (C4, gates/_from_head.sh), so the worker, the verify step it starts (`ship.py --verify`, by this
# file's own path) and every step's GOH_DIR are that one immutable tree. They read the live
# checkout before: a consumer's ship, started under v0.109, ran its verify step against v0.110's
# ship.py, which had dropped the flag (`unrecognized arguments: --ci-wait`).
# Pinned by tests/test_ship_pinned.py.
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
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))
import bounded_run
import ship_item
import ship_pushed
import ship_status
import ship_undo
import ship_verify
from ship_preflight import preflights, run_preflights
from ship_version import (
    SEMVER,
    Refused,
    git,
    git_bindings,
    held_lock_refusal,
    newest_tag,
    next_version,
    unbound_env,
)

from tui.lib import err, info, ok, step

STEPS = ("suite", "push", "release", "install", "verify", "done")
save, tail = ship_status.save, ship_status.tail  # the result file, a step log (ship_status)
GRACE, DEFAULT_TIMEOUT = 5, 3600
_HELD: dict[str, float] = {}  # "at": time.monotonic() when this worker took the land lock
KEYS = ("GOH_SHIP_RELEASE", "GOH_SHIP_INSTALL", "GOH_SHIP_TIMEOUT", "GOH_SHIP_RELEASE_PUSHES",
        "GOH_SHIP_PREFLIGHT", "GOH_SHIP_CHANGELOG", "GOH_CHANGELOG_FRAGMENTS",
        "GOH_SHIP_COVERAGE", "GOH_SHIP_QUIET_IDLE_PCT", "GOH_SHIP_QUIET_MAX_S")  # fmt: skip


def config(repo: Path) -> dict[str, str]:
    """The ship keys: the environment's, else what the repo's .gatesrc declares (read in a child,
    as land.sh reads GOH_LAND_GATE). A key set in the environment, even empty, wins."""
    found = {k: os.environ[k] for k in KEYS if k in os.environ}
    rc = repo / ".gatesrc"
    if rc.is_file() and len(found) < len(KEYS):
        script = ". ./.gatesrc >/dev/null 2>&1 </dev/null; " + "; ".join(
            f'printf "%s\\0" "${{{k}-}}"' for k in KEYS)  # fmt: skip
        env = {k: v for k, v in os.environ.items() if k not in KEYS}
        out = subprocess.run(["bash", "-c", script], cwd=repo, env=env, capture_output=True,
                             text=True, stdin=subprocess.DEVNULL, check=False,
                             timeout=30)  # fmt: skip
        values = out.stdout.split("\0")
        for k, v in zip(KEYS, values, strict=False):
            found.setdefault(k, v)
    return {k: found.get(k, "") for k in KEYS}


def default_out(name: str, sha: str) -> Path:
    base = Path(os.environ.get("GOH_SHIP_DIR") or Path.home() / ".local/state/goh/ship")
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return base / name.replace("/", "_") / f"{stamp}-{sha[:8]}.json"


def loopctl() -> Path:
    return Path(os.environ.get("GOH_LOOPCTL") or Path.home() / "Projects/.loop/tools/loopctl.py")


def plan(args: argparse.Namespace) -> dict:
    """Everything decided before a worker starts; a Refused here starts nothing."""
    top = git(Path(args.repo or "."), "rev-parse", "--show-toplevel")
    if not top:
        raise Refused(f"{args.repo or os.getcwd()} is not a git checkout")
    repo = Path(top)
    if git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise Refused(f"{repo}: uncommitted changes to tracked files -- commit first, then ship")
    upstream = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if not upstream:
        raise Refused(f"{repo}: the branch tracks no remote branch; nothing to push to")
    remote, _, branch = upstream.partition("/")
    if args.progress and not args.item:
        raise Refused("--progress needs --item: it records a slice of that item as PROGRESS")
    root = Path(os.environ.get("LOOPCTL_ROOT") or Path.home() / "Projects")
    try:
        name = str(repo.resolve().relative_to(root.resolve()))
    except ValueError:
        name = repo.name
    why = ship_item.item_refusal(repo, loopctl(), args.item, name) if args.item else None
    why = why or ship_item.hook_refusal(repo, ROOT)
    if why:
        raise Refused(why)
    cfg = config(repo)
    if cfg["GOH_SHIP_COVERAGE"]:  # GOH.COVOFFLAND: coverage measured nowhere, so nothing released
        raise Refused("coverage in dummy mode; no release" if cfg["GOH_SHIP_COVERAGE"] == "dummy"
                      else f"GOH_SHIP_COVERAGE={cfg['GOH_SHIP_COVERAGE']!r}: the one value is "
                      "'dummy'")  # fmt: skip
    tag = newest_tag(repo)
    sha = git(repo, "rev-parse", "HEAD")
    if why := ship_item.lock_refusal(out := Path(args.out) if args.out else default_out(name, sha)):
        raise Refused(why)  # a held .ship.lock names its holder and state (GOH.SHIPBISECTCOV)
    if tag and not cfg["GOH_SHIP_RELEASE"]:
        raise Refused(f"{repo.name} tags releases ({tag}) but declares no GOH_SHIP_RELEASE in its "
                      ".gatesrc: name its release kit, e.g. GOH_SHIP_RELEASE='tools/release.sh "
                      "{version}'")  # fmt: skip
    version = next_version(repo, tag, args.version, args.bump, bool(cfg["GOH_SHIP_RELEASE"]))
    import diffcov_carry  # lib/, here: tools/ship.py's import ceiling (E402) is spent

    if why := diffcov_carry.ship_refusal(repo, tag, sha, allow := args.allow_diffcov):
        raise Refused(why)
    try:
        timeout = int(cfg["GOH_SHIP_TIMEOUT"] or DEFAULT_TIMEOUT)
    except ValueError:
        raise Refused(f"GOH_SHIP_TIMEOUT={cfg['GOH_SHIP_TIMEOUT']!r} is not seconds") from None
    asked = preflights(repo, cfg, version, args.summary or "")
    run_preflights(repo, asked, version, args, timeout)
    return {
        "schema": 1, "repo": str(repo), "name": name, "item": args.item or None,
        "progress": bool(args.progress),
        "summary": args.summary or "", "remote": remote, "branch": branch, "sha": sha,
        "handoff_sha": sha,
        "base_tag": tag, "version": version, "bump": args.bump,
        "release": cfg["GOH_SHIP_RELEASE"], "install": cfg["GOH_SHIP_INSTALL"],
        "release_pushes": cfg["GOH_SHIP_RELEASE_PUSHES"] == "1",
        "preflight": [c for w, c in asked if w == "GOH_SHIP_PREFLIGHT"],
        "timeout_s": timeout, "loopctl": str(loopctl()), "result": str(out),
        "status": "running", "started": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "steps": [], "failed_step": None, "tail": [], "undo": [], "pid": None,
        "load1": os.getloadavg()[0],  # the box's load at the ship's START, for the budget's row
        "allow_diffcov": allow, "quiet_settings": ship_status.quiet_settings(cfg),
    }  # fmt: skip


def profiles_file(res: dict) -> str:
    """Where the suite step writes its reused and fresh binary tests (lib/ship_full_run.py)."""
    return str(Path(res["result"]).with_suffix("")) + ".suite.profiles.json"


def profiles(res: dict) -> list | None:
    """The suite step's reused and fresh binary tests per binary; None when it wrote none."""
    try:
        got = json.loads(Path(profiles_file(res)).read_text(encoding="utf-8")).get("binaries")
    except (OSError, ValueError, AttributeError):
        return None
    return got if isinstance(got, list) else None


def command(res: dict, name: str) -> tuple[str | None, str]:
    """The shell command for step NAME, or None and why it is skipped."""
    lc = shlex.quote(res["loopctl"])
    py = shlex.quote(sys.executable)
    if name == "suite":  # GOH.SHIPSTARVE: the tip's own instrumented full run, under the land lock
        import ship_full_run  # here, not at the top: tools/ship.py's import ceiling (E402) is spent

        repo = Path(res["repo"])
        if why := ship_full_run.owed(repo):
            return None, why
        me, wt = ROOT / "lib" / "ship_full_run.py", Path(res["result"]).parent / "suite-wt"
        counts = shlex.quote(profiles_file(res))  # GOH.COVPROFILEREUSE: reused vs fresh, per binary
        return f"{py} {shlex.quote(str(me))} --repo {shlex.quote(str(repo))} --wt " + shlex.quote(
            str(wt)
        ) + f" --profiles-out {counts}", ""
    if name == "push":
        if res.get("release_pushes") and command(res, "release")[0] is not None:
            return None, ("the release kit pushes the hand-off with its release "
                          "(GOH_SHIP_RELEASE_PUSHES=1)")  # fmt: skip
        return f"git push {shlex.quote(res['remote'])} HEAD:refs/heads/{res['branch']}", ""
    if name == "release":  # lock-order-runs: GOH_SHIP_RELEASE
        if not res["release"]:
            return None, "the repo tags nothing and declares no GOH_SHIP_RELEASE"
        if res.get("bump") == "none":
            kept = f"the version stays {res['base_tag']}" if res["base_tag"] else "no version"
            return None, f"--bump none: no release ({kept})"
        held = [t for t in git(Path(res["repo"]), "tag", "--points-at", "HEAD").splitlines()
                if SEMVER.match(t)]  # fmt: skip
        if held:
            return None, f"HEAD already carries {held[0]}"
        return ship_pushed.after(res["release"].replace("{version}", res["version"]), res), ""
    if name == "install":  # lock-order-runs: GOH_SHIP_INSTALL
        return (res["install"] or None), "no GOH_SHIP_INSTALL declared"
    have = Path(res["loopctl"]).is_file()
    if name == "verify":
        if not have and not res["item"]:
            return None, f"no loopctl at {res['loopctl']} and no --item"
        me = shlex.quote(str(Path(__file__).resolve()))
        return f"{py} {me} --verify {shlex.quote(res['result'])}", ""
    if not res["item"]:
        return None, "no --item to record"
    verb = "progress" if res.get("progress") else "done"  # a slice stays open (LOOP.SHIPSLICE)
    return f"{py} {lc} {verb} {shlex.quote(res['item'])} --shipped {shlex.quote(res['result'])}", ""


def run_steps(res: dict, first: str = STEPS[0]) -> int:
    """Run the pipeline from step FIRST in this process; a mark per step; 0 shipped, 1 failed."""
    os.chdir(res["repo"])
    for k in git_bindings():
        os.environ.pop(k, None)
    os.environ.update(GOH_SHIP_VERSION=res["version"], GOH_SHIP_RESULT=res["result"],
                      GOH_SHIP_SUMMARY=res["summary"], GOH_SHIP_ITEM=res["item"] or "")  # fmt: skip
    # THE SHIP'S CLASS (GOH.SLOTPRIORITY): the push gate a step's `git push` runs is admitted as
    # `ship` -- the ship declares it, the gate never guesses from its command line; consumed by the
    # first admission in each step, so nothing that gate starts inherits it.
    os.environ["GOH_GATE_CLASS"] = "ship"
    stem = Path(res["result"]).with_suffix("")
    for name in STEPS[STEPS.index(first) :]:
        cmd, why = command(res, name)
        if cmd is None:
            res["steps"].append({"name": name, "status": "skipped", "why": why})
            save(res)
            step(f"{name}: skipped ({why})")
            continue
        log, prof = Path(f"{stem}.{name}.log"), ship_status.profile(stem, name)
        log.write_text(f"$ {cmd}\n", encoding="utf-8")
        t0, ticks0 = time.monotonic(), ship_status.ticks()  # the step's own idle (GOH.SHIPQUIET)
        snap = ship_undo.snapshot(Path(res["repo"])) if name == "release" else None
        out = bounded_run.run_step(["bash", "-c", cmd], res["timeout_s"], GRACE, f"ship {name}",
                                   str(log))  # fmt: skip
        secs = round(time.monotonic() - t0, 1)
        entry = {"name": name, "code": out.code, "seconds": secs, "log": str(log), "timings": prof,
                 **ship_status.step_load(ticks0)}  # fmt: skip
        if name == "suite" and (counted := profiles(res)) is not None:  # reused vs fresh
            entry["profiles"] = counted
        if out.code == 0 and not out.timed_out:
            entry["status"] = "ok"
            res["steps"].append(entry)
            if name == "release":
                res["sha"] = git(Path(res["repo"]), "rev-parse", "HEAD") or res["sha"]
            ship_status.carry(res)  # the canary's grace-end finding, the consumers' pins
            save(res)
            ok(f"{name} ({secs} s)")
            continue
        entry["status"] = "timed_out" if out.timed_out else "failed"
        if out.timed_out:
            with log.open("a", encoding="utf-8") as f:
                f.write(f"TIMED OUT after {res['timeout_s']}s: its process group was killed\n")
        if snap:  # a failed release leaves the checkout as it found it (GOH.SHIPUNDO)
            res["undo"] = ship_undo.undo(Path(res["repo"]), snap, res["remote"], log)
        res["steps"].append(entry)
        res.update(status="failed", failed_step=name, tail=tail(log))
        res["finished"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        own_run(res)
        save(res)
        ship_status.record_ship_timing(res)
        err(f"{name} {entry['status']} (exit {out.code}, {secs} s): {res['result']}")
        return 1
    res.update(status="ok", finished=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"))
    own_run(res)
    save(res)
    ship_status.record_ship_timing(res)
    return 0


def own_run(res: dict) -> None:
    """`own_s`: the land lock taken to now, this worker's run (a resumed ship adds its own)."""
    if "at" in _HELD:
        res["own_s"] = round(res.get("own_s", 0.0) + time.monotonic() - _HELD.pop("at"), 1)


def worker(result: str, resume: bool = False) -> int:
    """The pipeline under the repo's ship lock; RESUME: a ship failed at verify, from verify."""
    res = json.loads(Path(result).read_text(encoding="utf-8"))
    lock = Path(res["result"]).parent / ".ship.lock"
    import ship_lock  # here, not at the top: tools/ship.py's import ceiling (E402) is spent

    with lock.open("a+") as held:  # never "w": that would empty the holder's name (ship_lock)
        if who := ship_lock.take(held, res["result"]):  # never waits: a second ship is refused
            res.update(status="failed", failed_step="lock", tail=[f"{lock} is held: {who}"])
            save(res)
            err(res["tail"][0])
            return 1
        if resume and not ship_verify.reopen(res):
            return ship_verify.verify(result)  # resumed by a peer meanwhile: only read the card
        res["pid"] = os.getpid()
        import pid_owner  # here, not at the top: the import ceiling (E402) is spent

        res["pid_stamp"] = pid_owner.stamp_line(os.getpid())  # a reused pid is not this worker
        save(res)
        import land_lock  # here, not at the top: tools/ship.py's import ceiling (E402) is spent
        import ship_full_run  # the same: a red suite run's bisect, a flake's retry

        ship_status.quiet_wait(res, resume)  # GOH.SHIPQUIET: bounded, for a quiet host
        queue, who = land_lock.path(Path(res["repo"])), f"goh ship {res['name']} @ {res['sha'][:8]}"
        for retry in (False, True):  # a flake's one retry of the suite step (GOH.BISECTRECHECK)
            asked = time.monotonic()
            with land_lock.held(queue, who, info):  # lock-order: land, under the ship lock
                _HELD["at"] = time.monotonic()  # the queue's time ends, the ship's own begins
                res["land_wait_s"] = round(res.get("land_wait_s", 0.0) + _HELD["at"] - asked, 1)
                rc = run_steps(res, "verify" if resume else STEPS[0])
            if not rc or resume or not ship_full_run.retry_after_red(res, save, retry):
                return rc  # a red suite run is bisected with the land lock given back
        return rc


def start(args: argparse.Namespace) -> int:
    try:
        res = plan(args)
    except Refused as e:
        err(f"goh ship: {e} -- nothing started")
        return 2
    save(res)
    if args.wait:
        return worker(res["result"])
    log = Path(res["result"]).with_suffix(".log")
    with log.open("ab") as out:
        worker_argv = [sys.executable, str(Path(__file__).resolve()), "--worker", res["result"]]
        subprocess.Popen(worker_argv, cwd=res["repo"], env=unbound_env(), stdout=out, stderr=out,
                         stdin=subprocess.DEVNULL, start_new_session=True,
                         close_fds=True)  # fmt: skip
    info(f"goh ship started: {res['result']}")
    # GOH.TOOLTIMEOUT: say why it returned at once, so no caller re-runs it in the foreground
    info(f"goh ship runs long (push gate, release, install, verify): detached; "
         f"`goh ship --status {res['result']}` reads its marks, --wait runs it here")  # fmt: skip
    return 0


@held_lock_refusal(err)  # GOH.SHIPORDERERR
def main(argv: list[str] | None = None) -> int:
    given = sys.argv[1:] if argv is None else argv
    if given[:1] == ["verdict"]:  # the recorded gate verdict (GOH.VERDICTAPI)
        from ship_verdict import main as verdict

        return verdict(given[1:])
    ap = argparse.ArgumentParser(prog="goh ship", description=__doc__.splitlines()[0])
    ap.add_argument("--repo", help="the checkout to ship (default: the current one)")
    ap.add_argument("--item", help="the queue item `loopctl done` records on success")
    ap.add_argument("--progress", action="store_true",
                    help="with --item: one slice: PROGRESS, the item left open")  # fmt: skip
    ap.add_argument("--summary", help="what landed, one line (the item's result)")
    ap.add_argument("--version", help="the release version X.Y.Z (default: from the commits)")
    ap.add_argument("--bump", choices=("patch", "minor", "major", "none"),
                    help="the next version's part, or none: ship with no release (no version, "
                         "or a HEAD already tagged)")  # fmt: skip
    ap.add_argument("--allow-diffcov", metavar="REASON", help="ship despite uncovered lines")
    ap.add_argument("--wait", action="store_true", help="run in the foreground, a mark per step")
    ap.add_argument("--out", help="the result JSON (default ~/.local/state/goh/ship/REPO/...)")
    ap.add_argument("--status", metavar="RESULT", help="print a result's marks")
    ap.add_argument("--worker", metavar="RESULT", help=argparse.SUPPRESS)
    ap.add_argument("--verify", metavar="RESULT", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.verify:  # a re-run of a ship failed at verify resumes it there (GOH.SHIPREVERIFY)
        if ship_verify.reopen(json.loads(Path(args.verify).read_text(encoding="utf-8")), dry=True):
            return worker(args.verify, resume=True)
        return ship_verify.verify(args.verify)
    if args.worker:
        return worker(args.worker)
    if args.status:
        return ship_status.status(args.status)
    return start(args)


if __name__ == "__main__":
    sys.exit(main())
