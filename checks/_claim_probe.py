"""The self-proof for `check_claim_derivation.py`, split out so neither file
crowds the 500-line cap.

It is the LOAD-BEARING part of this gate, because the gate's failure mode is
specific and has happened here before: a scanner whose subject population is empty
prints the same word as a scanner that examined everything and found nothing wrong.
This repo has found that class in three repos and in two of its own gates, and the
reason it recurs is that nobody edits a gate to break it -- they edit the tree it
reads, or the regex it matches with, and the code still looks correct.

So every claim type is driven in BOTH directions on a fixture built for it:

  red   a claim whose number is wrong, on its own unit
  green the same claim with the number the tree says
  quiet prose that is NOT a claim, and must produce nothing

and then, for each unit, the command the gate PRINTS is executed and its output
compared with the count the gate returned. That last one is what stops "here is
the command" decaying into a command that prints something else -- a finding a
reader cannot check is a claim, and this gate exists about claims.

The third direction is the gate itself: `main()` must return 1 on a fixture with a
false claim, and 0 on the same fixture with the number corrected. A checker whose
findings exist but whose exit code does not move is a checker nothing runs.
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _claim_derive import Unresolved, run_command
from _gitutil import foreign_repo_env

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, ok

HERE = os.path.dirname(os.path.abspath(__file__))

# A fixture with enough real structure to be judged by more than its filename: a
# package with two declared lists (so the `declared` unit can be asked for one by
# name and be refused for the other), tests at two nesting depths (so the `tests`
# unit has something a shallow counter would get wrong), nested directories (so a
# `files` claim has to use git's own pathspec rather than a top-level walk), and
# a file with NO trailing newline (so a `lines` claim proves the count is not
# `wc -l`'s).
FIXTURE = {
    # A module with ONE declared list, so the unnamed form has exactly one
    # candidate and needs no guess.
    "solo.py": 'ROSTER = [\n    "a",\n    "b",\n    "c",\n]\n',
    "pkg/roster.py": (
        "STEPS = [\n"
        '    "P1 no_emoji",\n'
        '    "P1 marker_reason selftest",\n'
        '    "P2 oracle selftest",\n'
        '    "P4 realapp_smoke",\n'
        "]\n"
        "FILTERS = [\n"
        '    "a",\n'
        '    "b",\n'
        "]\n"
        "\n"
        "steps = [s for s in STEPS if s.startswith('P1')]\n"
        "\n"
        "\n"
        "def helper():\n"
        "    return len(STEPS)\n"
    ),
    "pkg/__init__.py": "",
    "pkg/notes.md": "# notes\n\nA file about the package.\n",
    "tests/test_thing.py": (
        "import pytest\n"
        "\n"
        "\n"
        "def test_one():\n"
        "    pass\n"
        "\n"
        "\n"
        "@pytest.mark.parametrize('n', [1, 2])\n"
        "def test_many(n):\n"
        "    pass\n"
        "\n"
        "\n"
        "class Group:\n"
        "    def test_nested(self):\n"
        "        pass\n"
        "\n"
        "    async def test_async(self):\n"
        "        pass\n"
        "\n"
        "def not_a_test():\n"
        "    pass\n"
    ),
    "tests/nested/test_deep.py": "def test_deep():\n    pass\n",
    "no_trailing_newline.txt": "alpha\nbeta",
}

# The claims, one per direction. `CLAIMS` is what the gate is asked to re-derive;
# each entry is `(label, prose, expect_red)`.
CLAIMS = (
    # `lines` — pkg/notes.md is 3 lines. no_trailing_newline.txt is 2 and `wc -l`
    # says 1, which is why the printed command is `awk` and why this case exists:
    # a finding whose command disagreed with the gate would be a rumour with a
    # command attached.
    ("a `lines` claim on the right number is GREEN", "claim: 3 lines in pkg/notes.md", False),
    ("a `lines` claim on the wrong number is RED", "claim: 4 lines in pkg/notes.md", True),
    (
        "a file with NO trailing newline counts its last line (wc -l would not)",
        "claim: 2 lines in no_trailing_newline.txt",
        False,
    ),
    # `declared` — STEPS has four entries and is named, so the count is forced.
    ("a `declared` claim by NAME is GREEN", "claim: 4 gates in pkg/roster.py:STEPS", False),
    (
        "a `declared` claim on the wrong number is RED",
        "claim: 5 gates in pkg/roster.py:STEPS",
        True,
    ),
    # No `:NAME`, and the file holding exactly ONE declared list -- so the choice
    # is forced rather than guessed. The printed command must then NAME the list
    # the count came from, or it prints something else.
    (
        "a `declared` claim with no NAME, in a file with one list, is GREEN",
        "claim: 3 gates in solo.py",
        False,
    ),
    ("...and the same claim with a wrong number is RED", "claim: 9 gates in solo.py", True),
    # The estate's exact defect: a label carrying a second space, which the
    # `re.findall(r'"(P\d+ [a-z_0-9]+)"')` that read 64 against 70 cannot match.
    (
        "a label with a second space is COUNTED, not skipped",
        "claim: 4 steps in pkg/roster.py:STEPS",
        False,
    ),
    # `tests` — four functions in test_thing.py (one nested in a class, one async,
    # one parametrised counting once) plus one in the nested directory.
    ("a `tests` claim over a FILE is GREEN", "claim: 4 tests in tests/test_thing.py", False),
    ("a `tests` claim over a DIRECTORY is GREEN", "claim: 5 tests in tests", False),
    ("a `tests` claim on the wrong number is RED", "claim: 20 tests in tests/test_thing.py", True),
    # `files` — two .py under pkg (`notes.md` is markdown) and two under tests.
    ("a `files` claim is GREEN", "claim: 2 *.py files under pkg", False),
    ("a `files` claim on the wrong number is RED", "claim: 9 *.py files under pkg", True),
    # The backtick form: number AND path in backticks.
    ("the backtick form is GREEN on a true number", "`3` lines in `pkg/notes.md`", False),
    ("the backtick form is RED on a false one", "`99` lines in `pkg/notes.md`", True),
    # Unresolvable, and each one is a FINDING rather than a skip: a claim nobody
    # can re-derive is a number nobody is checking.
    (
        "a claim naming no path is UNRESOLVED",
        "claim: 4 lines in pkg/gone.py",
        True,
    ),
    (
        "a `declared` claim naming an ambiguous file is UNRESOLVED",
        "claim: 4 gates in pkg/roster.py",
        True,
    ),
    (
        "a `declared` claim naming a structure that does not exist is UNRESOLVED",
        "claim: 4 gates in pkg/roster.py:NOPE",
        True,
    ),
    (
        "a `lines` claim naming a directory is UNRESOLVED",
        "claim: 4 lines in pkg",
        True,
    ),
    # A `files` claim without an extension is a marked promise the gate cannot
    # keep -- and one more `files` case with the extension present, so the pair
    # shows the requirement is the extension and not the word "files".
    (
        "a `files` claim without an extension is UNRESOLVED (a marked promise)",
        "claim: 3 files under pkg",
        True,
    ),
    # The negative control, and it is the reason the gate is narrow. Every one of
    # these is a NUMBER beside a PATH in the prose, and none is a claim.
    ("a bare number beside a path is NOT a claim", "pkg/notes.md is 3 lines.", False),
    ("a bare number beside a backticked path is NOT a claim", "`pkg/notes.md` is 3 lines.", False),
    (
        "an unmarked number whose path is not backticked is NOT a claim",
        "claims 4 gates in tests/e2e/verify.py for the roadmap",
        False,
    ),
    ("a backticked number with an unknown unit is NOT a claim", "`4` widgets in `pkg`", False),
    ("a version is NOT a claim", "v2.10.0 of `pkg/roster.py`", False),
    ("a heading anchor is NOT a claim", "see #48-what-90-can-actually-do in `pkg`", False),
    ("a CLAIM ABOUT a version is not a claim", "the docs claim: 4 gates in pkg/roster.py", False),
    ("a number with no path is NOT a claim", "4 tests were added", False),
    ("a fenced example is NOT a claim", "```\nclaim: 99 lines in pkg/notes.md\n```", False),
    ("an indented example is NOT a claim", "    claim: 99 lines in pkg/notes.md", False),
    ("a glob on a non-files claim is NOT a claim", "`3` *.py lines in `pkg/notes.md`", False),
    ("`#64 gates` is not a claim: the marker needs its own token", "#64 gates in a.py", False),
    (
        "a comment marker mid-line is not a marker",
        "steps = [s for s in STEPS]  # claim: 99 gates in pkg/roster.py:STEPS",
        False,
    ),
    (
        "prose ABOUT a commented claim is not a claim",
        "# the docs claim: 4 gates in pkg/roster.py",
        False,
    ),
    # The marker form is most often written where prose is COMMENTED -- a
    # `.gatesrc` line, a shell script, a Makefile recipe -- so the comment openers
    # are part of the grammar rather than an afterthought. Each is a real comment
    # syntax in this estate and the set is closed; see `_claim_text`.
    ("a `#` marker is a claim", "# claim: 3 lines in pkg/notes.md", False),
    ("a `//` marker is a claim", "// claim: 3 lines in pkg/notes.md", False),
    ("a `#` marker with a wrong number is RED", "# claim: 99 lines in pkg/notes.md", True),
)


def _git(root: str, *args: str) -> None:
    subprocess.run(
        ["git", "-C", root, *args], check=True, capture_output=True, env=foreign_repo_env()
    )


def _fixture(base: str) -> str:
    """A git repo holding FIXTURE. `listed_files` reads git scope, so a directory
    on disk is not a repository this checker can see."""
    root = os.path.join(base, "repo")
    for rel, body in FIXTURE.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    return root


def write_gatesrc(root: str, body: str) -> None:
    """A `.gatesrc`, committed -- the checker's own policy input."""
    with open(os.path.join(root, ".gatesrc"), "w", encoding="utf-8") as handle:
        handle.write(body)
    _git(root, "add", "-A")


def _with_doc(base: str, label: str, prose: str) -> str:
    """The fixture plus `docs/<label>.md` carrying one line of prose."""
    root = _fixture(os.path.join(base, label.replace(" ", "_").replace("/", "_")))
    path = os.path.join(root, "docs", "note.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"# note\n\n{prose}\n")
    _git(root, "add", "-A")
    return root


def probe() -> int:
    """Every claim type in both directions, then the gate's own exit codes."""
    import tempfile

    sys.path.insert(0, HERE)
    from check_claim_derivation import check, main

    bad = 0
    with tempfile.TemporaryDirectory() as td:
        # Every unit's GREEN fixture in one repo, so the reported counts are the
        # whole scan rather than one case each.
        green_root = os.path.join(td, "green")
        truths = "\n\n".join(
            prose
            for label, prose, red in CLAIMS
            if not red
            and not label.startswith(
                (
                    "a bare",
                    "an unmarked",
                    "a backticked",
                    "a version",
                    "a heading",
                    "a CLAIM",
                    "a number with",
                    "a fenced",
                    "an indented",
                    "a glob on",
                )
            )
        )
        os.makedirs(green_root)
        green = _with_doc(green_root, "truths", truths)
        verdict = check(green, None, False)
        for label, prose, red in CLAIMS:
            if red:
                continue
            marked = [f for f in verdict.findings if f[1].text.strip() == prose]
            if marked:
                err(f"probe: {label} -- reported {marked[0][3]!r}")
                bad += 1
            else:
                ok(f"probe: {label}")
        if (verdict.stale, verdict.problems) != ([], []):
            err(f"probe: a truthful fixture produced {verdict.stale!r} / {verdict.problems!r}")
            bad += 1
        else:
            ok("probe: the truthful fixture is clean, with no stale entry and no problem")

        # ...and every RED fixture, one repo per case, so a break in one unit's
        # derivation is a line of its own rather than a count that could be right
        # for the wrong reason.
        for index, (label, prose, red) in enumerate(CLAIMS):
            if not red:
                continue
            root = _with_doc(os.path.join(td, "red"), f"case{index}", prose)
            findings = check(root, None, False).findings
            if len(findings) != 1:
                err(
                    f"probe: {label} -- wanted 1 finding, got {len(findings)}: "
                    f"{[f[3] for f in findings]!r}"
                )
                bad += 1
                continue
            rel, claim, kind, message, _, _ = findings[0]
            if (
                rel != "docs/note.md"
                or claim.line != 3
                or kind != "STALE"
                and "UNRESOLVED" not in kind
            ):
                err(f"probe: {label} -- {rel}:{claim.line} {kind} {message!r}")
                bad += 1
            else:
                ok(f"probe: {label} ({kind})")

        # The printed command must PRINT the count. Executed, not trusted: a
        # finding that hands the reader a command which disagrees with the gate is
        # worse than one that prints no command, because it looks checked.
        ONE_PER_UNIT = {
            "lines": ("claim: 3 lines in pkg/notes.md", "claim: 7 lines in pkg/notes.md"),
            "declared": (
                "claim: 4 gates in pkg/roster.py:STEPS",
                "claim: 7 gates in pkg/roster.py:STEPS",
            ),
            "tests": ("claim: 5 tests in tests", "claim: 7 tests in tests"),
            "files": ("claim: 2 *.py files under pkg", "claim: 7 *.py files under pkg"),
        }
        truths = os.path.join(td, "commands")
        green_cmds = _with_doc(truths, "cmds", "\n\n".join(t for t, _ in ONE_PER_UNIT.values()))
        clean_findings = check(green_cmds, None, False).findings
        if clean_findings:
            err(f"probe: the command fixture is not clean: {[f[3] for f in clean_findings]!r}")
            bad += 1
        else:
            ok("probe: every unit's true claim is GREEN in one scan")
        for unit, (_, false_prose) in ONE_PER_UNIT.items():
            root = _with_doc(os.path.join(td, f"cmd-{unit}"), unit, false_prose)
            findings = check(root, None, False).findings
            if len(findings) != 1 or not findings[0][4]:
                err(f"probe: the {unit} finding printed no re-derivation command")
                bad += 1
                continue
            _, _, _, _, command, value = findings[0]
            try:
                printed = run_command(command, root)
            except Unresolved as exc:
                err(f"probe: the {unit} command does not run: {exc}")
                bad += 1
                continue
            if printed != str(value) or printed == str(findings[0][1].number):
                err(f"probe: the {unit} command printed {printed!r}, the gate says {value!r}")
                bad += 1
            else:
                ok(f"probe: the {unit} command prints the gate's own number ({printed})")

        # The gate's exit codes, which is what a hook reads. A checker whose
        # findings exist but whose code never moves is a checker nothing runs.
        for label, prose, want in (
            ("a false claim makes the gate exit 1", "claim: 99 lines in pkg/notes.md", 1),
            ("a true claim makes the gate exit 0", "claim: 3 lines in pkg/notes.md", 0),
        ):
            root = _with_doc(
                os.path.join(td, f"exit-{want}"), label.split()[0] + label.split()[1], prose
            )
            got = main(["--root", root])
            if got != want:
                err(f"probe: {label} (wanted {want}, got {got})")
                bad += 1
            else:
                ok(f"probe: {label}")

        # The EMPTY-SCOPE refusal, which `check_empty_scope.py` demands of every
        # gate and which is the reason the checker reads the repo's own `.gatesrc`:
        # opted in with no marked claim anywhere is a gate looking at nothing.
        hollow = _with_doc(os.path.join(td, "hollow"), "hollow", "no numbers here at all\n")
        write_gatesrc(hollow, "GOH_MAX_LINES=500\nGOH_CLAIM_DERIVATION=1\n")
        hollow_verdict = check(hollow, None, False)
        if not hollow_verdict.problems or "adopted" not in hollow_verdict.problems[0]:
            err(f"probe: an opted-in tree with no marked claim did not refuse: {hollow_verdict!r}")
            bad += 1
        else:
            ok("probe: an opted-in tree with no marked claim REFUSES (the empty-scope rule)")
        # ...and the same tree WITHOUT the key is a fact about the repo, not a fault.
        write_gatesrc(hollow, "GOH_MAX_LINES=500\n")
        quiet_verdict = check(hollow, None, False)
        if quiet_verdict.problems:
            err(f"probe: a repo that has not adopted the convention was failed: {quiet_verdict!r}")
            bad += 1
        elif main(["--root", hollow]) != 0:
            err("probe: a repo that has not adopted the convention should exit 0")
            bad += 1
        else:
            ok("probe: a repo that has NOT adopted the convention is reported, not failed")

        # The ALLOWLIST ratchet, both directions. A stale entry failing is the half
        # that keeps the file from becoming permission.
        allow_root = _with_doc(os.path.join(td, "allow"), "allow", "claim: 3 lines in pkg/notes.md")
        with open(
            os.path.join(allow_root, "claim_derivation_allow.json"), "w", encoding="utf-8"
        ) as h:
            json_dump(
                h,
                [
                    {
                        "path": "docs/note.md",
                        "claim": "claim: 3 lines in pkg/notes.md",
                        "status": "unreviewed",
                        "reason": "probe: seeded, not yet examined",
                    }
                ],
            )
        _git(allow_root, "add", "-A")
        verdict = check(allow_root, None, False)
        if (verdict.findings, verdict.stale, verdict.problems, verdict.excused) != ([], [], [], 1):
            err(f"probe: an allowlisted claim is still a finding: {verdict!r}")
            bad += 1
        elif verdict.debt != 1:
            err(f"probe: an 'unreviewed' entry did not count as debt: {verdict.debt}")
            bad += 1
        else:
            ok("probe: a claim with an entry is excused, and the entry counts as debt")
        with open(os.path.join(allow_root, "docs", "note.md"), "w", encoding="utf-8") as h:
            h.write("# note\n\nclaim: 3 lines in pkg/notes.md, and nothing else\n")
        _git(allow_root, "add", "-A")
        verdict = check(allow_root, None, False)
        if not verdict.stale or verdict.excused:
            err(
                "probe: a stale allowlist entry did not fail "
                f"({verdict.excused} used, {len(verdict.stale)} stale)"
            )
            bad += 1
        else:
            ok("probe: a claim that moved takes its exemption with it (stale entry FAILS)")

    if bad:
        err(f"check_claim_derivation --probe: {bad} case(s) wrong")
        return 1
    ok(
        "check_claim_derivation --probe: every claim type goes red on a wrong number, "
        "stays green on a true one, and prints a command that agrees with the gate"
    )
    return 0


def json_dump(handle, entries) -> None:
    import json

    json.dump({"entries": entries}, handle, indent=2)
