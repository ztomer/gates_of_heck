"""lib/full_run.py -- the instrumented full run's lock-free parts, shared (GOH.SHIPSTARVE).

The idle lander (idle_suite) and goh ship's own run (ship_full_run) make the same run the same
way: the tip's .gatesrc read for GOH_SHIP_FULL_SUITE, a kept detached worktree at the tip, and
INSTRUMENTED, the coverage gate's Rust mode with its external suite. Split from the idle lander so
the ship's run (under the land lock) reaches no module that takes the idle lock, which ranks below
it (lib/lock_order.tsv; the lock order check follows imports and named files). Takes no lock.
Pinned by tests/test_ship_starve.py (and the idle lander's own tests).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gitrun
import pid_owner
import ship_records

MAX = "90m"  # a land gate's own bound
# The tree's own passes of tests measured running no binary are not run again (tests/_same_tree.py):
# every binary-running, carried or unmeasured test still runs instrumented (GOH.SHIPBREACH0137).
REUSE = "@tree"
IDLE = "idle-suite"  # the idle lander's state under the landing queue (lib/idle_suite.py)
PROFILES = "profiles.json"  # its run's reused and fresh binary tests, beside its worktree
SHIP_NAME = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]+\.json$")  # a ship result, not its side files


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
# coverage carry is compared with (lib/prof_carry.py: GOH_PROFCARRY_AUDIT=fresh makes the step
# compare its summary with the land's claim instead of claiming, GOH.COVCARRY2). Read from the
# tip's own .gatesrc, in bash.
INSTRUMENTED = (
    ". ./.gatesrc >/dev/null 2>&1 </dev/null; "
    "GOH_PROFCARRY_AUDIT=fresh exec bash gates/coverage_gate.sh --lang rust "
    '--external "$GOH_COV_RUST_EXTERNAL" '
    '--floor "$GOH_COV_FLOOR_RUST" .'
)


# ONE RUN, TWO CALLERS (GOH.IDLESUITERETIRE). On v0.149.2 (2026-11-22) the idle lander's run and the
# ship's run judged the same tree at once, and the ship's release land waited 388 s for the slot the
# idle run held. The two now run the SAME judgement (`judge_env`: the diff coverage based at the
# last ship, the @tree reuse key), so a finished green idle run of a tip IS the ship's run of it
# (`adoptable`), and the idle lander starts none while a ship of the repository is in flight.


def last_ship(repo: Path, sha: str) -> str:
    """The commit of the newest version tag below SHA (the last ship), or "" when none is."""
    tag = _git(repo, "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", sha)
    if tag.returncode or not tag.stdout.strip():
        return ""
    return _git(repo, "rev-parse", f"{tag.stdout.strip()}^{{commit}}").stdout.strip()


def judge_env(base: str, gate_class: str) -> dict[str, str]:
    """The environment of the instrumented run of a tip whose diff coverage is based at BASE (the
    last ship), admitted as GATE_CLASS: the store's record alone, the tree's own verdicts reused."""
    env = dict(os.environ, GOH_LAND_VERDICTS_RECORD=os.devnull, GOH_GATE_CLASS=gate_class,
               GOH_LAND_VERDICTS_REUSE=REUSE)  # fmt: skip
    env.pop("GOH_CHANGE_BASE", None)
    if base:
        env["GOH_CHANGE_BASE"] = base
    return env


def landq_dir() -> Path:
    return Path(os.environ.get("GOH_LANDQ_DIR") or Path.home() / ".local/state/goh/landq")


def adoptable(sha: str, base: str, q: Path | None = None) -> tuple[Path | None, str]:
    """(the idle run's worktree, "") when the idle lander's last run judged SHA green with its diff
    coverage based at BASE -- the very run a ship of SHA makes -- else (None, why not)."""
    state = (q or landq_dir()) / IDLE
    try:
        last = json.loads((state / "last.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None, "no idle run is recorded"
    if last.get("sha") != sha:
        return None, f"the idle run judged {str(last.get('sha') or 'nothing')[:8]}, not {sha[:8]}"
    if last.get("outcome") != "green":
        return None, f"the idle run of {sha[:8]} is {last.get('outcome') or 'no verdict'}"
    if last.get("base", None) != base:
        return None, f"the idle run's diff coverage was based at {str(last.get('base'))[:8]}"
    return state / "wt", ""


def _alive(rec: dict) -> bool:
    """The ship's worker still runs, by the start time beside its pid (GOH.STRAYSIGNAL2): a pid
    handed to another process once the worker ended reads as gone."""
    return pid_owner.alive_line(str(rec.get("pid_stamp") or rec.get("pid")))


def ships_in_flight(repo: str) -> list[str]:
    """The ship results of REPO still running: status `running` and their worker alive (one that
    has not said its pid yet counts as running)."""
    out = []
    for p, rec in ship_records.in_dir(ship_records.ship_dir() / repo):
        if not SHIP_NAME.match(p.name):
            continue
        if rec.get("status") in ("running", "bisecting") and (not rec.get("pid") or _alive(rec)):
            out.append(p.name)
    return out
