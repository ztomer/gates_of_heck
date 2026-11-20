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
and releases. It is never skipped: no other run of the tip judged the floors against the last
ship (the idle lander's judged no base, and a land's measured nothing). The run is the idle
lander's own command (full_run.INSTRUMENTED, the coverage gate's Rust mode with its external
suite) with GOH_LAND_VERDICTS_RECORD=/dev/null and GOH_LAND_VERDICTS_REUSE=@tree (a pass JUDGED on
this very tree by a test measured running no binary is not run again, tests/_same_tree.py), in a
detached worktree kept between ships beside the ship results (`<ship dir>/suite-wt`: its build
caches stay warm), admitted as class `ship`. Its seconds are the step's, so the ship record keeps
the lock wait (`land_wait_s`) apart from the ship's own run (`own_s`).

A RED RUN (exit RED) stops the ship before anything is pushed or released, and `bisect` -- run by
the ship's worker AFTER it gives the land lock back, so lands go on -- runs the same command on the
lands since the last ship (`goh-landed`, the tips land.sh recorded), halving, to the first red one,
and files a fault item naming it (`loopctl file`). The last ship is taken as green: it released.

    ship_full_run.py --repo MAIN --wt DIR   exit 0 green (or owed none), 1 red, 2 no verdict

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
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import full_run  # lib/: the instrumented command, the worktree, the .gatesrc read (no lock)
import reuse  # lib/: the land record (`landed_identity`)
import reuse_fault  # lib/: `loopctl file` and the queue's current phase
import ship_suite  # lib/: the check the release kit makes

ROOT = Path(__file__).resolve().parent.parent
MAX = full_run.MAX
# The tree's own passes of tests measured running no binary are not run again (tests/_same_tree.py):
# every binary-running, carried or unmeasured test still runs instrumented (GOH.SHIPBREACH0137).
REUSE = "@tree"
GREEN, RED, NO_VERDICT = 0, 1, 2


def owed(repo: Path) -> str:
    """ "" when the ship of REPO owes its run, else why not."""
    return "" if full_run.declares_full(repo) else "the repo declares no GOH_SHIP_FULL_SUITE=on"


def last_ship(repo: Path, sha: str) -> str:
    """The commit of the newest version tag below SHA (the last ship), or "" when none is."""
    tag = full_run._git(repo, "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", sha)
    if tag.returncode or not tag.stdout.strip():
        return ""
    return full_run._git(repo, "rev-parse", f"{tag.stdout.strip()}^{{commit}}").stdout.strip()


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
    env = dict(os.environ, GOH_LAND_VERDICTS_RECORD=os.devnull, GOH_GATE_CLASS="ship",
               GOH_LAND_VERDICTS_REUSE=REUSE)  # fmt: skip
    if base:
        env["GOH_CHANGE_BASE"] = base
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
    rc = _judge(repo, wt, sha, last_ship(repo, sha), argv)
    if rc == GREEN and (why := ship_suite.full_suite(repo, sha)[1]):  # green, yet no record: say so
        print(f"ship full suite on {sha[:8]}: green, but {why}", flush=True)
        return NO_VERDICT
    print(f"ship full suite on {sha[:8]}: {'green' if rc == GREEN else 'red'}", flush=True)
    return rc


def lands(repo: Path, base: str, head: str) -> list[str]:
    """The landed tips in BASE..HEAD (land.sh's record, lib/reuse.py `landed_identity`), oldest
    first, HEAD last: what this ship coalesces."""
    span = full_run._git(repo, "rev-list", "--reverse", "--first-parent",
                         f"{base}..{head}" if base else head).stdout.split()  # fmt: skip
    out = [c for c in span[:-1] if reuse.landed_identity(repo, c) is not None]
    return [*out, head] if span else []


def bisect(repo: Path, wt: Path, head: str, argv: list[str] | None = None) -> dict:
    """HEAD's run was red: the first red land since the last ship, halving over `lands`, and the
    fault item filed for it. A probe with no verdict stops the bisect, naming the range left."""
    base = last_ship(repo, head)
    cands, runs = lands(repo, base, head), []
    lo, hi = -1, len(cands) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        rc = _judge(repo, wt, cands[mid], base, argv)
        runs.append({"sha": cands[mid], "verdict": {GREEN: "green", RED: "red"}.get(rc, "none")})
        if rc == NO_VERDICT:
            break
        lo, hi = (mid, hi) if rc == GREEN else (lo, mid)
    found = {"base": base, "lands": len(cands), "runs": runs}
    if not cands or hi - lo > 1:
        return {**found, "culprit": "", "fault": "not filed: the bisect reached no single land"}
    culprit, since = cands[hi], cands[lo] if lo >= 0 else base
    subject = full_run._git(repo, "log", "-1", "--format=%s", culprit).stdout.strip()
    return {**found, "culprit": culprit, "since": since, "subject": subject,
            "fault": file_fault(repo, culprit, since, base, subject)}  # fmt: skip


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


def after_red(res: dict, save: Callable[[dict], None]) -> None:
    """The ship's worker, its land lock given back: a suite step that ran RED is bisected and the
    result says which land (`bisect`); any other failure is no red run to bisect."""
    step = next((s for s in res.get("steps", []) if s.get("name") == "suite"), {})
    if (
        res.get("failed_step") != "suite"
        or step.get("status") != "failed"
        or step.get("code") != RED
    ):
        return
    repo = Path(res["repo"])
    head = full_run._git(repo, "rev-parse", "HEAD").stdout.strip()
    print(
        f"ship full suite: red on {head[:8]} -- bisecting the lands since the last ship", flush=True
    )
    res["bisect"] = bisect(repo, Path(res["result"]).parent / "suite-wt", head)
    save(res)
    b = res["bisect"]
    print(f"ship full suite: bisect: {b['culprit'][:12] or 'no single land'} "
          f"({b.get('subject', '')}); fault {b['fault']}", flush=True)  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ship_full_run.py", description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, required=True, help="the main checkout the ship releases")
    ap.add_argument("--wt", type=Path, required=True, help="the run's own worktree (kept)")
    a = ap.parse_args(argv)
    return run(a.repo.resolve(), a.wt)


if __name__ == "__main__":
    sys.exit(main())
