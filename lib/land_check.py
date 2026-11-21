"""lib/land_check.py -- `goh land --check WORKTREE`: a land's structural guards, in seconds.

GOH.LANDCHECK.

WHY. 59 of 257 goh lands since 2026-11-01 went red, mostly on a structural guard the land runs
and the lane never did, found after a 200-900 s pre-land run: GOH.STRAYSIGNAL3 was refused for a
missing docs/map.md row, a stale 450-line headroom entry, and the env-spread and declared-reads
guards, one per try. None of those needs the suite; each is a read of the tree.

THE GUARDS, run in the checkout as the land runs them, side by side:
  * the land gate's cheap STEPS: the tree's `.gatesrc` GOH_LAND_CHECK_STEPS, a colon list each of
    whose entries is verbatim one of GOH_CI_STEPS (tests/test_land_check.py), so the check runs
    what the gate runs and never a second copy (goh's: the structural gate's caps, roadmap and
    seeds, lint as the gate runs it, the lock order, the saved-pid probes); none declared, none;
  * the GUARD TESTS: every test function of a file declaring itself a source guard
    (`# goh-reads-staged:`, gates/staged_guards.sh) or a land guard (`# goh-land-guard: NAME...`,
    no name meaning every function of the file), plus every test a past land's record names as
    its refusing guard (`guards` in `<landq>/done/*.json`, below) -- each kept only while the
    suite's own measurement (lib/fast_tests.py `load`) costs it at most EACH_S, so a guard that
    grows slow leaves by its own number. Run serially by plain pytest with GOH_STAGED_GUARD_RUN=1
    (no binary: a guard reads text), the way a commit's source guards run.

THE RECORD. A refused land names its guard: `guards(text)` reads a land's log for the failing
test node ids and the failing gate steps, `fields` puts `guard` (the first) and `guards` on the
done record, and `goh land-profile --guards` counts refusals by guard. The enqueue's own refusal
(`goh land --enqueue --land`, tools/landq.py) is recorded the same way with `stage: check`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT)]
from lib import fast_tests  # noqa: E402

EACH_S = 1.0  # the most one guard test may cost, by the suite's measurement
HISTORY = 400  # the newest done records read for past refusing guards
STEP_TIMEOUT = 300
GUARD = re.compile(r"^# goh-land-guard:(.*)$", re.MULTILINE)
STAGED = re.compile(r"^# goh-reads-staged:", re.MULTILINE)
FAILED = re.compile(r"^(?:E\s+)?FAILED (tests/[^\s:]+\.py::[^\s]+)", re.MULTILINE)
SUBGATE = re.compile(r"^✗ (?:\[([a-z][\w-]*)\]|structural: (.+?) failed\b)", re.MULTILINE)
STEP_RED = re.compile(r"✗ \[\d+/\d+\] FAILED \([^)]*\): (.+)$", re.MULTILINE)


def gatesrc(root: Path, key: str) -> str:
    """KEY from the environment, else as the tree's .gatesrc sets it, read in a child (the way
    tools/land.sh's `gatesrc_var` reads one)."""
    if key in os.environ or not (root / ".gatesrc").is_file():
        return os.environ.get(key, "")
    prog = 'unset "$1"; . ./.gatesrc >/dev/null 2>&1 </dev/null; printf %s "${!1-}"'
    res = subprocess.run(
        ["bash", "-c", prog, "gatesrc", key],
        cwd=root,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return res.stdout if res.returncode == 0 else ""


def steps(root: Path) -> list[str]:
    """The land gate's cheap steps the tree declares (none declared: none)."""
    declared = [s for s in gatesrc(root, "GOH_LAND_CHECK_STEPS").split(":") if s.strip()]
    return declared


def declared(root: Path) -> list[str]:
    """Every test function a tests/ file declares a guard: a source guard's whole file, a land
    guard's named functions (none named: the whole file)."""
    out: list[str] = []
    for path in sorted((root / "tests").glob("test_*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(root).as_posix()
        named = [n for m in GUARD.finditer(text) for n in m.group(1).split()]
        if STAGED.search(text) or (GUARD.search(text) and not named):
            out.append(rel)
        out += [f"{rel}::{n}" for n in named]
    return out


def history(q: Path | None) -> list[str]:
    """The test nodes past lands' records name as their refusing guards, newest first."""
    if q is None or not (q / "done").is_dir():
        return []
    out: list[str] = []
    for path in sorted((q / "done").glob("*.json"), reverse=True)[:HISTORY]:
        try:
            rec = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        out += [g for g in rec.get("guards") or [] if isinstance(g, str) and "::" in g]
    return list(dict.fromkeys(out))


def tests(root: Path, q: Path | None) -> list[str]:
    """The guard test functions the tree defines now, each measured at most EACH_S; a declared
    guard the suite has not timed yet is run (its first measurement is the next suite's)."""
    known = fast_tests.load()
    cost: dict[str, float] = {}
    for nodeid, s in known.items():
        cost[fast_tests._base(nodeid)] = cost.get(fast_tests._base(nodeid), 0.0) + s
    mine = set(fast_tests.units(root, declared(root)))
    past = [fast_tests._base(g) for g in history(q)]
    every = list(dict.fromkeys([*sorted(mine), *fast_tests.units(root, past)]))
    return [n for n in every if cost.get(n, 0.0 if n in mine else EACH_S + 1) <= EACH_S]


def _env(root: Path) -> dict[str, str]:
    """The checkout's own git and suite: no binding to the caller's repository or suite."""
    sys.path.insert(0, str(ROOT / "checks"))
    from _gitutil import local_env_vars

    drop = {*local_env_vars(), "GATES_OF_HECK_SUITE_RUNNER", "GOH_LAND_VERDICTS_RECORD"}
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.setdefault("GOH_PY", sys.executable)
    env.update(
        PYTHONDONTWRITEBYTECODE="1", GOH_STAGED_GUARD_RUN="1", GATES_OF_HECK_SUITE_NESTED_OK="1"
    )
    return env


def _run(argv: list[str], root: Path, env: dict[str, str]) -> tuple[int, str, float]:
    t0 = time.monotonic()
    try:
        res = subprocess.run(
            argv,
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=STEP_TIMEOUT,
            check=False,
        )
        rc, out = res.returncode, res.stdout + res.stderr
    except subprocess.TimeoutExpired:
        rc, out = 124, f"timed out after {STEP_TIMEOUT} s"
    return rc, out, round(time.monotonic() - t0, 1)


def run(root: Path, q: Path | None = None, say=print) -> list[str]:
    """Every guard over ROOT; the refusing guards' names ([] is green). SAY prints a line."""
    env = _env(root)
    nodes = tests(root, q)
    jobs: dict[str, list[str]] = {s: ["bash", "-c", s] for s in steps(root)}
    if nodes:
        serial = ["-n", "0"] if (root / "tests" / "_shard.py").is_file() else []  # goh's xdist
        py = [env["GOH_PY"], "-m", "pytest", "-q", "-p", "no:cacheprovider", *serial]
        jobs["guard tests"] = [*py, "--", *nodes]
    if not jobs:
        say("check: no guard declared (GOH_LAND_CHECK_STEPS, # goh-land-guard:) -- nothing to run")
        return []
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        done = {name: pool.submit(_run, argv, root, env) for name, argv in jobs.items()}
        results = {name: f.result() for name, f in done.items()}
    refused: list[str] = []
    for name, (rc, out, took) in results.items():
        if rc == 0:
            say(f"check: {name}: green in {took} s")
            continue
        subgates = dict.fromkeys(map("".join, SUBGATE.findall(out)))
        named = (
            FAILED.findall(out) if name == "guard tests" else [f"{name} [{g}]" for g in subgates]
        )
        refused += named or [name]
        tail = "\n".join(out.rstrip().splitlines()[-25:])
        say(f"check: {name}: REFUSED in {took} s (exit {rc}): {', '.join(named) or name}\n{tail}")
    return refused


def guards(text: str) -> list[str]:
    """A land log's refusing guards: the failing test nodes, then the failing gate steps."""
    found = [fast_tests._base(n) for n in FAILED.findall(text)]
    found += [f"[{''.join(g)}]" for g in SUBGATE.findall(text)]
    found += [s.strip() for s in STEP_RED.findall(text)]
    return list(dict.fromkeys(found))[:10]


def fields(outcome: dict) -> dict:
    """`guard` and `guards` for a red OUTCOME that names none yet, read from its log."""
    if outcome.get("state") != "red" or outcome.get("guards"):
        return {}
    try:
        text = Path(outcome.get("log") or "").read_text(errors="replace")
    except OSError:
        text = ""
    found = guards(text) or ([outcome["why"]] if outcome.get("why") else [])
    return {"guard": found[0], "guards": found} if found else {}


def count(q: Path) -> dict[str, int]:
    """Refusals by guard over every done record (`goh land-profile --guards`)."""
    tally: dict[str, int] = {}
    for path in (q / "done").glob("*.json"):
        try:
            rec = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if rec.get("state") == "red" and rec.get("guard"):
            tally[rec["guard"]] = tally.get(rec["guard"], 0) + 1
    return dict(sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])))


def at_enqueue(q: Path, req: dict, file, record, say=print) -> str:
    """REQ's guards in its slot before the pre-step: '' green; else the refusal, the request filed
    by FILE under `early/` and recorded red with its guards (lib/landq_early.py settle)."""
    from lib import landq_early

    lines: list[str] = []
    t0 = time.monotonic()
    refused = run(Path(req["worktree"]), q, lambda s: (say(s), lines.append(s)))
    if not refused:
        say(f"check: every guard green in {time.monotonic() - t0:.1f} s")
        return ""
    why = f"check: {refused[0]} refused {req['sha'][:8]}" + (
        f" (and {len(refused) - 1} more)" if len(refused) > 1 else ""
    )
    red = {
        "sha": req["sha"],
        "repo": req["repo"],
        "mode": "land",
        "state": "red",
        "stage": "check",
        "why": why,
        "guard": refused[0],
        "guards": refused[:10],
    }
    landq_early.settle(q, file(req, "early"), red, "\n".join(lines) + "\n", record)
    from tui.lib import err

    err(f"goh land: {why} -- recorded red at enqueue, nothing queued")
    return why


def cli(worktree: str, q: Path) -> int:
    """`goh land --check WORKTREE`: its committed tip's guards; 0 green, 1 refused, 2 unusable."""
    from lib import gitrun  # no-lock readers under a deadline (GOH.GITKILL)
    from tui.lib import err, info, ok

    found = gitrun.run_reader(("rev-parse", "--show-toplevel"), repo=worktree, timeout=60)
    top = found.stdout.strip() if found.returncode == 0 else ""
    if not top:
        err(f"goh land --check: {worktree} is not a git checkout")
        return 2
    dirty = gitrun.run_reader(
        ("status", "--porcelain", "--untracked-files=no"), repo=top, timeout=60
    )
    if dirty.returncode or dirty.stdout.strip():
        err(
            f"goh land --check: {top} has uncommitted changes to tracked files -- the land judges "
            "the commit; commit them, then check"
        )
        return 2
    t0 = time.monotonic()
    refused = run(Path(top), q, info)
    took = f"{time.monotonic() - t0:.1f} s"
    if refused:
        err(f"goh land --check: refused in {took} by {', '.join(refused)}")
        return 1
    ok(f"goh land --check: every guard green in {took}")
    return 0
