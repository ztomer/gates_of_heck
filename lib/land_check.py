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
    suite's own measurement (lib/fast_tests.py `load`) costs it at most EACH_S (a declared one
    DECLARED_EACH_S), so a guard that grows slow leaves by its own number. Run by plain pytest in
    up to SHARDS serial runs balanced by that cost, with GOH_STAGED_GUARD_RUN=1 (no binary: a
    guard reads text), the way a commit's source guards run. A pre-step's refusal at enqueue is
    `learn`ed into `<landq>/guards.jsonl`, so the next check runs the test that refused it.

THE JUDGES (`goh land --check` only; GOH.TABLESEARLY, GOH.LANDCHECK2). 25 of the 48 reds in 100
land records were consistency-table tests (lib/land_reds.py's class), and GOH-TINY's land went red
7 times on judges only the pre-step ran, one per attempt. The check adds every judge the change can
break (`judge_tests`): the enqueue pre-step's own choice for the diff cut to its whole-tree judges
(its first run) and the test functions land_reds' rule calls table judges (`judges_a_table`), each
under JUDGE_EACH_S, over JUDGE_SHARDS runs; and the pre-step's diff coverage judge run alone
(`diff_cov_job`). Every job runs to its verdict, so one pass names every refusal; a table refusal
names the table files its assertions name. The enqueue's check leaves them out: its pre-step runs
them minutes later anyway.

THE RECORD. A refused land names its guard: `guards(text)` reads a land's log for the failing
test node ids and the failing gate steps, `fields` puts `guard` (the first) and `guards` on the
done record, and `goh land-profile --guards` counts refusals by guard. The enqueue's own refusal
(`goh land --enqueue --land`, tools/landq.py) is recorded the same way with `stage: check`.
"""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT)]
from lib import cov_risk, fast_tests, land_records, land_reds, main_red, proc_deadline, terse  # noqa: E402
from tui.lib import counted  # noqa: E402

EACH_S = 1.0  # the most a past refuser may cost, by the suite's measurement
DECLARED_EACH_S = 10.0  # ... and a declared guard: its file chose it
SHARDS = 3  # the guard tests' pytest runs, side by side, balanced by measured cost
UNMEASURED_S = 1.0  # the cost of a test the suite has not measured yet
# EVERY JUDGE A DIFF CAN BREAK (GOH.LANDCHECK2). GOH-TINY's land went red 7 times in 45 min on
# 2026-11-22, each on a different judge only the pre-step ran (an orphan session leader, a reader
# count, an env dump, self_walk[shell], diff coverage twice), each attempt naming one. The check
# now runs the pre-step's whole first run -- every whole-tree judge it chooses (affected_tests
# --only judges) -- at most JUDGE_EACH_S each by the suite's measurement, over JUDGE_SHARDS runs,
# plus its diff coverage judge (--only diff-cov: the instrumented run of the chosen binary tests,
# under DIFFCOV_TIMEOUT), side by side, and names every refusal in one pass.
JUDGE_EACH_S, JUDGE_SHARDS, DIFFCOV_TIMEOUT = 120.0, 8, 1500
DIFFCOV = "diff coverage"
SELECT_TIMEOUT = 120
HISTORY = 400  # the newest done records read for past refusing guards
STEP_TIMEOUT = 300
# Exits that are no verdict on the tree: a step past its deadline (124); pytest interrupted (3),
# misused (4: a node it cannot find) or given no test (5). A check that judged nothing is never
# green (GOH.CHECKGIVEUP300): under the lane clamp every guard shard gave up at a fixed 300 s, the
# check said green, and GOH.SUITEDRIFTBUILD's land went red on 2 guards it had not judged. So each
# job's deadline stretches by the clamp measured on this thread (lib/proc_deadline.py `stretch`),
# at most STRETCH_CAP_S, and a job past it REFUSES, by its name, here and at enqueue.
NO_VERDICT = {"steps": {124}, "guard tests": {3, 4, 5, 124}}
STRETCH_CAP_S = 1500  # the check's longest bound already (DIFFCOV_TIMEOUT)
BLIND = " (no verdict in "  # in the name `run` refuses a job with no verdict by


def blind(refusal: str) -> bool:
    """REFUSAL is a job that judged nothing, not a guard that refused: it names no culprit."""
    return BLIND in refusal


def deadline(quiet_s: float, stretch: float) -> float:
    """QUIET_S stretched by STRETCH, never below it, never over STRETCH_CAP_S."""
    return max(quiet_s, min(STRETCH_CAP_S, quiet_s * stretch))


GUARD = re.compile(r"^# goh-land-guard:(.*)$", re.MULTILINE)
STAGED = re.compile(r"^# goh-reads-staged:", re.MULTILINE)
FAILED = re.compile(r"^(?:E\s+)?(?:FAILED|ERROR) (tests/[^\s:]+\.py::[^\s]+)", re.MULTILINE)
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


def steps(root: Path, no_pre: bool = False) -> list[str]:
    """The tree's land-gate cheap steps; NO_PRE adds GOH_LAND_NOPRE_STEPS' lint (GOH.NOPREBATCH)."""
    keys = ["GOH_LAND_CHECK_STEPS"] + ["GOH_LAND_NOPRE_STEPS"] * no_pre
    return list(dict.fromkeys(s for k in keys for s in gatesrc(root, k).split(":") if s.strip()))


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


def records(q: Path | None, newest: int | None = None) -> list[dict]:
    """The red records naming guards: the land records (the NEWEST that many, newest first) and
    the pre-step refusals `learn` kept (lib/land_records.py, GOH.STATEDB)."""
    if q is None:
        return []
    out: list[dict] = []
    out += [rec for _, rec in land_records.records(q, reverse=True)[:newest]]
    learned = land_records.ledger(q, "guards")
    out += reversed(learned[-newest:] if newest else learned)
    named = [r for r in out if isinstance(r, dict) and r.get("state") == "red"]
    return [
        {**r, "guard": r.get("guard") or r["guards"][0]}
        for r in named
        if r.get("guard") or r.get("guards")
    ]


def history(q: Path | None, repo: str = "") -> list[str]:
    """The test nodes past refusals of REPO (any, when empty) name as their guards, newest first."""
    out: list[str] = []
    for rec in records(q, HISTORY):
        if not repo or rec.get("repo") == repo:
            out += [g for g in rec.get("guards") or [] if isinstance(g, str) and "::" in g]
    return list(dict.fromkeys(out))


def costs() -> dict[str, float]:
    """The suite's measured seconds per test function (its parameters and groups summed)."""
    cost: dict[str, float] = {}
    for nodeid, s in fast_tests.load().items():
        cost[fast_tests._base(nodeid)] = cost.get(fast_tests._base(nodeid), 0.0) + s
    return cost


def tests(root: Path, q: Path | None, repo: str = "", rules: bool = False) -> list[str]:
    """The guard test functions the tree defines now: a declared one measured at most
    DECLARED_EACH_S (one the suite has not timed yet is run), a past refuser at most EACH_S; RULES
    (`goh land --check`, `--rules`) declares every rule test by its shape (lib/rule_tests.py)."""
    cost = costs()
    from lib import rule_tests  # RULES: every rule test by its shape too (GOH.RULESLEFT)

    found = rule_tests.rules(root) if rules else []
    mine = set(fast_tests.units(root, [*declared(root), *found]))
    past = [fast_tests._base(g) for g in history(q, repo)]
    every = list(dict.fromkeys([*sorted(mine), *fast_tests.units(root, past)]))
    cap = dict.fromkeys(mine, DECLARED_EACH_S)
    return [n for n in every if cost.get(n, 0.0 if n in mine else EACH_S + 1) <= cap.get(n, EACH_S)]


def chosen_by_diff(root: Path, judges: bool = False) -> list[str]:
    """What the enqueue's pre-step would choose for ROOT's diff against main: the tree's own
    tools/affected_tests.py in its enqueue mode (JUDGES: its first run alone, the whole-tree
    judges); [] for a tree without one, or when it fails."""
    tool = root / "tools" / "affected_tests.py"
    if not tool.is_file():
        return []
    env = {**_env(root), "GOH_LAND_PRE_ONLY": "1"}
    argv = [env["GOH_PY"], str(tool), "--root", str(root), "--base", "main"]
    argv += ["--only", "judges"] if judges else []
    try:
        res = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, check=False,
                             stdin=subprocess.DEVNULL, timeout=SELECT_TIMEOUT)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return []
    lines = res.stdout.splitlines() if res.returncode == 0 else []
    return [ln.strip() for ln in lines if ln.startswith("tests/")]


def judge_tests(root: Path) -> list[str]:
    """Every judge ROOT's change can break (GOH.LANDCHECK2): of the pre-step's choice for its diff,
    its whole-tree judges (`chosen_by_diff` JUDGES) and the test functions lib/land_reds.py's rule
    calls table judges (GOH.TABLESEARLY), each at most JUDGE_EACH_S by the suite's measurement (an
    unmeasured one runs); no total budget, the shards spread them."""
    cost = costs()
    judges = set(fast_tests.units(root, chosen_by_diff(root, judges=True)))
    found = [n for n in fast_tests.units(root, chosen_by_diff(root)) if land_reds.judges_a_table(n)]
    return sorted(n for n in judges | set(found) if cost.get(n, UNMEASURED_S) <= JUDGE_EACH_S)


def diff_cov_job(root: Path, env: dict[str, str]) -> list[str] | None:
    """The pre-step's diff coverage judge run alone (tools/affected_tests.py --only diff-cov), or
    None for a tree whose selector has no such mode."""
    tool = root / "tools" / "affected_tests.py"
    if not tool.is_file() or "diff-cov" not in tool.read_text(errors="replace"):
        return None
    return [env["GOH_PY"], str(tool), "--root", str(root), "--base", "main", "--only", "diff-cov"]


def shards(nodes: list[str], n: int = SHARDS) -> list[list[str]]:
    """NODES in up to N runs of near-equal measured cost (the costliest first, to the lightest)."""
    cost = costs()
    out: list[list[str]] = [[] for _ in range(min(n, len(nodes)))]
    load = [0.0] * len(out)
    for node in sorted(nodes, key=lambda x: -cost.get(x, 0.1)):
        i = load.index(min(load))
        out[i].append(node)
        load[i] += cost.get(node, 0.1)
    return [sorted(s) for s in out if s]


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


def _run(
    argv: list[str], root: Path, env: dict[str, str], timeout: float | None = None
) -> tuple[int, str, float]:
    """ARGV in its own session, its output in a file (a child that outlives it holds no pipe), the
    whole group stopped at TIMEOUT, else STEP_TIMEOUT (lib/killtree.py: TERM, grace, KILL)."""
    from lib import killtree

    limit = timeout or STEP_TIMEOUT
    t0 = time.monotonic()
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)  # fmt: skip
        rc = proc_deadline.wait(proc, limit)
        if rc is None:
            killtree.stop_group(killtree.session_pgid(proc), proc=proc)
            proc.wait()
            rc = 124
        log.seek(0)
        out = log.read().decode(errors="replace")
    if rc == 124:
        out += f"\ntimed out after {limit} s"
    return rc, out, round(time.monotonic() - t0, 1)


def run(root: Path, q: Path | None = None, say=print, repo: str = "", tables=False, no_pre=False,
        cov=True, reqs: list[dict] | None = None, rules=False):  # fmt: skip
    """Every guard over ROOT; the refusing guards' names ([] is green). SAY prints a line. TABLES
    (`goh land --check`) adds every judge the change can break (`judge_tests`) and, unless not COV
    (a batch's ratchet phase, lib/landq_ratchet.py), diff coverage; NO_PRE `steps`' no-pre lint,
    the Rust gate's coverage (its tests) deferred by name. REQS (a batch's stack; else ROOT's HEAD
    as enqueued) name what its reds may waive: main's own, a newer rule (lib/land_waive.py)."""
    env = {**_env(root), **({"GOH_RUST_COVERAGE": "defer"} if no_pre else {})}
    at = ["git", "rev-parse", "HEAD"]
    rc, head, _ = _run(at, root, env, 60) if repo and not reqs else (1, "", 0)
    reqs = reqs or [{"repo": repo, "sha": head.strip()}] * (rc == 0)
    from lib import land_waive  # its reds that are main's or a newer rule's (GOH.MAINREDWAIVE)

    env.update(w.split("=", 1) for w in land_waive.words(reqs))
    nodes = tests(root, q, repo, rules or tables)  # positional: a test's fake takes *args
    if tables:
        t0 = time.monotonic()
        extra = [n for n in judge_tests(root) if n not in nodes]
        say(f"check: {counted(len(extra), 'judge test')} the diff can break, chosen in "
            f"{time.monotonic() - t0:.1f} s")  # fmt: skip
        nodes += extra
    jobs: dict[str, list[str]] = {s: ["bash", "-c", s] for s in steps(root, no_pre)}
    serial = ["-n", "0"] if (root / "tests" / "_shard.py").is_file() else []  # goh's xdist
    py = [env["GOH_PY"], "-m", "pytest", "-q", "-p", "no:cacheprovider", *serial]
    parts = shards(nodes, JUDGE_SHARDS if tables or rules else SHARDS)  # RULES: --check --rules
    for i, part in enumerate(parts, 1):
        jobs[f"guard tests {i}/{len(parts)}"] = [*py, "--", *part]
    if tables and cov and (job := diff_cov_job(root, env)):
        jobs[DIFFCOV] = job
    if not jobs:
        say("check: no guard declared (GOH_LAND_CHECK_STEPS, # goh-land-guard:) -- nothing to run")
        return []
    limit = deadline(STEP_TIMEOUT, slow := proc_deadline.stretch())  # before the jobs load it
    say(f"check: each job's deadline {limit:.0f} s (this thread runs x{slow:.1f} a quiet core)")
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        done = {
            name: pool.submit(_run, argv, root, env, DIFFCOV_TIMEOUT if name == DIFFCOV else limit)
            for name, argv in jobs.items()
        }
        results = {name: f.result() for name, f in done.items()}
    refused: list[str] = []
    for name, (rc, out, took) in results.items():
        if rc == 0:
            say(f"check: {name}: green in {took} s")
            continue
        pytest = name.startswith("guard tests")
        if rc in NO_VERDICT["guard tests" if pytest else "steps"]:
            refused.append(f"{name}{BLIND}{took} s, exit {rc})")
            say(f"check: {name}: NO VERDICT in {took} s (exit {rc}) -- red: it judged nothing")
            continue
        subgates = dict.fromkeys(map("".join, SUBGATE.findall(out)))
        named = (
            list(dict.fromkeys(map(fast_tests._base, FAILED.findall(out))))
            if pytest
            else [f"[{g}]" for g in subgates]
        )
        refused += named or [name]
        if tabled := [n for n in named if land_reds.judges_a_table(n)]:
            files = ", ".join(land_reds.tables(out)) or "none named (read the test)"
            say(f"check: {counted(len(tabled), 'table test')} refused; the tables named: {files}")
        head = f"check: {name}: REFUSED in {took} s (exit {rc}): {', '.join(named) or name}"
        say("\n".join(terse.under(head, out, 8)))  # GOH.TERSEOUT: never a 25-line tail each
    return refused


def guards(text: str) -> list[str]:
    """A land log's refusing guards: the failing test nodes, then the failing gate steps."""
    found = [fast_tests._base(n) for n in FAILED.findall(text)]
    found += [f"[{''.join(g)}]" for g in SUBGATE.findall(text)]
    found += [s.strip() for s in STEP_RED.findall(text)]
    return list(dict.fromkeys(found))[:10]


def fields(outcome: dict, req: dict | None = None) -> dict:
    """`guard` and `guards` for a red OUTCOME that names none yet, read from its log; and REQ's
    diff coverage warning from its enqueue (`cov_risk`, lib/cov_risk.py), whatever the state."""
    kept = {"cov_risk": req["cov_risk"]} if req and "cov_risk" in req else {}
    if outcome.get("state") != "red" or outcome.get("guards"):
        return kept
    try:
        text = Path(outcome.get("log") or "").read_text(errors="replace")
    except OSError:
        text = ""
    found = guards(text) or ([outcome["why"]] if outcome.get("why") else [])
    return {**kept, **({"guard": found[0], "guards": found} if found else {})}


def count(q: Path) -> dict[str, int]:
    """Refusals by guard over every record (`goh land-profile --guards`)."""
    tally: dict[str, int] = {}
    for rec in records(q):
        tally[rec["guard"]] = tally.get(rec["guard"], 0) + 1
    return dict(sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])))


def at_enqueue(q: Path, req: dict, file, record, say=print, no_pre: str | None = None) -> str:
    """REQ's guards in its slot before the pre-step: '' green; else the refusal, the request filed
    by FILE under `early/` and recorded red with its guards (lib/landq_early.py settle). The
    stacking check on main runs first: a conflict is red in seconds (GOH.BATCHCONFLICTFIRST)."""
    from lib import landq_early
    from tui.lib import err

    if red := landq_early.at_enqueue(q, req, file, record):  # seconds, before any test runs
        err(f"goh land: {red.splitlines()[0]} -- recorded red at enqueue, nothing queued")
        return red.splitlines()[0]  # GOH.BATCHCONFLICTFIRST: no guard or pre-step for it
    lines: list[str] = []
    main_red.notice(q, req, say)  # main red in the idle suite: said, its nodes to the pre-step
    t0 = time.monotonic()
    req.update({} if no_pre is None else {"no_pre": no_pre.strip()})  # alone: lib/landq_batch.py
    # Every rule test by its shape too (GOH.RULESLEFT): 230 of them ran in 30.5 s over 8 runs, and a
    # rule no file declares no longer reaches the lander unjudged; a job past its clamp-stretched
    # deadline is red by its name (GOH.CHECKGIVEUP300), never a pass.
    refused = run(Path(req["worktree"]), q, lambda s: (say(s), lines.append(s)), req["repo"],
                  no_pre=no_pre is not None, reqs=[req], rules=True)  # fmt: skip
    req["cov_risk"] = cov_risk.say(req["worktree"], req["repo"], say)  # kept on the land record
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
    err(f"goh land: {why} -- recorded red at enqueue, nothing queued")
    return why


def add_arguments(mode, ap) -> None:
    mode.add_argument("--check", metavar="WORKTREE", help="its tip's structural land guards")
    ap.add_argument("--rules", action="store_true", help="--check: every rule test, no judge")


def cli(worktree: str, q: Path, rules: bool = False) -> int:
    """`goh land --check [--rules] WORKTREE` (lib/land_check_cli.py, split out at the line cap)."""
    from lib import land_check_cli

    return land_check_cli.cli(worktree, q, rules)


NODE = re.compile(r"tests/[^\s:,()]+\.py::[^\s,()]+")


def learn(q: Path, req: dict, refusal: str) -> None:
    """A pre-step's refusal at enqueue, which files no land record: the test nodes it names, in
    goh.db's land_guards for `history` and `count` (never a reason the refusal is not said)."""
    found = list(dict.fromkeys(map(fast_tests._base, NODE.findall(refusal))))[:10]
    if not found:
        return
    row = {"repo": req.get("repo", ""), "item": req.get("item", ""), "sha": req.get("sha", ""),
           "stage": "pre", "state": "red", "guard": found[0], "guards": found,
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}  # fmt: skip
    with contextlib.suppress(OSError, sqlite3.Error):
        land_records.append(q, "guards", [row])
