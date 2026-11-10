"""lib/not_applicable.py -- a step that needs the owner's estate, run where there is none (CI.PUBLIC2).

A GitHub-hosted runner is a clean machine: no ~/Projects of consumer repos, no installed_repos
registry. A step that measures the estate (the R3 corpus sweep and its probe, the release canary)
cannot judge there, and it has exactly two dishonest options -- pass (a sweep of nothing read as
green) or fail (red for a reason no commit can fix). The honest third is NOT APPLICABLE: said by
name, with what is missing, and distinct from both.

It is narrow on purpose (engineering rule 13, probe-first):

  * only a HOSTED run may say it -- `GITHUB_ACTIONS=true`, which GitHub sets and the owner's shell
    does not. On any other machine the step judges as it always did, and an absent prerequisite is
    the step's own failure: the owner's machine is the judge of record and runs every step;
  * only when the step PROBED its prerequisite and found it absent -- the caller checks the thing
    it needs (a corpus root, a registry file), never "am I in CI" alone;
  * it exits `EXIT` (78, EX_CONFIG: "the configuration this needs is not here"), which
    gates/local_ci.sh reports as its own line and accepts only on a hosted run, so a step that says
    it off a hosted runner is RED there (tests/test_not_applicable.py, both directions).
"""

from __future__ import annotations

import os
import sys

EXIT = 78
MARK = "NOT APPLICABLE"


def hosted(env: dict[str, str] | None = None) -> bool:
    """True on a GitHub Actions run, and nowhere else."""
    return (os.environ if env is None else env).get("GITHUB_ACTIONS") == "true"


def line(step: str, missing: str) -> str:
    return (
        f"{MARK} on this hosted runner: {step} needs {missing}, which is not here -- "
        "not a pass and not a failure; the owner's machine is the judge of record and runs it"
    )


def declare(step: str, missing: str) -> bool:
    """Say the step is not applicable, if this run may: True (said) on a hosted run, False
    (nothing said) anywhere else, where the caller fails as it always did."""
    if not hosted():
        return False
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tui.lib import warn

    warn(line(step, missing))
    return True
