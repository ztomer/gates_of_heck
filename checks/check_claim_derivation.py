#!/usr/bin/env python3
"""Re-derive every MARKED number in a repo's prose from the tree it describes.

    ```
    `70` gates in `tests/e2e/verify.py`                 the backtick form
    claim: 321 lines in tools/necrohand_mcp_server.py   the marker form
    ```

Both of those spellings are EXAMPLES, and they are fenced because a fenced block
is where this gate keeps its own syntax -- see `checks/_claim_text.py`. The forms
are spelled with real paths from the estate they were found in, because an example
with a made-up path teaches the reader the wrong alphabet.

## THE DEFECT, and why prose is the dangerous half

Three independent adversarial reviews of the games estate converged on one
finding: **the prose is written one step ahead of the mechanism, in files where the
prose is far more convincing than the mechanism is load-bearing.** Each of these
was found by hand, at hours apiece, and every one is a number that stopped being
true while its sentence kept reading perfectly:

    roadmap_state.py      "64 declared gates"     verify.py declares 70 -- the
                                                  regex cannot match a label
                                                  carrying a second space
    tools/gate.sh         "the same 26 gates"     verify_gate_names() returns 29
    check_mcp_server.py   "477 lines, 18 tests"   321 lines, 20 tests -- in the
                                                  gate whose job is COUNTING
    .coverage-floor.json  "No floor is recorded"  floors["unit+app"] = 94.95, six
                                                  lines below
    README.md             "86 files, 1059 tests"  101 files, 1732 tests
    CLAUDE.md             "main is 66 ahead"      `0  0`

A number in prose cannot watch itself. That is the whole reason these survived: not
that nobody re-checked them, but that re-checking one means opening three files,
running a command, and reading the difference -- so it happens in an audit and
never again. This gate makes it the price of writing the number down.

The defining property of the class is that **the mechanism and the sentence are in
different files, and only one of them is load-bearing**. `roadmap_state.py`'s
parser is the thing that decides what the roadmap says; its docstring is the thing
a reader trusts; and the parser was fixed twenty lines from the docstring that
still quoted its old output.

## WHAT IS CHECKED, and what is deliberately not

Four units, each a function of the tree with no second implementation to drift
(`checks/_claim_derive.py` carries the exactness argument for each):

    `N` lines in `PATH`          the file-length cap's own `line_count`
    `N` tests in `PATH`          test functions, counted by `ast`
    `N` gates in `PATH[:NAME]`   elements of a NAMED module-level list, by `ast`
    `N` `*.EXT` files under `D`  the length of `git ls-files`'s own answer

That is the whole table, and this docstring says so in the form the gate reads:
claim: 4 checks in checks/_claim_derive.py:CHECKS. It is a live claim about this
repo, re-derived on every run of this gate over this repo, so the sentence and the
code cannot drift apart -- which is the class this gate exists for, applied to the
gate's own documentation first. Adding a fifth derivation without updating it
turns this gate red on itself.

Narrow on purpose. Precision matters more than recall here, and the reason is
arithmetic rather than taste: a false positive costs a reader's trust in the gate
and gets the gate switched off, while a false negative costs one stale sentence
that was going to be read anyway. So the unit vocabulary is CLOSED, an unknown
unit word is not a claim, and every refusal is reported by name.

NOT checked, and each exclusion is a decision rather than a gap:

* **Unmarked prose.** This gate would not have caught any of the six defects above
  as they stood -- they are bare numbers, and reading bare numbers means reading
  English. What it does is make the MARKED claim a gate, so the first edit after a
  defect is the one that re-derives the number rather than retyping it. The looser
  rule ("any number in a sentence naming a path") was rejected for the reasons in
  `checks/_claim_text.py`: it cannot tell an inventory from an incident, a current
  claim from a quoted one, a count from a version, and when it is wrong it is
  wrong loudly and in bulk.
* **A `tests` claim's PARAMETRISED cases.** One `@pytest.mark.parametrize` row is
  one test function here, which is the `EXPECTED_TESTS`-style quantity. A suite
  that wants the collected count wants a command, not a number.
* **Anything but Python for `tests`/`gates`.** A Swift or Rust gate roster is not
  counted, because counting it exactly means owning a second parser for a language
  this gate does not parse.
* **Fenced and indented code blocks in markup.** That is where documentation about
  THIS gate shows its own syntax; reading it would make the gate's documentation a
  finding about itself. Inline code spans are NOT skipped, because a backtick is
  this gate's own marker.

## EVERY FINDING PRINTS THE COMMAND THAT PRODUCES THE TRUTH

A finding that says "the tree says 64" is a claim. One that says "the tree says 64
-- `python3 -c ...`" is something the reader can check, and `--probe` EXECUTES
those printed commands against fixtures and compares, so they cannot decay into
commands that print something else. That is the difference between a gate that
reports and a gate that nags.

    check_claim_derivation.py                 # every tracked text file
    check_claim_derivation.py --staged        # the INDEX, for pre-commit scope
    check_claim_derivation.py --exclude RE    # exempt a vendored/generated tree
    check_claim_derivation.py --probe         # prove this gate can go red

## EXEMPTIONS are a ratchet, in `claim_derivation_allow.json` at the repo root

    ```
    {"entries": [{"path": "docs/plan.md",
                  "claim": "`70` gates in `tests/e2e/verify.py`",
                  "status": "unreviewed",
                  "reason": "seeded 2026-10-04; not yet examined"}]}
    ```

Each entry excuses ONE claim in ONE file, matched on the claim as written with all
whitespace collapsed -- so a `ruff format` cannot revoke an exemption, which is the
same reason `check_no_kill_by_name` collapses whitespace. An entry matching no
claim in the scanned files is STALE and fails: a claim that got fixed must take
its exemption with it. Every entry needs a reason; `status` is `legitimate` (a
decision, and its reason is the argument someone can challenge) or `unreviewed`
(debt, counted and warned about on every run).

Opt in per repo with `GOH_CLAIM_DERIVATION=1` in `.gatesrc`: this gate lands RED
in every repo in the estate, because every repo in the estate has this defect. A
gate that goes red in twenty places on the day it lands is a gate that gets
disabled, so each repo turns it on when it has marked its claims or fixed them.

Exit codes: 0 clean (or a named non-run), 1 findings, 2 usage/environment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _claim_allow import ALLOW_FILE, judge
from _claim_allow import load as load_allowlist
from _claim_derive import CHECKS, Unresolved
from _claim_text import claims_in_file, scannable
from _gitutil import content_bytes, listed_files, repo_root

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, info, ok, step, warn

MAX_REPORTED = 40


OPT_IN_KEY = "GOH_CLAIM_DERIVATION"


def adopted(root: str, staged: bool) -> bool:
    """Has THIS repo opted into the convention?

    Read from the repo's own `.gatesrc` rather than from the environment, and the
    distinction is load-bearing. The environment cannot answer it for
    `check_empty_scope.py`'s empty-tree sweep, which runs every checker over a
    skeleton with no content: there, "zero marked claims" and "nothing to mark"
    print the same thing, and a gate that cannot tell them apart is the blind-gate
    class this estate has found in three repos and in two of its own gates. The
    skeleton's `.gatesrc`, however, IS the real one -- so the sweep's tree says
    "opted in" and this gate correctly REFUSES over it.

    It is also what makes the gate usable: a repo that has not adopted the
    convention has no marked claims, and that is a fact about the repo rather than
    about the gate. Opted in with zero claims is a different fact, and a refusal.

    `present and non-empty`, matching `gatesrc::opt_in` and the shell's
    `[ -n "${GOH_CLAIM_DERIVATION:-}" ]`, because the two tiers must agree about
    which gates a repo runs or the pipeline and the binary disagree.
    """
    blob = content_bytes(root, ".gatesrc", staged=staged)
    if blob is None:
        return False
    for raw in blob.decode("utf-8", "replace").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep or key.strip() != OPT_IN_KEY:
            continue
        value = value.strip()
        for quote in ("'", '"'):
            if value.startswith(quote):
                value = value[1:].split(quote)[0]
                break
        else:
            for cut in (" #", "\t#"):
                if cut in value:
                    value = value.split(cut)[0]
        if value.strip():
            return True
    return False


class Verdict(NamedTuple):
    """What one `check()` call found.

    A NamedTuple rather than a tuple of seven because every field is read by
    something other than the function that wrote it -- the reporter, the probe, the
    pytest suite -- and a positional seven is how one of them silently reads
    `claims` as `excused` and reports a clean run over a red tree.
    """

    findings: list
    stale: list
    problems: list
    notes: list
    examined: int
    claims: int
    excused: int
    debt: int


def scan(root: str, files: list[str], staged: bool) -> tuple[list, int]:
    """(claims, files_read) for every MARKED claim in the scanned files.

    `claims` holds `(rel, claim, value, command)` for a claim the tree answered and
    `(rel, claim, None, reason)` for one it refused. Both are reported: a refusal is
    a number nobody is checking, which is the state this gate exists to end, so it
    is never a silent pass.

    A binary or unreadable blob is skipped and NOT counted, exactly as every other
    checker here does -- which is why the count of files read is reported next to
    the count of claims, so "there is nothing in here" never prints like "there is
    nothing wrong in here".
    """
    out = []
    read = 0
    for rel in files:
        blob = content_bytes(root, rel, staged=staged)
        if blob is None or b"\0" in blob[:8000]:
            continue
        read += 1
        found = claims_in_file(rel, blob.decode("utf-8", "replace"))
        if not found:
            continue
        for claim in found:
            if claim.malformed:
                out.append((rel, claim, None, claim.malformed))
                continue
            derive = CHECKS.get(claim.kind)
            if derive is None:
                # Unreachable while UNITS and CHECKS agree, and the refusal is
                # there because "silently skip" would leave an author believing a
                # number is policed when nothing reads it.
                out.append((rel, claim, None, f"no derivation for {claim.kind!r}"))
                continue
            try:
                value, command = derive(root, staged, claim.target, claim.name, claim.glob)
            except Unresolved as exc:
                out.append((rel, claim, None, str(exc)))
            except (OSError, ValueError) as exc:
                out.append((rel, claim, None, f"could not be re-derived: {exc}"))
            else:
                out.append((rel, claim, value, command))
    return out, read


def check(root: str, exclude: str | None, staged: bool) -> Verdict:
    """One pass over the tree. `Verdict` carries what it found.

    `problems` are REFUSALS -- an empty tracked scope, an allowlist this gate
    cannot read -- and any one of them is exit 1. `notes` are facts the reader
    wants ("nothing staged") and never a verdict. The two are separate fields
    because a gate that reports the second in the first's voice has made "nothing
    to check" sound like "nothing wrong", which is the empty-scope failure this
    estate has found in three repos.

    `debt` is how many USED allowlist entries are `unreviewed` -- seeded when the
    gate landed and never examined. Reported on every run, so a list of debt never
    reads as a clean list.

    Raises ValueError on an unparsable `--exclude`: a config error the caller turns
    into exit 2, because exempting the whole tree while still claiming to have
    examined it is the worse of the two answers.
    """
    if exclude:
        try:
            pattern = re.compile(exclude)
        except re.error as exc:
            raise ValueError(f"bad --exclude regex {exclude!r}: {exc}") from exc
    else:
        pattern = None

    files = [f for f in listed_files(root, staged=staged) if scannable(f)]
    if pattern:
        files = [f for f in files if not pattern.search(f)]
    if not files:
        # Nothing STAGED is a fact about the commit (a commit that only deletes, a
        # push with nothing pending); nothing TRACKED is a fact about the GATE --
        # its scope stopped matching the tree, and "0 files clean" would read like
        # a clean tree. `check_no_home_paths` draws the same line, and it is the
        # `check_empty_scope` requirement.
        if staged:
            return Verdict([], [], [], ["nothing staged — 0 text files to re-derive"], 0, 0, 0, 0)
        return Verdict(
            [],
            [],
            ["no text file in scope — refusing to report clean over zero files"],
            [],
            0,
            0,
            0,
            0,
        )

    entries, problems = load_allowlist(root, staged)
    if problems:
        return Verdict([], [], problems, [], len(files), 0, 0, 0)

    scanned, examined = scan(root, files, staged)
    findings, stale, used, excused = judge(scanned, entries, set(files))
    debt = sum(1 for i in used if entries[i].get("status", "unreviewed") == "unreviewed")
    # The empty-scope refusal. `check_empty_scope.py` builds a skeleton with the
    # SHAPE files and no content, and its `.gatesrc` is the real one -- so the
    # sweep's tree says "opted in" with no marked claim anywhere, which is the one
    # state in which this gate is looking at nothing and cannot say so.
    #
    # Full scope ONLY. Nothing STAGED is a fact about a commit: a commit that
    # touches no prose carries no claims, and refusing it would block every source
    # change in a repo that has adopted the convention. `check_no_kill_by_name`
    # draws the same line for the same reason.
    if not staged and not len(scanned) and adopted(root, staged):
        return Verdict(
            [],
            [],
            [
                (
                    f"{OPT_IN_KEY}=1 and no marked claim in the tree: the convention is "
                    "adopted and no number is under it, so this gate is inspecting nothing. "
                    "Mark a claim (`claim: N lines in path`), or turn the key off in .gatesrc."
                )
            ],
            [],
            examined,
            0,
            excused,
            debt,
        )
    return Verdict(findings, stale, [], [], examined, len(scanned), excused, debt)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None, help="repository to read (default: cwd's repo)")
    parser.add_argument(
        "--staged", action="store_true", help="judge the INDEX, not the working tree"
    )
    parser.add_argument(
        "--exclude", default=None, help="regex on repo-relative paths to exempt (vendored trees)"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--probe", action="store_true", help="prove this gate can go red")
    args = parser.parse_args(argv)
    if args.probe:
        from _claim_probe import probe

        return probe()

    root = args.root or repo_root() or "."
    if not os.path.isdir(root):
        err(f"[claim_derivation] not a directory: {root}")
        return 2

    try:
        verdict = check(root, args.exclude, args.staged)
    except ValueError as exc:
        err(f"[claim_derivation] {exc}")
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "findings": [
                        {"file": rel, "line": claim.line, "kind": kind, "detail": message}
                        for rel, claim, kind, message, _, _ in verdict.findings
                    ],
                    "stale_allow_entries": verdict.stale,
                    "problems": verdict.problems,
                    "notes": verdict.notes,
                    "examined": verdict.examined,
                    "claims": verdict.claims,
                    "allowlisted": verdict.excused,
                    "unreviewed": verdict.debt,
                },
                indent=2,
            )
        )
        return 1 if verdict.findings or verdict.stale or verdict.problems else 0

    for problem in verdict.problems:
        err(f"[claim_derivation] {problem}")
    findings, stale, problems = verdict.findings, verdict.stale, verdict.problems
    examined, claims, excused, debt = (
        verdict.examined,
        verdict.claims,
        verdict.excused,
        verdict.debt,
    )
    for rel, claim, kind, message, command, _ in findings[:MAX_REPORTED]:
        err(f"[claim_derivation] {rel}:{claim.line}: {kind} — {message}")
        step(f"the claim: {claim.text.strip()[:160]}")
        if command:
            step(f"re-derive: {command}")
    if len(findings) > MAX_REPORTED:
        err(f"    ... and {len(findings) - MAX_REPORTED} more")
    for entry in stale:
        err(
            f"[claim_derivation] stale entry in {ALLOW_FILE}: {entry.get('path')}: "
            f"{entry.get('claim')} — the claim it excuses is gone"
        )
    if findings or stale or problems:
        if findings:
            err(f"--- {len(findings)} finding(s): a number that stopped being true ---")
        if stale:
            err(f"--- {len(stale)} stale allowlist entr(ies): delete them ---")
        info("A claim that must stand carries `claim:` plus a reason in")
        info(f"{ALLOW_FILE}, and an entry that matches nothing FAILS -- a fixed claim takes it.")
        info("Otherwise: the command above is the truth, so keep the command and drop the number.")
        return 1
    if verdict.notes:
        # A NAMED non-run, and the reason it is not a refusal: nothing staged is a
        # fact about the commit. The empty-scope refusal below is for the TRACKED
        # tree, where zero files means the gate's own scope stopped matching.
        for note in verdict.notes:
            info(f"[claim_derivation] {note}")
        return 0
    if examined == 0:
        err("[claim_derivation] nothing to re-derive — refusing to report clean over zero files")
        return 1
    if claims == 0:
        ok(f"[claim_derivation] OK — {examined} text file(s) scanned, 0 MARKED claims")
        info("A claim is only read when the number AND the path are backticked, or the line")
        info("carries `claim:`. Unmarked numbers are prose, and prose is not this gate's job.")
        if not adopted(root, args.staged):
            info(f"Turn the convention on with {OPT_IN_KEY}=1 in .gatesrc once there are claims.")
        return 0
    if debt:
        warn(
            f"[claim_derivation]   {debt} of {excused} allowed claim(s) are 'unreviewed': seeded "
            "when the gate landed, not yet examined"
        )
    ok(
        f"[claim_derivation] OK — {examined} text file(s), all {claims} marked claim(s) re-derived "
        f"from the tree" + (f", {excused} allowlisted in {ALLOW_FILE}" if excused else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
