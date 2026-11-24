"""lib/land_check_cli.py -- `goh land --check [--rules] WORKTREE`, split from lib/land_check.py at
its line cap (GOH.RULESLEFT).

`--rules` is the lane's pre-enqueue door: the land gate's cheap steps and every rule test
(lib/rule_tests.py, the declared guards), sharded wide, with no judge the diff chooses and no diff
coverage -- every house rule a land will hold the change to, in well under a minute. The full
check adds the judges, and both name each refusal in one pass.
"""

from __future__ import annotations

import time
from pathlib import Path

from lib import land_check


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
    # A commit no commit gate judged (a cherry-pick, a rebase, --no-verify) is judged now, by the
    # gate its pre-commit would have run, and named (GOH.CHERRYPICKGATE).
    from lib import commit_stamp

    refused_commits = commit_stamp.check(top, info)
    for r in refused_commits:
        err(f"goh land --check: {r}")
    # An item the range names with no changelog fragment is refused here, where the lane can add
    # it, not at the ship's release (GOH.FRAGMENTATLAND).
    from lib import fragment_check

    no_fragment = fragment_check.missing(top)
    for r in no_fragment:
        err(f"goh land --check: {r}")
    # A job past its deadline is a refusal by its name (land_check.NO_VERDICT, GOH.CHECKGIVEUP300):
    # 2026-11-24, 8 of 8 guard shards timed out and the check said "every guard green in 331.8 s".
    say = info

    refused = land_check.run(Path(top), None if rules else q, say, main, tables=not rules,
                             rules=rules)  # fmt: skip
    refused += [f"the commit gate on {r.split(' ', 5)[4]}" for r in refused_commits]
    refused += [f"the changelog fragment of {r.split(' ', 1)[0]}" for r in no_fragment]
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
