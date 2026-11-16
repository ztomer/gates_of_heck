"""The loaded suite's verdict, recorded per TREE, so a ship reads it instead of running it.

GOH.SHIPRELREUSE R2 (coordinator ruling 2026-11-15, owner-delegated). tools/release_goh.sh ran
tools/loaded_suite.sh -- the suite once under tools/load.py's spinners (GOH.CLASS.HARNESS),
485-511 s -- inside every ship, on a release commit whose tree differs from the land-gated tip only
in CHANGELOG.md and the fragments it folds (lib/goh_version.sh: the version is the tag). Now each
land starts that run on the tip it landed, in the background, and records the verdict here; the ship
requires a green verdict for its tip's tree: it waits for one that is running and refuses one that
is red, failed to run, or missing, saying which.

    loaded_verdict.py start --repo DIR --sha SHA [--cmd CMD]   # land.sh, after main moved
    loaded_verdict.py wait  --repo DIR --sha SHA [--max SECS]   # the ship, before its land
    loaded_verdict.py show  --repo DIR --sha SHA                # the record, as JSON

The record is `<common git dir>/goh-loaded/<tree>-<identity>.json`: keyed by the tree (and by
lib/gate_identity.py's identity of the command, GOH.VERDICTKEY), so any commit of the same bytes
reads it, and in the common git dir, so it outlives the worktree that landed. `start`
makes a detached worktree of SHA under the checkout's run root (lib/run_path.py) and a runner in a
session of its own that outlives land.sh: it runs CMD there (default tools/loaded_suite.sh, whose
load.py accounts and reaps every spinner and bounds the run at 60 min), writes green or red with the
exit code and the log, and removes the worktree. A runner killed by a signal kills its command's
group and records `died`; a record still `running` whose runner is gone reads as died too. Pinned by
tests/test_loaded_verdict.py.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_identity  # the verdict's identity beside its tree (GOH.VERDICTKEY)
import run_path  # the one place run paths come from

DEFAULT_CMD = "tools/loaded_suite.sh"
POLL_S = 10.0
EXIT_REFUSED = 1


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                         env=env, check=True, stdin=subprocess.DEVNULL)  # fmt: skip
    return out.stdout.strip()


def store(repo: Path) -> Path:
    common = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common) / "goh-loaded"


def tree_of(repo: Path, sha: str) -> str:
    return _git(repo, "rev-parse", f"{sha}^{{tree}}")


def record_path(repo: Path, sha: str, cmd: str = "") -> Path:
    """`<tree>-<identity>.json`: the tree AND what judged it (GOH.VERDICTKEY) -- the command, the
    gates' content and the tools (lib/gate_identity.py). Keyed on the tree alone, a run green under
    `--cmd true` answered "green already" for `--cmd false`."""
    ident = gate_identity.key(f"loaded {cmd or DEFAULT_CMD}")[:16]
    return store(repo) / f"{tree_of(repo, sha)}-{ident}.json"


def read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def state(rec: dict | None) -> str:
    """green, red, died, running, or missing -- a `running` record whose runner is gone is died."""
    if rec is None:
        return "missing"
    s = str(rec.get("state", ""))
    if s == "running" and not alive(rec.get("pid")):
        return "died"
    return s if s in {"green", "red", "died", "running"} else "missing"


def start(repo: Path, sha: str, cmd: str) -> int:
    """Begin the loaded run of SHA's tree unless one is green or running; returns at once."""
    sha = _git(repo, "rev-parse", f"{sha}^{{commit}}")
    path = record_path(repo, sha, cmd)
    now = state(read(path))
    if now in {"green", "running"}:
        print(f"loaded verdict for {sha[:8]}: {now} already ({path})")
        return 0
    work = run_path.run_dir(repo) / f"loaded-{sha[:12]}"
    log = run_path.run_dir(repo) / f"loaded-{sha[:12]}.log"
    work.parent.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, str(Path(__file__).resolve()), "run", "--repo", str(repo),
            "--sha", sha, "--cmd", cmd, "--work", str(work), "--log", str(log)]  # fmt: skip
    with open(os.devnull, "rb") as nul, open(log, "ab") as out:
        child = subprocess.Popen(argv, stdin=nul, stdout=out, stderr=subprocess.STDOUT,
                                 start_new_session=True, close_fds=True)  # fmt: skip
    write(path, {"state": "running", "sha": sha, "tree": path.stem.split("-")[0], "pid": child.pid,
                 "cmd": cmd, "log": str(log), "started": int(time.time())})  # fmt: skip
    print(f"loaded verdict for {sha[:8]}: started (runner pid {child.pid}, log {log})")
    return 0


def run(repo: Path, sha: str, cmd: str, work: Path, log: Path) -> int:
    """The runner: CMD in a detached worktree of SHA, its verdict recorded, the worktree removed."""
    path = record_path(repo, sha, cmd)
    # `start` writes this runner's record after spawning it: wait for that write (bounded), so a
    # fast verdict can never be overwritten by the starter's `running`.
    until = time.monotonic() + 10
    while time.monotonic() < until and (read(path) or {}).get("pid") != os.getpid():
        time.sleep(0.05)
    tree = path.stem.split("-")[0]
    rec = {"state": "running", "sha": sha, "tree": tree, "pid": os.getpid(), "cmd": cmd,
           "log": str(log), "started": int(time.time())}  # fmt: skip
    write(path, rec)
    proc: subprocess.Popen | None = None

    def died(signum: int, _frame: object) -> None:
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
        write(path, {**rec, "state": "died", "rc": -signum, "ended": int(time.time())})
        sys.exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, died)
    rc = 1
    try:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(work)],
                       capture_output=True, check=False, stdin=subprocess.DEVNULL)  # fmt: skip
        _git(repo, "worktree", "add", "-q", "--detach", str(work), sha)
        proc = subprocess.Popen(["bash", "-c", cmd], cwd=work, stdin=subprocess.DEVNULL,
                                start_new_session=True)  # fmt: skip
        rc = proc.wait()
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(work)],
                       capture_output=True, check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    write(path, {**rec, "state": "green" if rc == 0 else "red", "rc": rc,
                 "ended": int(time.time())})  # fmt: skip
    return rc


def wait(repo: Path, sha: str, max_s: float, say=print, cmd: str = "") -> int:
    """0 on a green verdict for SHA's tree; waits while one runs; refuses anything else, named."""
    sha = _git(repo, "rev-parse", f"{sha}^{{commit}}")
    path = record_path(repo, sha, cmd)
    deadline = time.monotonic() + max_s
    told = False
    while True:
        rec = read(path)
        now = state(rec)
        if now == "green":
            say(f"loaded verdict for {sha[:8]} (tree {path.stem[:12]}): green, recorded "
                f"{time.strftime('%H:%M:%S', time.localtime(rec.get('ended', 0)))}")  # fmt: skip
            return 0
        if now == "missing":
            say(f"no loaded verdict for {sha[:8]} (tree {path.stem[:12]}): no land started the "
                f"suite under load on this tree -- start it: {Path(__file__).name} start "
                f"--repo {repo} --sha {sha[:12]}")  # fmt: skip
            return EXIT_REFUSED
        if now in {"red", "died"}:
            say(f"loaded verdict for {sha[:8]} (tree {path.stem[:12]}): {now} (exit "
                f"{rec.get('rc', '?')}) -- a test the box's load turns red, or a run that did not "
                f"finish (75: load.py's wait for the gates in flight ran out); its log: "
                f"{rec.get('log', '?')}; run it again: {Path(__file__).name} start --repo {repo} "
                f"--sha {sha[:12]}")  # fmt: skip
            return EXIT_REFUSED
        if time.monotonic() >= deadline:
            say(f"loaded verdict for {sha[:8]}: still running after {int(max_s)} s (runner pid "
                f"{rec.get('pid')}, log {rec.get('log')}) -- refusing")  # fmt: skip
            return EXIT_REFUSED
        if not told:
            say(f"loaded verdict for {sha[:8]}: running (runner pid {rec.get('pid')}) -- waiting")
            told = True
        time.sleep(min(POLL_S, max(0.1, deadline - time.monotonic())))


def declared(repo: Path) -> str:
    """GOH_LAND_LOADED: the environment's (even empty), else what the repo's .gatesrc declares."""
    if "GOH_LAND_LOADED" in os.environ:
        return os.environ["GOH_LAND_LOADED"]
    if not (repo / ".gatesrc").is_file():
        return ""
    script = ('unset GOH_LAND_LOADED; . ./.gatesrc >/dev/null 2>&1 </dev/null; '
              'printf %s "${GOH_LAND_LOADED-}"')  # fmt: skip
    out = subprocess.run(["bash", "-c", script], cwd=repo, capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, check=False, timeout=30)  # fmt: skip
    return out.stdout


def before_clock(res: dict, command) -> bool:
    """GOH.SHIPLOADEDWAIT: goh ship's worker waits here, under the land lock and before its first
    step, for the tip's verdict -- never inside the release step's clock, where v0.125.0 spent
    470.6 s of 780.2 s in tools/release_goh.sh's read of it. The run stays apart from the land
    gate: its spinners take the load slot alone after every gate in flight is done, and in the land
    gate they would starve the gates sharing its slot (tools/load.py rule 8, D-0003). Red, died or
    missing refuses the ship before anything moved; `res["before_clock"]` names the wait apart from
    the steps (`goh ship --status`). True: go on (nothing declared, no release, or green)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import datetime as dt

    import ship_status

    from tui.lib import err, info

    repo = Path(res["repo"])
    cmd = declared(repo)
    if not cmd or command(res, "release")[0] is None:
        return True
    said: list[str] = []

    def say(line: str) -> None:
        said.append(line)
        info(line)

    t0 = time.monotonic()
    rc = wait(repo, "HEAD", float(res.get("timeout_s") or 3600), say, cmd)
    log = Path(res["result"]).with_suffix(".loaded.log")
    log.write_text("".join(f"{line}\n" for line in said), encoding="utf-8")
    res["before_clock"] = {"name": "the tip's loaded verdict", "status": "failed" if rc else "ok",
                           "seconds": round(time.monotonic() - t0, 1), "log": str(log)}  # fmt: skip
    if rc:
        res.update(status="failed", failed_step="release", tail=said[-40:],
                   finished=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"))  # fmt: skip
        err(f"the tip's loaded verdict refuses the ship before any step: {res['result']}")
    ship_status.save(res)
    return rc == 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loaded_verdict.py")
    sub = ap.add_subparsers(dest="verb", required=True)
    for verb in ("start", "wait", "show", "run"):
        p = sub.add_parser(verb)
        p.add_argument("--repo", required=True, type=Path)
        p.add_argument("--sha", required=True)
        p.add_argument("--cmd", default=DEFAULT_CMD)
        if verb == "run":
            p.add_argument("--work", required=True, type=Path)
            p.add_argument("--log", required=True, type=Path)
        if verb == "wait":
            p.add_argument("--max", type=float, default=3600.0)
    a = ap.parse_args(argv)
    repo = a.repo.resolve()
    if a.verb == "start":
        return start(repo, a.sha, a.cmd)
    if a.verb == "run":
        return run(repo, a.sha, a.cmd, a.work, a.log)
    if a.verb == "wait":
        return wait(repo, a.sha, a.max, cmd=a.cmd)
    print(json.dumps(read(record_path(repo, a.sha, a.cmd)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
