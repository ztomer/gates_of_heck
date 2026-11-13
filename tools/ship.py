#!/usr/bin/env python3
"""goh ship: the detached, time-bounded LOCAL landing pipeline (GOH.SHIP, owner 2026-11-08).

    goh ship [--repo DIR] [--item ID] [--summary TEXT]
             [--version X.Y.Z | --bump patch|minor|major|none]
             [--wait] [--out PATH]
    goh ship --status RESULT.json

An agent's contract ends at "committed, local gate green, goh ship started". What it used to do
after its first push -- re-run the gate, release, install, read the claim card, wait -- was 49% of
every agent's context (GOH.SPENDWATCH). Here it is a pipeline no turn watches:

    push     `git push REMOTE HEAD:BRANCH` (the upstream the branch tracks), through the pre-push
             gate; skipped when `.gatesrc` GOH_SHIP_RELEASE_PUSHES=1 and the release step runs: that
             kit pushes the hand-off with its release commit (a repo whose release note may only
             land in the release commit has a hand-off its own gate refuses alone)
    release  the repo's release kit, `.gatesrc` GOH_SHIP_RELEASE, run with GOH_SHIP_VERSION set (also
             `{version}` in the command); skipped when HEAD already carries a version tag; a repo
             that tags none is refused first; then lib/ship_pushed.py: HEAD and its tag are pushed
    install  `.gatesrc` GOH_SHIP_INSTALL; skipped (said) when undeclared (a kit may install)
    verify   first, when the remote is GitHub's, the CI verdict on HEAD, read ONCE through the
             throttle-aware door (`loopctl ci SHA --repo OWNER/NAME`), never waited on: hosted CI is
             ADVISORY (owner 2026-11-11; .loop LOOP.CIADVISORY, GOH.CIWAIT). Red (or unread) fails
             here, a finding; queued, running or throttled on a checkout whose pre-push hook is
             goh's push gate passes, said as advisory (`loopctl done` records the run and `loopctl
             tick` re-reads it, naming a red one); with no push-gate hook it fails at once, as done
             would refuse it. Then `loopctl verify REPO`, the claim card (GOH_LOOPCTL; REPO relative
             to LOOPCTL_ROOT or ~/Projects), whose only failing line may be that advisory CI one;
             skipped without loopctl and without --item
    done     with --item: `loopctl done ID --shipped RESULT`, which records the item from this
             result and the card through done's own gates (CI on what landed), so no agent turn

PREFLIGHT (GOH.SHIPPREFLIGHT). The kit runs after the push, so a precondition it could read before
(token-pool's "no '## Unreleased' section", another kit's "no row for v5.18.0") was found after.
When the release step will run, plan() first runs goh's CHANGELOG.md reading (the contract
GOH_SHIP_CHANGELOG declares), then `.gatesrc` GOH_SHIP_PREFLIGHT ({version} substituted), in the
repo under the ship's bound with GOH_SHIP_VERSION/SUMMARY/ITEM set; a non-zero exit refuses, nothing
pushed. A new check is a line in the repo's preflight, never a case here (ship_preflight_audit).

Started without --wait it validates, writes the result JSON (status `running`), detaches a worker
in its own session and prints one line naming the result; the worker prints a ✓/✗/· line per step
to `<result>.log`. Each step runs under `lib/bounded_run.py`: own session, GOH_SHIP_TIMEOUT seconds
(default 3600), then its whole process group TERM'd and KILL'd -- nothing it started is left
running with PPID=1. A failed step stops the pipeline; the result names it (`failed_step`) and
carries the last 40 lines of its log (`tail`), which is all a resumed agent needs.

The next version, when neither --version nor --bump is given, is the maintained tool's answer,
`git-cliff --bumped-version` over the conventional commits since the newest v-tag (a `!` or BREAKING
CHANGE is major, a feat minor; a repo's own cliff.toml [bump] is honoured); a span git-cliff finds
nothing to bump in (no conventional commit) is a patch. No git-cliff on PATH refuses the ship,
naming the install; --version or --bump needs none (gates/required_tools.tsv, layer `ship`).
`--bump none` ships with no release: a repo that declares no version (no v-tag), or a HEAD that
already carries its tag; a tagging repo past its newest tag is refused, naming --version X.Y.Z (the
version a commit already bumped, which the kit then tags) and the bumps (GOH.CIWAIT).

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
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))
import bounded_run
import gitrun
import ship_changelog
import ship_pushed
import ship_verify

from tui.lib import err, info, ok, step

STEPS = ("push", "release", "install", "verify", "done")
TAIL = 40
GRACE = 5
DEFAULT_TIMEOUT = 3600
KEYS = ("GOH_SHIP_RELEASE", "GOH_SHIP_INSTALL", "GOH_SHIP_TIMEOUT", "GOH_SHIP_RELEASE_PUSHES",
        "GOH_SHIP_PREFLIGHT", "GOH_SHIP_CHANGELOG")  # fmt: skip
SEMVER = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def git_bindings() -> tuple[str, ...]:
    """The variables that bind git to one repository: git's own list (`--local-env-vars`,
    checks/_gitutil.py), never a hand copy -- the copy here held 6 of git's 15 (GOH.ENVLEAK)."""
    sys.path.insert(0, str(ROOT / "checks"))
    from _gitutil import local_env_vars

    return local_env_vars()


class Refused(Exception):
    pass


def git(repo: Path, *args: str) -> str:
    done = gitrun.run_reader(args, repo=repo, timeout=60)
    return done.stdout.strip() if done.returncode == 0 else ""


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
                             text=True, stdin=subprocess.DEVNULL, check=False, timeout=30)  # fmt: skip
        values = out.stdout.split("\0")
        for k, v in zip(KEYS, values, strict=False):
            found.setdefault(k, v)
    return {k: found.get(k, "") for k in KEYS}


def newest_tag(repo: Path) -> str:
    for tag in git(repo, "tag", "--list", "v*", "--sort=-v:refname").splitlines():
        if SEMVER.match(tag):
            return tag
    return ""


CLIFF_TAGS = r"^v[0-9]+\.[0-9]+\.[0-9]+$"  # SEMVER, as git-cliff's --tag-pattern


def cliff_version(repo: Path, tag: str) -> str:
    """git-cliff's next version after TAG (X.Y.Z), or "" when it finds nothing to bump."""
    if not shutil.which("git-cliff"):
        raise Refused("the next version is git-cliff's answer and git-cliff is not on PATH: "
                      "brew install git-cliff (or pass --version X.Y.Z / --bump KIND)")  # fmt: skip
    done = subprocess.run(["git-cliff", "--bumped-version", "--tag-pattern", CLIFF_TAGS],
                          cwd=repo, env=unbound_env(), capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, check=False, timeout=120)  # fmt: skip
    lines = done.stdout.split()
    if done.returncode != 0 or not lines or not SEMVER.match(lines[-1]):
        why = (done.stderr.strip().splitlines() or [f"exit {done.returncode}"])[-1]
        raise Refused(f"git-cliff --bumped-version gave no vX.Y.Z: {why}")
    return "" if lines[-1] == tag else lines[-1].removeprefix("v")


def next_version(repo: Path, tag: str, version: str | None, bump: str | None) -> str:
    if version:
        if bump:
            raise Refused(f"--version {version} and --bump {bump} both name the version: pass one")
        if not re.fullmatch(r"\d+\.\d+\.\d+", version.removeprefix("v")):
            raise Refused(f"--version {version!r} is not X.Y.Z")
        return version.removeprefix("v")
    if bump == "none":
        held = [t for t in git(repo, "tag", "--points-at", "HEAD").splitlines() if SEMVER.match(t)]
        if tag and not held:
            raise Refused(f"--bump none ships no release, but {repo.name} tags releases and HEAD "
                          f"is past {tag}, so it would land untagged: pass --version X.Y.Z for "
                          "the version a commit already bumped (the kit tags it), or --bump "
                          "patch|minor|major to cut the next")  # fmt: skip
        return ""
    m = SEMVER.match(tag)
    if not m:
        return ""
    major, minor, patch = (int(x) for x in m.groups())
    if not bump:
        found = cliff_version(repo, tag)
        if found:
            return found
        bump = "patch"
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def default_out(name: str, sha: str) -> Path:
    base = Path(os.environ.get("GOH_SHIP_DIR") or Path.home() / ".local/state/goh/ship")
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return base / name.replace("/", "_") / f"{stamp}-{sha[:8]}.json"


def loopctl() -> Path:
    return Path(os.environ.get("GOH_LOOPCTL") or Path.home() / "Projects/.loop/tools/loopctl.py")


def preflights(repo: Path, cfg: dict[str, str], version: str, summary="") -> list[tuple[str, str]]:
    """The commands asked before the push when the release step will run, in order: goh's
    changelog reading (GOH.SHIPUNRELEASED: routines' kit said `Unreleased is empty: nothing to
    release` after its push), then the repo's GOH_SHIP_PREFLIGHT, each as (whose, command). None
    when no release runs."""
    if not (cfg["GOH_SHIP_RELEASE"] and version):
        return []
    if any(SEMVER.match(t) for t in git(repo, "tag", "--points-at", "HEAD").splitlines()):
        return []
    try:
        out = ship_changelog.reading(repo, cfg["GOH_SHIP_CHANGELOG"], version, summary)
    except ValueError as e:
        raise Refused(str(e)) from None
    if cfg["GOH_SHIP_PREFLIGHT"].strip():
        out.append(("GOH_SHIP_PREFLIGHT", cfg["GOH_SHIP_PREFLIGHT"].replace("{version}", version)))
    return out


def run_preflights(repo: Path, cmds: list[tuple[str, str]], version: str, args: argparse.Namespace,
                   timeout: int) -> None:  # fmt: skip
    """Run each preflight under the ship's bound; the first refusal raises Refused, naming it."""
    env = ["env", *(f"-u{k}" for k in git_bindings()), f"GOH_SHIP_VERSION={version}",
           f"GOH_SHIP_SUMMARY={args.summary or ''}",
           f"GOH_SHIP_ITEM={args.item or ''}"]  # fmt: skip
    cd = f"cd {shlex.quote(str(repo))} || exit 2; "
    for whose, cmd in cmds:
        fd, logname = tempfile.mkstemp(prefix="goh-ship-preflight.", suffix=".log")
        os.close(fd)
        log = Path(logname)
        try:
            argv = [*env, "bash", "-c", cd + cmd]
            out = bounded_run.run_step(argv, timeout, GRACE, "ship preflight",
                                       str(log))  # fmt: skip
            said = [ln for ln in tail(log) if ln.strip()][-8:]
        finally:
            log.unlink(missing_ok=True)
        if out.code == 0 and not out.timed_out:
            continue
        what = f"timed out after {timeout}s" if out.timed_out else f"exit {out.code}"
        raise Refused(
            f"{repo.name}: the release preflight ({whose}) refuses v{version} before anything is "
            f"pushed ({what}): `{cmd}`\n  " + "\n  ".join(said or ["(it printed nothing)"])
        )


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
    cfg = config(repo)
    tag = newest_tag(repo)
    sha = git(repo, "rev-parse", "HEAD")
    if tag and not cfg["GOH_SHIP_RELEASE"]:
        raise Refused(f"{repo.name} tags releases ({tag}) but declares no GOH_SHIP_RELEASE in its "
                      ".gatesrc: name its release kit, e.g. GOH_SHIP_RELEASE='tools/release.sh "
                      "{version}'")  # fmt: skip
    version = next_version(repo, tag, args.version, args.bump)
    try:
        timeout = int(cfg["GOH_SHIP_TIMEOUT"] or DEFAULT_TIMEOUT)
    except ValueError:
        raise Refused(f"GOH_SHIP_TIMEOUT={cfg['GOH_SHIP_TIMEOUT']!r} is not seconds") from None
    asked = preflights(repo, cfg, version, args.summary or "")
    run_preflights(repo, asked, version, args, timeout)
    root = Path(os.environ.get("LOOPCTL_ROOT") or Path.home() / "Projects")
    try:
        name = str(repo.resolve().relative_to(root.resolve()))
    except ValueError:
        name = repo.name
    out = Path(args.out) if args.out else default_out(name, sha)
    return {
        "schema": 1, "repo": str(repo), "name": name, "item": args.item or None,
        "summary": args.summary or "", "remote": remote, "branch": branch, "sha": sha, "handoff_sha": sha,
        "base_tag": tag, "version": version, "bump": args.bump,
        "release": cfg["GOH_SHIP_RELEASE"], "install": cfg["GOH_SHIP_INSTALL"],
        "release_pushes": cfg["GOH_SHIP_RELEASE_PUSHES"] == "1",
        "preflight": [c for w, c in asked if w == "GOH_SHIP_PREFLIGHT"],
        "timeout_s": timeout, "loopctl": str(loopctl()), "result": str(out),
        "status": "running", "started": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "steps": [], "failed_step": None, "tail": [], "pid": None,
    }  # fmt: skip


def save(res: dict) -> None:
    path = Path(res["result"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def tail(log: Path) -> list[str]:
    try:
        return log.read_text(encoding="utf-8", errors="replace").splitlines()[-TAIL:]
    except OSError:
        return []


def command(res: dict, name: str) -> tuple[str | None, str]:
    """The shell command for step NAME, or None and why it is skipped."""
    lc = shlex.quote(res["loopctl"])
    py = shlex.quote(sys.executable)
    if name == "push":
        if res.get("release_pushes") and command(res, "release")[0] is not None:
            return (
                None,
                "the release kit pushes the hand-off with its release (GOH_SHIP_RELEASE_PUSHES=1)",
            )
        return f"git push {shlex.quote(res['remote'])} HEAD:refs/heads/{res['branch']}", ""
    if name == "release":
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
    if name == "install":
        return (res["install"] or None), "no GOH_SHIP_INSTALL declared"
    have = Path(res["loopctl"]).is_file()
    if name == "verify":
        if not have and not res["item"]:
            return None, f"no loopctl at {res['loopctl']} and no --item"
        me = shlex.quote(str(Path(__file__).resolve()))
        return f"{py} {me} --verify {shlex.quote(res['result'])}", ""
    if not res["item"]:
        return None, "no --item to record"
    return f"{py} {lc} done {shlex.quote(res['item'])} --shipped {shlex.quote(res['result'])}", ""


def unbound_env() -> dict[str, str]:
    """This environment without git's repository bindings: a step runs in the shipped repo, never in
    whatever repository a hook that started `goh ship` had bound (contract #12)."""
    return {k: v for k, v in os.environ.items() if k not in git_bindings()}


def run_steps(res: dict) -> int:
    """Run the pipeline in this process; one mark per step; 0 shipped, 1 a step failed."""
    os.chdir(res["repo"])
    for k in git_bindings():
        os.environ.pop(k, None)
    os.environ.update(GOH_SHIP_VERSION=res["version"], GOH_SHIP_RESULT=res["result"],
                      GOH_SHIP_SUMMARY=res["summary"], GOH_SHIP_ITEM=res["item"] or "")  # fmt: skip
    stem = Path(res["result"]).with_suffix("")
    for name in STEPS:
        cmd, why = command(res, name)
        if cmd is None:
            res["steps"].append({"name": name, "status": "skipped", "why": why})
            save(res)
            step(f"{name}: skipped ({why})")
            continue
        log = Path(f"{stem}.{name}.log")
        log.write_text(f"$ {cmd}\n", encoding="utf-8")
        t0 = time.monotonic()
        out = bounded_run.run_step(["bash", "-c", cmd], res["timeout_s"], GRACE, f"ship {name}",
                                   str(log))  # fmt: skip
        secs = round(time.monotonic() - t0, 1)
        entry = {"name": name, "code": out.code, "seconds": secs, "log": str(log)}
        if out.code == 0 and not out.timed_out:
            entry["status"] = "ok"
            res["steps"].append(entry)
            if name == "release":
                res["sha"] = git(Path(res["repo"]), "rev-parse", "HEAD") or res["sha"]
            save(res)
            ok(f"{name} ({secs} s)")
            continue
        entry["status"] = "timed_out" if out.timed_out else "failed"
        if out.timed_out:
            with log.open("a", encoding="utf-8") as f:
                f.write(f"TIMED OUT after {res['timeout_s']}s: its process group was killed\n")
        res["steps"].append(entry)
        res.update(status="failed", failed_step=name, tail=tail(log))
        res["finished"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        save(res)
        err(f"{name} {entry['status']} (exit {out.code}, {secs} s): {res['result']}")
        return 1
    res.update(status="ok", finished=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"))
    save(res)
    return 0


def worker(result: str) -> int:
    res = json.loads(Path(result).read_text(encoding="utf-8"))
    lock = Path(res["result"]).parent / ".ship.lock"
    with lock.open("w") as held:
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            res.update(status="failed", failed_step="push",
                       tail=[f"another goh ship of {res['name']} holds {lock}"])  # fmt: skip
            save(res)
            err(res["tail"][0])
            return 1
        res["pid"] = os.getpid()
        save(res)
        return run_steps(res)


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
        subprocess.Popen(worker_argv, cwd=res["repo"], env=unbound_env(), stdin=subprocess.DEVNULL,
                         stdout=out, stderr=out, start_new_session=True, close_fds=True)  # fmt: skip
    info(f"goh ship started: {res['result']}")
    return 0


def status(result: str) -> int:
    """Print the marks a result holds. 0 shipped, 1 failed, 2 unreadable, 3 running, 4 the worker
    is gone (killed, or the machine slept through a reboot) while the result still says running."""
    try:
        res = json.loads(Path(result).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        err(f"goh ship: cannot read {result}: {e}")
        return 2
    for s in res["steps"]:
        mark = {"ok": ok, "skipped": step}.get(s["status"], err)
        mark(f"{s['name']}: {s['status']}" + (f" ({s['why']})" if s.get("why") else ""))
    if res["status"] == "running":
        pid = res.get("pid")
        try:
            if pid:
                os.kill(pid, 0)
            info(f"running: {result}")
            return 3
        except OSError:
            err(f"the worker (pid {pid}) is gone and the result says running: {result}")
            return 4
    return 0 if res["status"] == "ok" else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goh ship", description=__doc__.splitlines()[0])
    ap.add_argument("--repo", help="the checkout to ship (default: the current one)")
    ap.add_argument("--item", help="the queue item `loopctl done` records on success")
    ap.add_argument("--summary", help="what landed, one line (the item's result)")
    ap.add_argument("--version", help="the release version X.Y.Z (default: from the commits)")
    ap.add_argument("--bump", choices=("patch", "minor", "major", "none"),
                    help="the next version's part, or none: ship with no release (no version, "
                         "or a HEAD already tagged)")  # fmt: skip
    ap.add_argument("--wait", action="store_true", help="run in the foreground, a mark per step")
    ap.add_argument("--out", help="the result JSON (default ~/.local/state/goh/ship/REPO/...)")
    ap.add_argument("--status", metavar="RESULT", help="print a result's marks")
    ap.add_argument("--worker", metavar="RESULT", help=argparse.SUPPRESS)
    ap.add_argument("--verify", metavar="RESULT", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.verify:
        return ship_verify.verify(args.verify)
    if args.worker:
        return worker(args.worker)
    if args.status:
        return status(args.status)
    return start(args)


if __name__ == "__main__":
    sys.exit(main())
