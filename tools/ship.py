#!/usr/bin/env python3
"""goh ship: the detached, time-bounded LOCAL landing pipeline (GOH.SHIP, owner 2026-11-08).

    goh ship [--repo DIR] [--item ID] [--summary TEXT] [--version X.Y.Z | --bump patch|minor|major]
             [--wait] [--out PATH]
    goh ship --status RESULT.json

An agent's contract ends at "committed, local gate green, goh ship started". What it used to do
after its first push -- re-run the gate, release, install, read the claim card, wait -- was 49% of
every agent's context (GOH.SPENDWATCH). Here it is a pipeline no turn watches:

    push     `git push REMOTE HEAD:BRANCH` (the upstream the branch tracks), through the pre-push gate
    release  the repo's release kit, `.gatesrc` GOH_SHIP_RELEASE, run with GOH_SHIP_VERSION set (also
             `{version}` in the command); skipped when HEAD already carries a version tag; a repo
             that tags and declares none is refused before anything starts
    install  `.gatesrc` GOH_SHIP_INSTALL; skipped (said) when undeclared: a release kit that installs
             (sys_updater's --verify) or a repo that installs nothing needs no step
    verify   first, when the remote is GitHub's, the CI verdict on HEAD through the throttle-aware
             door (`loopctl ci SHA --repo OWNER/NAME --wait`): queued or running is PENDING and
             waited on, within the ship's bound; red, or still pending at the bound, fails here
             (GOH.SHIPCIWAIT). Then `loopctl verify REPO`, the claim card (GOH_LOOPCTL; REPO
             relative to LOOPCTL_ROOT or ~/Projects); skipped without loopctl and without --item
    done     with --item: `loopctl done ID --shipped RESULT`, which records the item from this
             result and the card through done's own gates (CI on what landed), so no agent turn

Started without --wait it validates, writes the result JSON (status `running`), detaches a worker
in its own session and prints one line naming the result; the worker prints a ✓/✗/· line per step
to `<result>.log`. Each step runs under `lib/bounded_run.py`: own session, GOH_SHIP_TIMEOUT seconds
(default 3600), then its whole process group TERM'd and KILL'd -- nothing it started is left
running with PPID=1. A failed step stops the pipeline; the result names it (`failed_step`) and
carries the last 40 lines of its log (`tail`), which is all a resumed agent needs.

The next version, when neither --version nor --bump is given, is release-please's rule over the
conventional-commit subjects since the newest v-tag: a `!` or BREAKING CHANGE is major, a feat is
minor, anything else a patch. Exit: 0 started (or, with --wait, shipped), 1 a step failed, 2 refused.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))
import bounded_run
import gitrun

from tui.lib import err, info, ok, step

STEPS = ("push", "release", "install", "verify", "done")
# `loopctl ci`'s exits (.loop tools/loopci.py): green, red or unread, pending, no verdict exists
# (Actions off, no workflows), throttled; 2 is a refused call.
CI_GREEN, CI_RED, CI_PENDING, CI_NONE, CI_THROTTLED = 0, 1, 3, 4, 75
CI_POLL = 60  # between two waits that came back pending (the door's own bound, or a throttle)
CI_MARGIN = 60  # of the verify step's bound, kept for the card after the wait
GITHUB = re.compile(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$")
TAIL = 40
GRACE = 5
DEFAULT_TIMEOUT = 3600
KEYS = ("GOH_SHIP_RELEASE", "GOH_SHIP_INSTALL", "GOH_SHIP_TIMEOUT")
SEMVER = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
GIT_BINDINGS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY")  # fmt: skip


class Refused(Exception):
    pass


def git(repo: Path, *args: str) -> str:
    done = gitrun.run_reader(["-C", str(repo), *args], timeout=60)
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


def bump_kind(subjects: list[str], bodies: str) -> str:
    """release-please's rule over conventional commits: breaking -> major, feat -> minor."""
    if "BREAKING CHANGE" in bodies or any(re.match(r"^\w+(\([^)]*\))?!:", s) for s in subjects):
        return "major"
    if any(re.match(r"^feat(\([^)]*\))?:", s) for s in subjects):
        return "minor"
    return "patch"


def next_version(repo: Path, tag: str, version: str | None, bump: str | None) -> str:
    if version:
        if not re.fullmatch(r"\d+\.\d+\.\d+", version.removeprefix("v")):
            raise Refused(f"--version {version!r} is not X.Y.Z")
        return version.removeprefix("v")
    m = SEMVER.match(tag)
    if not m:
        return ""
    major, minor, patch = (int(x) for x in m.groups())
    if not bump:
        span = f"{tag}..HEAD"
        bump = bump_kind(git(repo, "log", "--format=%s", span).splitlines(),
                         git(repo, "log", "--format=%b", span))  # fmt: skip
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
    try:
        timeout = int(cfg["GOH_SHIP_TIMEOUT"] or DEFAULT_TIMEOUT)
    except ValueError:
        raise Refused(f"GOH_SHIP_TIMEOUT={cfg['GOH_SHIP_TIMEOUT']!r} is not seconds") from None
    root = Path(os.environ.get("LOOPCTL_ROOT") or Path.home() / "Projects")
    try:
        name = str(repo.resolve().relative_to(root.resolve()))
    except ValueError:
        name = repo.name
    out = Path(args.out) if args.out else default_out(name, sha)
    return {
        "schema": 1, "repo": str(repo), "name": name, "item": args.item or None,
        "summary": args.summary or "", "remote": remote, "branch": branch, "sha": sha,
        "base_tag": tag, "version": next_version(repo, tag, args.version, args.bump),
        "release": cfg["GOH_SHIP_RELEASE"], "install": cfg["GOH_SHIP_INSTALL"],
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
        return f"git push {shlex.quote(res['remote'])} HEAD:refs/heads/{res['branch']}", ""
    if name == "release":
        if not res["release"]:
            return None, "the repo tags nothing and declares no GOH_SHIP_RELEASE"
        held = [t for t in git(Path(res["repo"]), "tag", "--points-at", "HEAD").splitlines()
                if SEMVER.match(t)]  # fmt: skip
        if held:
            return None, f"HEAD already carries {held[0]}"
        return res["release"].replace("{version}", res["version"]), ""
    if name == "install":
        return (res["install"] or None), "no GOH_SHIP_INSTALL declared"
    have = Path(res["loopctl"]).is_file()
    if name == "verify":
        if not have and not res["item"]:
            return None, f"no loopctl at {res['loopctl']} and no --item"
        card = f"{py} {lc} verify {shlex.quote(res['name'])}"
        wait = f"{py} {shlex.quote(str(Path(__file__).resolve()))} --ci-wait {shlex.quote(res['result'])}"
        return f"{wait} && {card}", ""
    if not res["item"]:
        return None, "no --item to record"
    return f"{py} {lc} done {shlex.quote(res['item'])} --shipped {shlex.quote(res['result'])}", ""


def github_slug(repo: Path, remote: str) -> str:
    """OWNER/NAME of REMOTE when it is GitHub's (its fetch URL), else ""."""
    m = GITHUB.search(git(repo, "remote", "get-url", remote))
    return m.group(1) if m else ""


def ci_wait(result: str) -> int:
    """Wait on HEAD's CI verdict before the card: 0 green or no verdict exists, 1 red or the bound.

    A queued or running CI is pending, never red: the ship that read loopci's one queued run 1.6 s
    after its push failed although CI went green minutes later (GOH.SHIPCIWAIT, run 42577152924).
    The wait is the door's (`loopctl ci --wait`), which polls through the shared cache and sleeps
    through a throttle; a wait that comes back pending (the door's own cap, a throttle past it) is
    asked again until the verify step's bound, less CI_MARGIN for the card."""
    res = json.loads(Path(result).read_text(encoding="utf-8"))
    repo = Path(res["repo"])
    slug = github_slug(repo, res["remote"])
    if not slug:
        step("CI: the remote is not GitHub's; no CI verdict to wait on")
        return 0
    sha = git(repo, "rev-parse", "HEAD")
    poll = float(os.environ.get("GOH_SHIP_CI_POLL") or CI_POLL)
    start = time.monotonic()
    end = start + max(1.0, res["timeout_s"] - CI_MARGIN)
    while True:
        left = end - time.monotonic()
        argv = [sys.executable, res["loopctl"], "ci", sha, "--repo", slug, "--wait", "--timeout",
                str(max(1, int(left)))]  # fmt: skip
        code = subprocess.run(argv, cwd=repo, env=unbound_env(), stdin=subprocess.DEVNULL,
                              check=False).returncode  # fmt: skip
        if code in (CI_GREEN, CI_NONE):
            return 0
        if code not in (CI_PENDING, CI_THROTTLED):
            what = "CI red" if code == CI_RED else f"loopctl ci failed (exit {code})"
            err(f"{what} for {sha[:8]} on {slug}: the ship stops at verify")
            return 1
        left = end - time.monotonic()
        if left <= 0:
            err(f"CI still pending for {sha[:8]} on {slug} after {time.monotonic() - start:.0f}s, "
                "the ship's bound (GOH_SHIP_TIMEOUT): not red, not green")  # fmt: skip
            return 1
        time.sleep(min(poll, left))


def unbound_env() -> dict[str, str]:
    """This environment without git's repository bindings: a step runs in the shipped repo, never in
    whatever repository a hook that started `goh ship` had bound (contract #12)."""
    return {k: v for k, v in os.environ.items() if k not in GIT_BINDINGS}


def run_steps(res: dict) -> int:
    """Run the pipeline in this process; one mark per step; 0 shipped, 1 a step failed."""
    os.chdir(res["repo"])
    for k in GIT_BINDINGS:
        os.environ.pop(k, None)
    os.environ["GOH_SHIP_VERSION"] = res["version"]
    os.environ["GOH_SHIP_RESULT"] = res["result"]
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
    ap.add_argument("--bump", choices=("patch", "minor", "major"))
    ap.add_argument("--wait", action="store_true", help="run in the foreground, a mark per step")
    ap.add_argument("--out", help="the result JSON (default ~/.local/state/goh/ship/REPO/...)")
    ap.add_argument("--status", metavar="RESULT", help="print a result's marks")
    ap.add_argument("--worker", metavar="RESULT", help=argparse.SUPPRESS)
    ap.add_argument("--ci-wait", metavar="RESULT", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.ci_wait:
        return ci_wait(args.ci_wait)
    if args.worker:
        return worker(args.worker)
    if args.status:
        return status(args.status)
    return start(args)


if __name__ == "__main__":
    sys.exit(main())
