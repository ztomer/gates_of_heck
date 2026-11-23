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
    main-moved  main moved between the gate and the merge (land.sh: "main is at X after the merge,
                not the gated Y"): the verdict was true of a tree that is no longer main's tip
    killed      the pre-land tests died to a signal (`exit -15`: the agent's own exit, not a test)
    diff-cov    the pre-step's diff-coverage judge refused: changed lines no test runs
    gate-step   a gate step failed and no test or structural line says why (clippy, the file-events
                ceiling ...): `detail` names the step; GOH.REDOTHER
    unclassified none of the above: a FAULT OF THIS CLASSIFIER, never a bucket. `goh land --report`
                names each one's log (GOH.REDOTHER)

The first that applies wins, in that order. The enqueue-stage reds (a pre-step or a guard check)
leave their failing nodes only in the record's `why`, or in the pre-step's own log it points at;
both are read with the same node rules as a gate log (GOH.REDOTHER). Telling a defect from a
load-flaky failure needs a rerun, which a log does not hold: `pre-test` and `gate-test` say
where it failed, never which.
"""

from __future__ import annotations

import re
from pathlib import Path

FAILED = re.compile(r"(?:^|[\s:])FAILED (tests/[^\s:]+\.py(?:::[^\s]+)?)", re.MULTILINE)
# The judges that read the whole tree, by name (tools/affected_tests.py TREE_NAMED and ALWAYS).
RATCHET_NAME = re.compile(r"::(?:test_every_|test_no_)")
RATCHET_FILES = ("tests/test_self_walk.py", "tests/test_gate_temp_leaks.py")
# Failing node ids in a record's `why` ("failing: a, b" / "check: NODE refused"), in a guard-check
# line ("guard tests 2/3: REFUSED in 19 s (exit 1): a, b") and a pre-step log's ERROR lines.
NODE = re.compile(r"(tests/[^\s,()]+\.py(?:::[^\s,()]+)?)")
GUARD_REFUSED = re.compile(r"^check: guard tests \d+/\d+: REFUSED in [^:]*: (.+)$", re.MULTILINE)
ERRORED = re.compile(r"^\s*ERROR (tests/[^\s:]+\.py(?:::[^\s]+)?)", re.MULTILINE)
PRESTEP_LOG = re.compile(r"its output is (\S+\.log)")
KILLED = re.compile(r"\(exit -\d+[,)]")
MAIN_MOVED = re.compile(r"main is at \w+ after the merge, not the gated")
GATE_FAILED = re.compile(r"the gate (?:failed on|went red)")
# A structural step, read as an indented line too (the guard check prints it under a `check:`),
# or a guard check that is not a test table or a test.
STRUCTURAL_LINE = re.compile(
    r"^\s*✗ structural: .* failed|^check: (?!guard tests|\d+ table tests? refused).*: REFUSED in",
    re.MULTILINE,
)
NOT_WHY = re.compile(
    r"FAILED \(exit status|printed \d+|land\.sh:|pre-push:|steps? failed|full output"
)
STEP_FAILED = re.compile(r"^✗ \[\d+/\d+\] FAILED \(exit status \d+\): (.+)$", re.MULTILINE)
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
    "main-moved",
    "killed",
    "diff-cov",
    "gate-step",
    "unclassified",
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


def nodes(rec: dict, log: str) -> list[str]:
    """Every failing test node a red leaves, once each: the log's FAILED lines, the guard check's
    REFUSED lines and the record's `why`, then the pre-step's own log when `why` points at one."""
    why = str(rec.get("why") or "")
    found = failed_tests(log)
    for line in GUARD_REFUSED.findall(log):
        found += NODE.findall(line)
    found += NODE.findall(why)
    pointed = PRESTEP_LOG.search(why)
    if pointed:
        try:
            text = Path(pointed.group(1)).read_text(errors="replace")
        except OSError:
            text = ""
        found += failed_tests(text) + ERRORED.findall(text)
    return list(dict.fromkeys(n.rstrip(" -") for n in found))


def detail(log: str) -> str:
    """For a `gate-step` red: the failed step and the first line that says why ("" when the log
    names no failed step). `goh land --report` shows it where a test node would be."""
    steps = STEP_FAILED.findall(log)
    if not steps:
        return ""
    lines = (re.sub(r"^\d+:", "", ln) for ln in log.splitlines())  # the digest numbers its lines
    why = next(
        (ln[:160] for ln in lines if ln.startswith("✗ ") and not NOT_WHY.search(ln)),
        "",
    )
    return f"{steps[0]}: {why}" if why else steps[0]


def classify(rec: dict, log: str) -> str:
    """The cause of one red attempt (CAUSES); a landed one has none and is never asked."""
    why = str(rec.get("why") or "")
    if "tip moved" in why:
        return "tip-moved"
    if MAIN_MOVED.search(log):
        return "main-moved"
    if "conflicts with main" in log:
        return "conflict"
    if "release canary" in log and "REFUSED" in log:
        return "canary"
    if "the gate never ran" in log:
        return "no-verdict"
    if KILLED.search(why) or KILLED.search(log):
        return "killed"
    pre_red = "the pre-land tests failed" in log or "the pre-land tests failed" in why
    tests = nodes(rec, log)
    if pre_red and not tests and "deadline of" in log:
        return "deadline"
    if "changed executable lines uncovered" in why + log:
        return "diff-cov"
    if STRUCTURAL_LINE.search(log) or STRUCTURAL.search(log):
        return "structural"
    if tests and all(is_ratchet(t) for t in tests):
        return "ratchet"
    enqueue = why.startswith(("enqueue ", "check: "))
    if tests and (pre_red or enqueue):
        return "pre-test"
    if tests and ("pre-land green" in log or GATE_FAILED.search(log)):
        return "gate-test"
    if pre_red:
        return "pre-test"  # the tool says the pre-land tests failed and names none
    if detail(log):
        return "gate-step"
    return "unclassified"
