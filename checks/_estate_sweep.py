"""Where the R3 estate corpus sweep RUNS, which is a question with a measured answer (2026-10-05).

Split out of `check_probes_pass.py` at the 500-line cap, and cut at a real boundary: that file
discovers and runs SELF-PROOFS, and this is a different sweep with a different subject — real
consumer corpora rather than a repo's own gates. The two had no call site in common before this
change; they have one now, and it is one call.

# THE 5x, and what it actually was

`check_no_unreaped_spawn.py`'s probe grew from 36 measured shapes to 55 and this repo's pytest suite
went 226 s to 1190 s, which reads like the table. It was not the table. Measured, one at a time:

    check_no_unreaped_spawn.py --probe (55 shapes)          0.06s
    check_estate_corpus.py    (8 entries)                  73.4s
    check_probes_pass.py --root <this repo>                 116.8s

The probe is in-process analysis; 55 shapes cost a sixteenth of a second and the table is not the
cost and was not shrunk. The minutes were this sweep, and specifically the fact that it ran on
EVERY invocation of `check_probes_pass.py` regardless of what `--root` named:

  * a run against a throwaway fixture estate holding one trivial gate cost **53.7s**, of which
    53.66s was the sweep -- and the answer could not differ from the answer this repo gets. The
    same command with `--no-corpus` takes 0.03s.
  * this gate's own `--probe` cost **54s**, because the probe calls `main()` three times on a
    fixture, and each of those ran the sweep.
  * five tests asserting that a FIXTURE's registry is sound were paying for eight real consumer
    repos, and would have gone red because a media_server commit landed. That is a test about a
    fixture whose verdict depends on the estate: the wrong kind of dependency, and expensive.

# THE RULE, and why it is scoping rather than caching

The sweep answers ONE question: do *the checkers in this `checks/` directory* still go red on a
violation planted in a real consumer corpus. Its subject is those checkers. `--root` names the tree
whose self-proofs are being swept, which is a different subject entirely. So the sweep runs where its
subject is, and says so when it does not.

Nothing is cached and nothing is skipped-then-remembered: a foreign root re-derives the answer every
time it is asked, the sweep's content is still proved entry by entry by `tests/test_estate_corpus.py`,
and the wiring that puts it in this repo's own `structural.sh --full` is untouched. What changed is
that a question about a fixture stopped being answered by a sweep over eight unrelated repositories.

`--estate` forces it anywhere, for a caller that wants the old behaviour on purpose.
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import foreign_repo_env  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import info  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# The sweep copies real subtrees and spawns a checker per entry, so it earns its own ceiling rather
# than sharing the per-probe one. Named here because the ceiling belongs to the sweep.
CORPUS_TIMEOUT = 600


def corpus_sweep(root=None, force=False) -> bool:
    """Run the R3 sweep: every house checker against a corpus taken from a real consumer repo.

    Lives beside `check_probes_pass.py` rather than in `gates/structural.sh`, because that file was
    outside the ownership this round had and wiring an unwired gate is worse than wiring it here
    (house rule: a gate nobody runs is indistinguishable from a gate that passes).
    `check_probes_pass.py` is the meta-gate every repo's hook runs, and R3 is the same question one
    level out from R1: not "does this gate carry a self-proof" but "does it still refuse a violation
    in the shape the estate really has".

    Cost is paid only on a machine that HAS the estate: an absent corpus is reported per entry and
    skipped, which is also why a repo whose hooks delegate here pays nothing for this.
    """
    if not force and not estate_in_scope(root):
        info(
            "estate corpus sweep NOT RUN: it measures the checkers in "
            f"{HERE}, not the tree under {root}. It runs where its subject is -- this repo's own "
            "`gates/structural.sh --full`, and the pytest suite -- and `--estate` runs it anywhere."
        )
        return True
    command = [sys.executable, os.path.join(HERE, "check_estate_corpus.py")]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=CORPUS_TIMEOUT,
        check=False,
        env=foreign_repo_env(),
    )
    for line in (result.stdout + result.stderr).strip().splitlines():
        info(line)
    return result.returncode == 0


def estate_in_scope(root) -> bool:
    """True when `root` is the checkout the checkers in THIS directory belong to.

    `os.path.realpath` on both sides, so a symlinked path, or one carrying `..`, is the same repo
    rather than a different-looking one. A foreign repo answers False, and `corpus_sweep` says so
    instead of running a sweep whose subject is not on disk here.
    """
    if not root:
        return False
    return os.path.realpath(str(root)) == os.path.realpath(REPO)
