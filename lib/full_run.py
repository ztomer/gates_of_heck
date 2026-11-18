"""lib/full_run.py -- the instrumented full run's lock-free parts, shared (GOH.SHIPSTARVE).

The idle lander (idle_suite) and goh ship's own run (ship_full_run) make the same run the same
way: the tip's .gatesrc read for GOH_SHIP_FULL_SUITE, a kept detached worktree at the tip, and
INSTRUMENTED, the coverage gate's Rust mode with its external suite. Split from the idle lander so
the ship's run (under the land lock) reaches no module that takes the idle lock, which ranks below
it (lib/lock_order.tsv; the lock order check follows imports and named files). Takes no lock.
Pinned by tests/test_ship_starve.py (and the idle lander's own tests).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gitrun

MAX = "90m"  # a land gate's own bound


def _git(repo: Path, *args: str, write: bool = False) -> subprocess.CompletedProcess:
    """git in REPO (no inherited GIT_* answers for it): a reader under a timeout, a writer
    (worktree add, checkout, clean) TERMed as a group at its deadline so no lock is stranded."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if not write:
        return gitrun.run_reader(list(args), cwd=repo, timeout=120, env=env)
    done = gitrun.run_writer(list(args), cwd=repo, deadline_s=600, env=env)
    return done or subprocess.CompletedProcess(["git", *args], 124, "", "stopped at its deadline")


def declares_full(main: Path) -> bool:
    """MAIN's .gatesrc sets GOH_SHIP_FULL_SUITE=on (read in a child, as the release kit does)."""
    if not (main / ".gatesrc").is_file():
        return False
    read = (
        "unset GOH_SHIP_FULL_SUITE; . ./.gatesrc >/dev/null 2>&1 </dev/null; "
        'printf %s "${GOH_SHIP_FULL_SUITE-}"'
    )
    out = subprocess.run(["bash", "-c", read], cwd=main, capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, check=False, timeout=60)  # fmt: skip
    return out.stdout == "on"


def prepare(main: Path, wt: Path, sha: str) -> str:
    """WT checked out at SHA, clean of tracked and untracked changes (ignored build caches kept);
    "" or why it could not be."""
    if (wt / ".git").exists():
        steps = [(wt, "checkout", "-q", "--detach", "-f", sha), (wt, "clean", "-fdq")]
    else:
        wt.parent.mkdir(parents=True, exist_ok=True)
        _git(main, "worktree", "prune", write=True)
        steps = [(main, "worktree", "add", "-q", "--detach", "-f", str(wt), sha)]
    for repo, *args in steps:
        done = _git(repo, *args, write=True)
        if done.returncode:
            return f"git {' '.join(args[:2])}: {done.stderr.strip()[:300]}"
    return ""


# THE INSTRUMENTED RUN (GOH.COVCARRY, the owner's term 2): the idle full suite is the coverage
# gate's Rust mode with its external suite, as the land gate runs it (gates/rust_gate.sh
# rust_coverage_checks) -- the whole suite against the INSTRUMENTED binary, so its record is the
# fresh instrumented full run every ship requires (lib/reuse.py full_suite) and the shadow the
# coverage carry is compared with (lib/prof_carry.py). Read from the tip's own .gatesrc, in bash.
INSTRUMENTED = (
    ". ./.gatesrc >/dev/null 2>&1 </dev/null; "
    'exec bash gates/coverage_gate.sh --lang rust --external "$GOH_COV_RUST_EXTERNAL" '
    '--floor "$GOH_COV_FLOOR_RUST" .'
)
