#!/usr/bin/env python3
"""lib/ship_full_run.py -- every ship makes the instrumented full run and judges every coverage
floor over what landed since the last ship; a red one releases nothing and is bisected to its land
(GOH.SHIPSTARVE, GOH.COVOFFLAND).

The owner's term (GOH.COVCARRY, 2026-11-17): "a fresh full instrumented run happens on main's tip
before every ship". And since GOH.COVOFFLAND (owner, 2026-11-19: lands took 15-30 min, the
instrumented run 69-76% of a land's gate) it is the ONLY coverage measurement on the path to a
release: a land runs the coverage step's tests plain (gates/rust_gate.sh GOH_RUST_COVERAGE=plain),
so the ship judges every floor -- the line floor, and the diff coverage of every Rust line changed
since the LAST SHIP (GOH_CHANGE_BASE: the newest v-tag below the tip, gates/diff_cov.py) -- over
all the lands it coalesces. Every floor is as strict as it was; only where it is measured moved.

So the ship's FIRST step (`suite`, tools/ship.py) runs here, holding the land lock: main cannot
move under it, so the run judges exactly the tip the release kit then checks (lib/ship_suite.py)
and releases. Only a finished green idle lander's run of this tip, its diff coverage based at the
same last ship, stands for it (GOH.IDLESUITERETIRE: the idle run makes this very judgement,
full_run.judge_env; `full_run.adoptable`): adopted, its profiles kept, nothing re-run; a land's
run measured nothing, so it never stands for it. The run is the idle
lander's own command (full_run.INSTRUMENTED, the coverage gate's Rust mode with its external
suite) with GOH_LAND_VERDICTS_RECORD=/dev/null and GOH_LAND_VERDICTS_REUSE=@tree (a pass JUDGED on
this very tree by a test measured running no binary is not run again, tests/_same_tree.py), in a
detached worktree kept between ships beside the ship results (`<ship dir>/suite-wt`: its build
caches stay warm), admitted as class `ship`. Its seconds are the step's, so the ship record keeps
the lock wait (`land_wait_s`) apart from the ship's own run (`own_s`).

A RED RUN (exit RED) stops the ship before anything is pushed or released. A diff coverage red
names its uncovered lines, so it is never bisected: `git blame` names each line's land, in the
record (`diffcov_red`) and the red's tail, seconds after the red, and a fault item is filed per land
(GOH.SHIPBISECTCOV: a 2026-11-22 ship's worker bisected such a red for 50 min, one full suite per
step, holding the ship lock). Any other red is bisected by the ship's worker AFTER it gives the land
lock back, so lands go on, over the lands since the last ship (`goh-landed`, the tips land.sh
recorded), halving, to the first red one, filing a fault item naming it (`loopctl file`): a red
naming failing tests probes those tests alone, plain (lib/ship_red_cause.py); one naming none (a
floor) re-runs the full command. While it bisects the record says `bisecting` with its step
(`bisect_step`), never `finished`. The last ship is taken as green: it released.

    ship_full_run.py --repo MAIN --wt DIR [--profiles-out FILE]
                                            exit 0 green (or owed none), 1 red, 2 no verdict

A binary-running test whose stored profile has its exact key (the binary's bytes, its inputs by
content, the tools) is taken by that profile, not run (tests/_prof_reuse.py, GOH.COVPROFILEREUSE);
FILE gets the reused and fresh counts per binary, which tools/ship.py puts in the ship record.

Pinned by tests/test_ship_starve.py and tests/test_ship_cov_bisect.py.
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
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cov_risk  # lib/: the kept per-line coverage sources (GOH.DIFFCOVLAND)
import full_run  # lib/: the instrumented command, the worktree, the .gatesrc read (no lock)
import reuse  # lib/: the land record (`landed_identity`)
import reuse_fault  # lib/: `loopctl file` and the queue's current phase
import ship_red_cause  # lib/: what the red's log names (GOH.SHIPBISECTCOV)
import ship_suite  # lib/: the check the release kit makes

ROOT = Path(__file__).resolve().parent.parent
MAX = full_run.MAX
REUSE = full_run.REUSE  # the tree's own no-binary passes are not run again (GOH.SHIPBREACH0137)
GREEN, RED, NO_VERDICT = 0, 1, 2


def owed(repo: Path) -> str:
    """ "" when the ship of REPO owes its run, else why not."""
    return "" if full_run.declares_full(repo) else "the repo declares no GOH_SHIP_FULL_SUITE=on"


last_ship = full_run.last_ship  # the newest version tag below a sha (shared with the idle lander)


def default_argv() -> list[str]:
    return [sys.executable, str(ROOT / "tools" / "load.py"), "--background-ok", "ship full suite",
            "--max", MAX, "0", "--", "bash", "-c", full_run.INSTRUMENTED]  # fmt: skip


def _judge(repo: Path, wt: Path, sha: str, base: str, argv: list[str] | None) -> int:
    """SHA's instrumented run in WT, its diff coverage based at BASE: GREEN, RED or NO_VERDICT."""
    if why := full_run.prepare(repo, wt, sha):
        print(f"ship full suite: no worktree at {sha[:8]}: {why}", flush=True)
        return NO_VERDICT
    print(f"ship full suite on {sha[:8]} in {wt}, its diff coverage since "
          f"{base[:8] if base else 'the root'}", flush=True)  # fmt: skip
    env = full_run.judge_env(base, "ship")  # the idle lander's run is this same one (adopt)
    rc = subprocess.run(argv or default_argv(), cwd=wt, env=env, stdin=subprocess.DEVNULL,
                        check=False).returncode  # fmt: skip
    return GREEN if rc == 0 else RED


def run(repo: Path, wt: Path, argv: list[str] | None = None) -> int:
    """The instrumented full run on REPO's HEAD in WT, its output the step's log; GREEN, RED or
    NO_VERDICT."""
    sha = full_run._git(repo, "rev-parse", "HEAD").stdout.strip()
    if not sha:
        print(f"ship full suite: {repo} names no HEAD", flush=True)
        return NO_VERDICT
    if why := owed(repo):
        print(f"ship full suite: none owed: {why}", flush=True)
        return GREEN
    base = last_ship(repo, sha)
    idle, why = full_run.adoptable(sha, base)
    if idle is not None and (why := ship_suite.full_suite(repo, sha)[1]) == "":
        print(f"ship full suite on {sha[:8]}: green, adopted: the idle lander's finished run "
              f"judged this tree with its diff coverage since {base[:8] or 'the root'} "
              f"(GOH.IDLESUITERETIRE)", flush=True)  # fmt: skip
        adopt_profiles(idle)
        keep_profile(idle)
        return GREEN
    print(f"ship full suite on {sha[:8]}: no idle run adopted: {why}", flush=True)
    rc = _judge(repo, wt, sha, base, argv)
    keep_profile(wt)
    if rc == GREEN and (why := ship_suite.full_suite(repo, sha)[1]):  # green, yet no record: say so
        print(f"ship full suite on {sha[:8]}: green, but {why}", flush=True)
        return NO_VERDICT
    print(f"ship full suite on {sha[:8]}: {'green' if rc == GREEN else 'red'}", flush=True)
    return rc


def adopt_profiles(idle_wt: Path) -> None:
    """The adopted idle run's reused and fresh binary tests (its GOH_PROF_REUSE_OUT, beside its
    worktree) copied to where the ship's record reads them; none kept, none copied."""
    out = os.environ.get("GOH_PROF_REUSE_OUT")
    kept = idle_wt.parent / full_run.PROFILES
    if out and kept.is_file():
        Path(out).write_bytes(kept.read_bytes())


def keep_profile(wt: Path) -> None:
    """The run's per-line coverage sources, kept beside the ship's records for the next land's
    diff coverage warning (lib/cov_risk.py, GOH.DIFFCOVLAND); a failure is said, never a verdict."""
    try:
        kept = cov_risk.keep(
            wt / "target" / "llvm-cov" / "lcov-parts", wt, wt.parent / cov_risk.NAME
        )
    except Exception as exc:  # noqa: BLE001 -- the profile is a hint, the ship's verdict stands
        print(f"ship full suite: coverage profile not kept: {exc}", flush=True)
        return
    if kept is None:
        print("ship full suite: no lcov parts, no coverage profile kept", flush=True)


def lands(repo: Path, base: str, head: str) -> list[str]:
    """The landed tips in BASE..HEAD (land.sh's record, lib/reuse.py `landed_identity`), oldest
    first, HEAD last: what this ship coalesces."""
    span = full_run._git(repo, "rev-list", "--reverse", "--first-parent",
                         f"{base}..{head}" if base else head).stdout.split()  # fmt: skip
    out = [c for c in span[:-1] if reuse.landed_identity(repo, c) is not None]
    return [*out, head] if span else []


def bisect(repo: Path, wt: Path, head: str, argv: list[str] | None = None, tests: list[str] = (),
           step: Callable[[dict], None] | None = None) -> dict:  # fmt: skip
    """HEAD's run was red: the first red land since the last ship, halving over `lands`, and the
    fault item filed for it. TESTS (the failing tests the red named): each probe runs those alone,
    plain (lib/ship_red_cause.py), never the full instrumented run. STEP is told each probe before
    it runs. A probe with no verdict stops the bisect, naming the range left."""
    base = last_ship(repo, head)
    cands, runs = lands(repo, base, head), []
    lo, hi = -1, len(cands) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if step:
            step({"n": len(runs) + 1, "lands": len(cands), "sha": cands[mid]})
        if tests and (why := full_run.prepare(repo, wt, cands[mid])):
            print(f"ship bisect: no worktree at {cands[mid][:8]}: {why}", flush=True)
            rc = NO_VERDICT
        elif tests:
            print(f"ship bisect on {cands[mid][:8]}: {' '.join(tests)}", flush=True)
            rc = ship_red_cause.probe(wt, list(tests), MAX)
        else:
            rc = _judge(repo, wt, cands[mid], base, argv)
        runs.append({"sha": cands[mid], "verdict": {GREEN: "green", RED: "red"}.get(rc, "none")})
        if rc == NO_VERDICT:
            break
        lo, hi = (mid, hi) if rc == GREEN else (lo, mid)
    found = {"base": base, "lands": len(cands), "runs": runs, "tests": list(tests)}
    if not cands or hi - lo > 1:
        return {**found, "culprit": "", "fault": "not filed: the bisect reached no single land"}
    culprit, since = cands[hi], cands[lo] if lo >= 0 else base
    parent = since if lo >= 0 else ""
    told = (lambda probe: step({**probe, "lands": len(cands)})) if step else None
    found["confirm"] = confirm(repo, wt, (culprit, parent), (base, argv), tests, runs, told)
    if [c["verdict"] for c in found["confirm"]] not in (["red", "green"], ["red"]):
        return {**found, "culprit": "", "candidate": culprit, "flake": True,
                "fault": f"not filed: a flake -- {culprit[:12]} is not red at its own failing "
                "tests, or its parent is not green there"}  # fmt: skip
    subject = full_run._git(repo, "log", "-1", "--format=%s", culprit).stdout.strip()
    return {**found, "culprit": culprit, "since": since, "subject": subject,
            "fault": file_fault(repo, culprit, since, base, subject)}  # fmt: skip


def confirm(repo: Path, wt: Path, pair: tuple[str, str], judge: tuple[str, list[str] | None],
            tests: list[str], runs: list[dict],
            step: Callable[[dict], None] | None = None) -> list[dict]:  # fmt: skip
    """Before the candidate culprit of PAIR is named (GOH.BISECTRECHECK): the red's failing TESTS
    run again at it, then at its parent land (PAIR's second, "" when that is the last ship, which
    released: green). Naming no tests (a floor), a culprit no probe saw red -- the tip, whose red
    is the run being bisected -- gets one more full run (JUDGE: its base, argv). Each {sha,
    verdict}; a culprit not red stops there. STEP is told each re-check, as each probe."""
    out: list[dict] = []
    for sha, want in zip(pair, (RED, GREEN), strict=True):
        if not sha:
            continue
        if step:
            step({"n": len(runs) + len(out) + 1, "sha": sha, "recheck": True})
        if tests:
            why = full_run.prepare(repo, wt, sha)
            print(f"ship bisect: re-check {sha[:8]}: {why or ' '.join(tests)}", flush=True)
            rc = NO_VERDICT if why else ship_red_cause.probe(wt, list(tests), MAX)
        elif any(r["sha"] == sha and r["verdict"] == ("red", "green")[want == GREEN] for r in runs):
            rc = want  # the full run already judged it in this bisect
        else:
            rc = _judge(repo, wt, sha, *judge)
        out.append({"sha": sha, "verdict": {GREEN: "green", RED: "red"}.get(rc, "none")})
        if rc != want:
            break
    return out


def _parent(repo: Path, sha: str) -> str:
    return full_run._git(repo, "rev-parse", f"{sha}^").stdout.strip()


def file_fault(repo: Path, culprit: str, since: str, base: str, subject: str) -> str:
    """`loopctl file` the fault item naming the culprit land; "filed ID", "already filed ID" (a
    later ship's bisect found the same land), or "not filed: why"."""
    loopctl = os.environ.get("GOH_LOOPCTL") or str(Path.home() / "Projects/.loop/tools/loopctl.py")
    if not os.path.isfile(loopctl):
        return f"not filed: no loopctl at {loopctl}"
    item = {"id": f"GOH.SHIPRED{culprit[:8].upper()}", "repo": repo.name, "status": "todo",
            "depends": [], "source": "goh ship's bisect (lib/ship_full_run.py)",
            "item": f"The ship's instrumented full run (every coverage floor, the diff coverage "
            f"since the last ship, the suite) is red, and the first red land since the last ship "
            f"is {culprit[:12]} ({subject}), landed on {since[:12] or 'the root'}: it releases "
            f"nothing until that land's red is fixed. Re-run: `bash gates/coverage_gate.sh --lang "
            f"rust` on {culprit[:12]} with GOH_CHANGE_BASE={base[:12] or '(unset)'}.",
            "metric": "the ship's suite step on main's tip: red, nothing released",
            "predicted": "red -> green: the next ship releases",
            "verify_after": {"ships": 1},  # the next ship is the re-measure (GOH.REUSEFAULTVA)
            "done_state": "the floor or the test the culprit land broke is met on main, a test "
            "pins it, and a ship releases",
            "class_of": "none", "class_why": "one land's red, found by the ship's bisect",
            }  # fmt: skip
    phase = reuse_fault.current_phase(set(item))
    if not phase.startswith("phase "):
        return f"not filed: {phase}"
    code, said = reuse_fault._file(loopctl, {**item, "phase": phase[6:]})
    if code == 0 or f"{item['id']} already exists" in said:
        return f"{'filed' if code == 0 else 'already filed'} {item['id']}"
    return f"not filed: {said[-200:]}"


def after_red(res: dict, save: Callable[[dict], None]) -> bool:
    """The ship's worker, its land lock given back, on a suite step that ran RED. A diff coverage
    red names its lines: each is blamed to its land (`diffcov_red`), no bisect. Any other red is
    bisected (`bisect`), on the failing tests it names when it names any, the record saying
    `bisecting` with its step until the bisect ends (GOH.SHIPBISECTCOV). True when the bisect
    found a flake (GOH.BISECTRECHECK): the ship retries its suite step once."""
    step = next((s for s in reversed(res.get("steps", [])) if s.get("name") == "suite"), {})
    if (
        res.get("failed_step") != "suite"
        or step.get("status") != "failed"
        or step.get("code") != RED
    ):
        return False
    repo = Path(res["repo"])
    head = full_run._git(repo, "rev-parse", "HEAD").stdout.strip()
    log = Path(step.get("log") or os.devnull)
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    if rows := ship_red_cause.uncovered(text):
        base = last_ship(repo, head)
        found = ship_red_cause.attribute(repo, head, lands(repo, base, head), rows)
        faults = {
            r["land"]: file_fault(repo, r["land"], _parent(repo, r["land"]), base, r["subject"])
            for r in found
            if r["land"]
        }
        res["diffcov_red"] = {"base": base, "lines": found, "faults": faults}
        said = [ship_red_cause.say(r) for r in found]
        res["tail"] = [*res.get("tail", []), *said]
        save(res)
        for line in said:
            print(f"ship full suite: {line}", flush=True)
        return False
    tests = ship_red_cause.failed_tests(text)
    print(f"ship full suite: red on {head[:8]} -- bisecting the lands since the last ship"
          + (f" on its failing tests: {' '.join(tests)}" if tests else ""), flush=True)  # fmt: skip
    res.pop("finished", None)  # the worker runs on: the record says so (GOH.SHIPBISECTCOV)
    res["status"] = "bisecting"

    def at(probe: dict) -> None:
        res["bisect_step"] = probe
        save(res)

    try:
        res["bisect"] = bisect(repo, Path(res["result"]).parent / "suite-wt", head, tests=tests,
                               step=at)  # fmt: skip
    finally:
        res.pop("bisect_step", None)
        res["status"] = "failed"
        res["finished"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        save(res)
    b = res["bisect"]
    print(f"ship full suite: bisect: {b['culprit'][:12] or 'no single land'} "
          f"({b.get('subject', '')}); fault {b['fault']}", flush=True)  # fmt: skip
    return bool(b.get("flake"))


def retry_after_red(res: dict, save: Callable[[dict], None], retried: bool) -> bool:
    """tools/ship.py, its suite step red: `after_red`; True when it found a flake and the ship has
    not RETRIED yet, the record set running again for the suite step's one more run."""
    if not after_red(res, save) or retried:
        return False
    res.update(status="running", retried=res.get("retried", 0) + 1)
    res.pop("failed_step", None)
    res.pop("finished", None)
    save(res)
    print("ship full suite: the bisect found a flake -- the suite step runs once more", flush=True)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ship_full_run.py", description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, required=True, help="the main checkout the ship releases")
    ap.add_argument("--wt", type=Path, required=True, help="the run's own worktree (kept)")
    ap.add_argument("--profiles-out", default="", help="where the run writes its reused and fresh "
                    "binary tests (tests/_prof_reuse.py, GOH.COVPROFILEREUSE)")  # fmt: skip
    a = ap.parse_args(argv)
    if a.profiles_out:
        os.environ["GOH_PROF_REUSE_OUT"] = a.profiles_out
    return run(a.repo.resolve(), a.wt)


if __name__ == "__main__":
    sys.exit(main())
