"""lib/land_cause.py: why a land went red, read from its done record and its log (GOH.PREPARITY).

OWNER, 2026-11-17: "we need to optimize the overall process." 23 of 55 goh land attempts went red
that day, each a full gate run and another queue wait. This is the classifier the hand count was
made with, so `goh land --report`'s `cause` column (lib/landq_report.py) is the same reading:

    tip-moved   the worktree's tip moved after the enqueue: the request judged another commit
    conflict    the rebase onto main conflicted (GOH.LANDUNION's class)
    canary      the release canary refused: a CONSUMER went red, not this tree
    deadline    the pre-land tests reached their deadline and nothing failed (before
                GOH.PRESTEPDEADLINE a deadline was red)
    no-verdict  the gate never ran: its slot wait expired (GOH.PREWAITVERDICT, land.sh exit 75);
                nothing was judged, so nothing failed
    structural  a structural gate step: the file-length cap, the map row, ruff ...
    ratchet     only repo-wide judges failed (a test_every_/test_no_ test, tests/test_self_walk.py,
                tests/test_gate_temp_leaks.py): each reads every file or key, so any diff can
                turn it red and the enqueue's first phase runs them all
    pre-test    the pre-land tests chose a test and it failed: a defect, or a load-flaky test
    gate-test   a test failed in the full gate after a GREEN pre-land: one the pre-step did not
                choose, or a load-flaky one
    other       none of the above (read the log)

The first that applies wins, in that order. Telling a defect from a load-flaky failure needs a
rerun, which a log does not hold: `pre-test` and `gate-test` say where it failed, never which.
"""

from __future__ import annotations

import re

FAILED = re.compile(r"(?:^|[\s:])FAILED (tests/[^\s:]+\.py(?:::[^\s]+)?)", re.MULTILINE)
# The judges that read the whole tree, by name (tools/affected_tests.py TREE_NAMED and ALWAYS).
RATCHET_NAME = re.compile(r"::(?:test_every_|test_no_)")
RATCHET_FILES = ("tests/test_self_walk.py", "tests/test_gate_temp_leaks.py")
STRUCTURAL = re.compile(
    r"FAILED \(exit status \d+\): \./gates/structural\.sh|^✗ structural: .* failed", re.MULTILINE
)
CAUSES = (
    "tip-moved",
    "conflict",
    "canary",
    "deadline",
    "no-verdict",
    "structural",
    "ratchet",
    "pre-test",
    "gate-test",
    "other",
)


def failed_tests(log: str) -> list[str]:
    """The test node ids a log names as FAILED, once each, in order."""
    out: list[str] = []
    for found in FAILED.findall(log):
        node = found.rstrip(" -")
        if node not in out:
            out.append(node)
    return out


# A pytest failure section's header (`____ test_x[a] ____`) and the `=== ... ===` rule that ends it.
SECTION = re.compile(r"^_{3,} (.+?) _{3,}$")
RULE = re.compile(r"^={3,} .* ={3,}$")
EACH, MOST, WIDE = 40, 10, 300  # lines kept per failing test, tests kept, characters per line


def excerpt(log: str, each: int = EACH, most: int = MOST) -> str:
    """What a pre-step red record keeps of its run (GOH.PRESTEPLOG): each failing test's pytest
    section, its LAST `each` lines (the frames nearest the failure and its E lines), at most
    `most` of them, then the FAILED and ERROR summary lines; "" when the log holds none."""
    sections: list[list[str]] = []
    current: list[str] | None = None
    for line in log.splitlines():
        if SECTION.match(line):
            current = [line]
            sections.append(current)
        elif RULE.match(line):
            current = None
        elif current is not None:
            current.append(line[:WIDE])
    out: list[str] = []
    for head, *body in sections[:most]:
        cut = len(body) - each
        out += [head, *([f"    ... {cut} lines above cut"] if cut > 0 else []), *body[-each:]]
    if len(sections) > most:
        out.append(f"... {len(sections) - most} more failing tests' sections cut")
    out += [ln[:WIDE] for ln in log.splitlines() if ln.startswith(("FAILED ", "ERROR "))]
    return "\n".join(out)


def is_ratchet(node: str) -> bool:
    return bool(RATCHET_NAME.search(node)) or node.split("::")[0] in RATCHET_FILES


def classify(rec: dict, log: str) -> str:
    """The cause of one red attempt (CAUSES); a landed one has none and is never asked."""
    why = str(rec.get("why") or "")
    if "tip moved" in why:
        return "tip-moved"
    if "conflicts with main" in log:
        return "conflict"
    if "release canary" in log and "REFUSED" in log:
        return "canary"
    if "the gate never ran" in log:
        return "no-verdict"
    pre_red = "the pre-land tests failed" in log
    tests = failed_tests(log)
    if pre_red and not tests and "deadline of" in log:
        return "deadline"
    if STRUCTURAL.search(log):
        return "structural"
    if tests and all(is_ratchet(t) for t in tests):
        return "ratchet"
    if pre_red and tests:
        return "pre-test"
    if tests and "pre-land green" in log:
        return "gate-test"
    return "other"
