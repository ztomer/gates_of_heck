"""Is this a gate that never started, or one that started and said no? Pure: no I/O, no repo.

Split out of `check_empty_scope.py` for the 500-line rule, and by CONCERN: that file sweeps every
gate over an empty tree, and the question here is what a gate's OUTPUT means — which is the part
worth testing on its own, because getting it wrong is silent.

The distinction exists because "ran and refused" and "never ran" both end in a non-zero exit, and
only one of them means the gate looked at its scope. A gate that DEMANDS an argument (`--policy`,
say) prints argparse's usage line and exits 2 having inspected nothing, and scored as a refusal it
lands in the healthy column — silent, and looking deliberate. A gate that timed out is the same
error one layer down.
"""

from __future__ import annotations


def first_error_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:120]
    return "(no message)"


def is_usage_error(text: str) -> bool:
    """A gate that printed a USAGE line and an `error:` and never did any work.

    Deliberately narrow. argparse's shape is what we match — `usage:` plus `error:` — because a
    gate that merely mentions the word "usage" in its own prose must not be mistaken for one that
    could not start. Exit status alone cannot decide it: several gates use 2 for a legitimate
    refusal, so the text is what distinguishes them.
    """
    low = text.lower()
    return "usage:" in low and "error:" in low
