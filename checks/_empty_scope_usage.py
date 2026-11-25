"""Is this a gate that never started, or one that started and said no? Pure: no I/O, no repo.

Split out of `check_empty_scope.py` for the 500-line rule, and by CONCERN: that file sweeps every
gate over an empty tree, and the question here is what a gate's OUTPUT means — which is the part
worth testing on its own, because getting it wrong is silent.

The distinction exists because "ran and refused" and "never ran" both end in a non-zero exit, and
only one of them means the gate looked at its scope. A gate that DEMANDS an argument (`--policy`,
say) prints argparse's usage line and exits 2 having inspected nothing, and scored as a refusal it
lands in the healthy column — silent, and looking deliberate. A gate that timed out is the same
error one layer down.

`checks/check_empty_scope.py`: The `--full` sweep: runs every gate over a skeleton with no CONTENT
and fails any that passes -- a gate that reports success over nothing is the class. It collects
`check_*.py` AND `check_*.sh` (a shell gate is a gate: Adv360-Pro-ZMK's `check_keymap_build.sh` and
`check_upstream_drift.sh` were never swept), runs a shell gate with bash, and reads the shell's own
usage shape (exit 2 and a usage line) as "never looked". Each gate runs under a stall and a 4x wall
backstop (`checks/_empty_scope_run.py`), never the wall alone: a starved gate says `TIMED OUT after
Ns -- starved`, and timeouts alone exit 124, which the release canary reads as no verdict, not a
consumer turned red (GOH.CANARYLOAD). Delegated by the native tier. Pinned by
`test_empty_scope_starved.py`, `test_gate_runtime_path.py`, the empty-tree rows of each checker's
own suite.
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
