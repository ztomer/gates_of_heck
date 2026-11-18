#!/usr/bin/env python3
"""lib/ship_full_run.py -- a ship makes the instrumented full run it stands on (GOH.SHIPSTARVE).

The owner's term (GOH.COVCARRY, 2026-11-17): "a fresh full instrumented run happens on main's tip
before every ship". Only the idle lander made that run (idle_suite), and it yields the moment
any land is pending or running; with up to 8 lanes the queue is rarely idle, so a ship starved. And
a ship waits for the land lock, then releases whatever main became meanwhile: a run made for the tip
it started on is stale by the time it holds the lock (2026-11-18: the ship of 8150234a waited 20
min, took 2a3f0dc9, and was refused, "the full run of tree f43a0244e13d drove the plain binary").

So the ship's FIRST step (`suite`, tools/ship.py) runs here, holding the land lock: main cannot move
under it, so the run judges exactly the tip the release kit then checks (lib/ship_suite.py) and
releases. A tip that already has its instrumented full run (the idle lander's, or an earlier ship's)
is not run again: the step is skipped, saying whose run it stands on. The run is the idle lander's
own command (full_run.INSTRUMENTED, the coverage gate's Rust mode with its external suite) with
GOH_LAND_VERDICTS_RECORD=/dev/null, in a detached worktree kept between ships beside the ship
results (`<ship dir>/suite-wt`: its build caches stay warm), admitted as class `ship` (ahead of
every `work` waiter). It never yields: a land waits on the lock the ship holds, as for any ship
step. Its seconds are the step's, so the ship record keeps the lock wait (`land_wait_s`) apart from
the ship's own run (`own_s`) and `goh budget` judges the ship, not the queue (lib/budget.py).

    ship_full_run.py --repo MAIN --wt DIR    exit 0 green (or owed none), 1 red or no worktree

Pinned by tests/test_ship_starve.py.
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import full_run  # lib/: the instrumented command, the worktree, the .gatesrc read (no lock)
import ship_suite  # lib/: the check the release kit makes

ROOT = Path(__file__).resolve().parent.parent
MAX = full_run.MAX


def skip_reason(repo: Path, sha: str) -> str:
    """Why the ship of REPO at SHA owes no run of its own ("" when it owes one)."""
    if not full_run.declares_full(repo):
        return "the repo declares no GOH_SHIP_FULL_SUITE=on"
    rec, _ = ship_suite.full_suite(repo, sha)
    if rec is not None:
        return f"{sha[:8]} already has its instrumented full run (by {rec.get('by') or '?'})"
    return ""


def default_argv() -> list[str]:
    return [sys.executable, str(ROOT / "tools" / "load.py"), "--background-ok", "ship full suite",
            "--max", MAX, "0", "--", "bash", "-c", full_run.INSTRUMENTED]  # fmt: skip


def run(repo: Path, wt: Path, argv: list[str] | None = None) -> int:
    """The instrumented full run on REPO's HEAD in WT, its output the step's log; its exit."""
    sha = full_run._git(repo, "rev-parse", "HEAD").stdout.strip()
    if not sha:
        print(f"ship full suite: {repo} names no HEAD", flush=True)
        return 1
    if why := skip_reason(repo, sha):
        print(f"ship full suite: none owed: {why}", flush=True)
        return 0
    if why := full_run.prepare(repo, wt, sha):
        print(f"ship full suite: no worktree at {sha[:8]}: {why}", flush=True)
        return 1
    print(f"ship full suite on {sha[:8]} in {wt}", flush=True)
    env = dict(os.environ, GOH_LAND_VERDICTS_RECORD=os.devnull, GOH_GATE_CLASS="ship")
    rc = subprocess.run(argv or default_argv(), cwd=wt, env=env, stdin=subprocess.DEVNULL,
                        check=False).returncode  # fmt: skip
    if rc == 0 and (why := ship_suite.full_suite(repo, sha)[1]):  # green, yet no record: say so
        print(f"ship full suite on {sha[:8]}: green, but {why}", flush=True)
        return 1
    print(f"ship full suite on {sha[:8]}: {'green' if rc == 0 else f'red (exit {rc})'}", flush=True)
    return 0 if rc == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ship_full_run.py", description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, required=True, help="the main checkout the ship releases")
    ap.add_argument("--wt", type=Path, required=True, help="the run's own worktree (kept)")
    a = ap.parse_args(argv)
    return run(a.repo.resolve(), a.wt)


if __name__ == "__main__":
    sys.exit(main())
