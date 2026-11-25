"""lib/land_check_cli.py -- `goh land --check [--rules] WORKTREE`, split from lib/land_check.py at
its line cap (GOH.RULESLEFT).

`--rules` is the lane's pre-enqueue door: the land gate's cheap steps and every rule test
(lib/rule_tests.py, the declared guards), sharded wide, with no judge the diff chooses and no diff
coverage -- every house rule a land will hold the change to, in well under a minute. The full
check adds the judges, and both name each refusal in one pass.

Pinned by `test_rule_tests.py`, `test_land_check_commits.py`.

`lib/land_check.py`: `goh land --check WORKTREE`, run by every `goh land --enqueue --land` before
its pre-step (GOH.LANDCHECK): the land's structural guards over the committed tip in seconds, side
by side -- the tree's `.gatesrc` GOH_LAND_CHECK_STEPS (each verbatim one of GOH_CI_STEPS; a
`--no-pre` enqueue adds GOH_LAND_NOPRE_STEPS, its Rust coverage deferred, GOH.NOPREBATCH) and the
guard tests: every rule test by its shape (`rule_tests.py`: a plain `test_every_*`/`test_no_*`
function with no temporary tree, GOH.RULESLEFT), every function of a `# goh-reads-staged:` file, the
functions a `# goh-land-guard: NAME...` line names (none named: the whole file), each kept while the
suite's measurement costs it at most 10 s (the list once learned from past refusals is retired), run
by the tree's own `tools/pytest.sh`, as its gate runs them (plain pytest for a tree with none; a run
stopped before pytest is no verdict, by its reason), in up to three serial runs balanced by that
cost; a job past its deadline (300 s stretched by the clamp measured on the check's own thread,
`proc_deadline.stretch`, at most 1500 s) or a pytest with no verdict REFUSES by its name
(GOH.CHECKGIVEUP300). A refusal is recorded red at enqueue naming its `guards` (`stage: check`);
every red land's record gets `guard`/`guards` read from its log (failing test nodes, `✗ [subgate]`
names, failing gate steps), which feeds `goh land-profile --guards`, never the check's choice of
guards. `goh land --check` alone adds every judge the diff can break (GOH.LANDCHECK2,
`judge_tests`): the pre-step's whole-tree judges (`tools/affected_tests.py --only judges`) and table
judges, each under 120 s by measurement, within a 480 s budget in all (`land_judges.py`: table
judges first, then the cheapest; the rest named by count and left to the pre-step, GOH.RULESLEFT),
over 8 runs, plus the pre-step's diff coverage judge run alone (`--only diff-cov`); every job runs
to its verdict, so one pass names every refusal. `goh land --check --rules` runs the steps and the
rule tests alone over 8 runs, with no judge (`land_check_cli.py`); the enqueue's own check runs the
rule tests too. Invoked by `tools/landq.py`.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lib import check_jobs, land_check


def _commits(top: str, say: Callable[[str], object], gated: bool) -> list[str]:
    """The commits' refusals, as `tip` says."""
    from lib import commit_stamp

    declares = any((Path(top) / f).is_file() for f in (".gatesrc", "tools/gate.sh"))
    if gated and not declares:
        return []
    # the enqueue is the land path's run, a lane's own --check whatever it was admitted as
    return commit_stamp.check(top, say, gate_class="pre" if gated else None)


def tip(top: str, say: Callable[[str], object], err: Callable[[str], object],
        rest: Callable[[], list[str]], gated: bool = False) -> list[str]:  # fmt: skip
    """What TOP's commits are refused by, each said by ERR, and what REST (the check's guards, run
    beside the commits' gates) refuses: a commit no commit gate judged (a cherry-pick, a rebase,
    --no-verify), judged now by the gate its pre-commit would have run (GOH.CHERRYPICKGATE); an
    item the range names with no changelog fragment, refused where the lane can add it, not at the
    ship's release (GOH.FRAGMENTATLAND). GATED (the enqueue): the commit gate only in a tree that
    declares goh's gates (.gatesrc, tools/gate.sh)."""
    from lib import fragment_check

    fragments = fragment_check.missing(top)
    with ThreadPoolExecutor(max_workers=1) as pool:
        gates = pool.submit(_commits, top, say, gated)
        guards = rest()
        commits = gates.result()
    for r in (*commits, *fragments):
        err(f"goh land --check: {r}")
    named = [re.sub(r"^the commit gate on (\S+).*", r"the commit gate on \1", r.split("\n")[0])
             for r in commits]  # fmt: skip
    absent = [f"the changelog fragment of {r.split(' ', 1)[0]}" for r in fragments]
    return [*named, *absent, *guards]


def cli(worktree: str, q: Path, rules: bool = False) -> int:
    """`goh land --check [--rules] WORKTREE`: its committed tip's guards; 0 green, 1 refused, 2
    unusable. RULES: the cheap steps and every rule test alone, no judge, no diff coverage."""
    from lib import gitrun  # no-lock readers under a deadline (GOH.GITKILL)
    from tui.lib import err, info, ok, warn

    found = gitrun.run_reader(("rev-parse", "--show-toplevel"), repo=worktree, timeout=60)
    top = found.stdout.strip() if found.returncode == 0 else ""
    if not top:
        err(f"goh land --check: {worktree} is not a git checkout")
        return 2
    dirty = gitrun.run_reader(("status", "--porcelain"), repo=top, timeout=60)
    if dirty.returncode or dirty.stdout.strip():
        some = ", ".join(ln[3:] for ln in dirty.stdout.splitlines()[:5])
        err(
            f"goh land --check: {top} is not its commit ({some or 'git status failed'}) -- the "
            "land judges the commit, and an uncommitted or untracked file would judge another "
            "tree; commit or remove them, then check"
        )
        return 2
    listing = gitrun.run_reader(("worktree", "list", "--porcelain"), repo=top, timeout=60)
    main = listing.stdout.split("\n", 1)[0].removeprefix("worktree ")
    t0 = time.monotonic()
    door = "rules" if rules else ""
    head = gitrun.run_reader(("rev-parse", "HEAD"), repo=top, timeout=60).stdout.strip()
    # A job past its deadline is a refusal by its name (land_check.NO_VERDICT, GOH.CHECKGIVEUP300):
    # 2026-11-24, 8 of 8 guard shards timed out and the check said "every guard green in 331.8 s".
    # A stop signal leaves no job and no worktree behind (lib/check_jobs.py, GOH.CHECKVERDICT).
    with check_jobs.guarded(door, main, head, signals=True) as job:
        refused = tip(top, info, err, lambda: land_check.run(
            Path(top), None if rules else q, info, main, tables=not rules, rules=rules,
            door=door or "check"))  # fmt: skip
        job.rc = int(bool(refused))
    took = f"{time.monotonic() - t0:.1f} s"
    t1 = time.monotonic()
    if not rules:
        land_check.cov_risk.say(top, main, warn)  # GOH.DIFFCOVLAND: a warning, never a verdict
        info(f"diff coverage risk read in {time.monotonic() - t1:.1f} s")
    if refused:
        err(f"goh land --check: refused in {took} by {', '.join(refused)}")
        return 1
    ok(f"goh land --check: every guard green in {took}")
    return 0
