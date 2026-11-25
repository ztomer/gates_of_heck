"""`check_claim_derivation.py` -- the GRAMMAR and the four DERIVATIONS.

The class this suite exists for is the estate's most expensive silence: a number
that stopped being true while the sentence around it kept reading perfectly. All
six of the instances that motivated the gate were found by hand, at hours each,
and every one of them lives in a file whose prose is more convincing than its
mechanism is load-bearing.

Two questions, in that order:

  1. **Can it go red?** Every claim type, red-before and green-after, plus the
     negative controls -- prose that is a number beside a path and must stay quiet.
     `--probe` in the checker covers the same table through the checker's own
     entry point; this suite covers it through `check()`, which is what the gate
     calls, so a break in the seam between them is visible here and not there.
  2. **Is what it claims about itself true?** The printed re-derivation command is
     EXECUTED and compared with the number the gate returned, because a finding
     that hands the reader a command which disagrees with the gate is a rumour
     with a command attached -- and this gate is about rumours in sentences.

The other half -- which tree the gate looks at, and what counts as an example --
is `tests/test_check_claim_derivation_scope.py`. Both judge the same fixtures,
built once in `tests/claim_kit.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from claim_kit import ROSTER, doc, estate, findings, numbers, parse, run_command
from conftest import REPO_ROOT, commit_all, run_goh, stage, write

# ── the grammar ─────────────────────────────────────────────────────────────

CLAIMED = (
    ("claim: 3 lines in pkg/notes.md", "lines", "pkg/notes.md", ""),
    ("`3` lines in `pkg/notes.md`", "lines", "pkg/notes.md", ""),
    ("claim: 2 lines in no_newline.txt", "lines", "no_newline.txt", ""),
    ("claim: 4 gates in pkg/roster.py:STEPS", "declared", "pkg/roster.py", "STEPS"),
    ("`4` steps in `pkg/roster.py:STEPS`", "declared", "pkg/roster.py", "STEPS"),
    ("claim: 4 tests in tests/test_thing.py", "tests", "tests/test_thing.py", ""),
    ("claim: 5 tests in tests", "tests", "tests", ""),
    ("claim: 2 *.py files under pkg", "files", "pkg", ""),
    ("  - claim: 4 gates in pkg/roster.py:STEPS", "declared", "pkg/roster.py", "STEPS"),
    ("<!-- claim: 4 gates in pkg/roster.py:STEPS -->", "declared", "pkg/roster.py", "STEPS"),
    ("# claim: 4 gates in pkg/roster.py:STEPS", "declared", "pkg/roster.py", "STEPS"),
    ("// claim: 4 gates in pkg/roster.py:STEPS", "declared", "pkg/roster.py", "STEPS"),
    ("-- claim: 4 gates in pkg/roster.py:STEPS", "declared", "pkg/roster.py", "STEPS"),
)


def test_every_marked_claim_is_recognised_with_its_target() -> None:
    """The whole table in one text, a claim a line: one process reads it all, and each claim is
    found on its own line (GOH.PYSLOW2)."""
    got = parse("\n".join(prose for prose, *_ in CLAIMED))
    want = [(n, *row[1:], None) for n, row in enumerate(CLAIMED, 1)]
    assert [(c.line, c.kind, c.target, c.name, c.malformed) for c in got] == want


# The negative control, and it is the reason the gate is narrow. Each of these is
# a NUMBER beside a PATH in prose; none is a claim, and two are the estate's real
# defects in the shape they were found in.
UNMARKED = (
    "pkg/notes.md is 3 lines.",
    "`pkg/notes.md` is 3 lines.",
    "claims 4 gates in tests/e2e/verify.py for the roadmap",
    "`4` widgets in `pkg`",
    "v2.10.0 of `pkg/roster.py`",
    "see #48-what-90-can-actually-do in `pkg`",
    "the docs claim: 4 gates in pkg/roster.py",
    "a claim: 4 gates in pkg/roster.py",
    "# the docs claim: 4 gates in pkg/roster.py",
    "x = 1  # claim: 4 gates in pkg/roster.py",
    "#64 gates in pkg/roster.py",
    "4 tests were added",
    "`3` *.py lines in `pkg/notes.md`",
    "`3` lines `pkg/notes.md`",
)


def test_prose_that_is_not_a_marked_claim_produces_nothing() -> None:
    assert parse("\n".join(UNMARKED)) == []


# The closed unit vocabulary (every unit has a derivation) is a Rust test now, beside the table:
# `claims::derive::tests::every_unit_names_a_derivation_and_there_are_four`.


def test_a_marked_promise_the_gate_cannot_keep_is_a_malformed_claim_not_a_skip() -> None:
    """The asymmetry is deliberate and load-bearing: the `claim:` form is a promise
    per line, so honouring it strictly costs its author nothing, while the
    backtick form is opportunistic and must stay silent to stay usable."""
    got = parse("claim: 3 files under pkg")
    assert len(got) == 1 and got[0].malformed and "must name the extension" in got[0].malformed
    assert parse("`3` files under `pkg`") == []
    globbed = parse("`3` *.py gates in `pkg`")
    assert globbed == [], "a glob belongs to a `files` claim and nowhere else"


# ── red before, green after, one case per unit ───────────────────────────────


TRUE_AND_FALSE = (
    ("claim: 3 lines in pkg/notes.md", 3),
    # `wc -l` reads a file with no trailing newline one line SHORT: 2 is the case that decides which
    # command the finding carries (below).
    ("claim: 2 lines in no_newline.txt", 2),
    # `re.findall(r'"(P\\d+ [a-z_0-9]+)"')` sees 2 of these 4 labels (below).
    ("claim: 4 gates in pkg/roster.py:STEPS", 4),
    ("claim: 4 tests in tests/test_thing.py", 4),
    ("claim: 5 tests in tests", 5),
    ("claim: 2 *.py files under pkg", 2),
    ("`3` lines in `pkg/notes.md`", 3),
)
UNANSWERABLE = (
    ("claim: 3 lines in pkg/gone.py", "names no path in this repo"),
    ("claim: 3 lines in outside", "names no path in this repo"),
    ("claim: 3 lines in pkg", "is a directory"),
    ("claim: 4 gates in pkg/roster.py", "declares 2 module-level string lists"),
    ("claim: 4 gates in pkg/roster.py:NOPE", "declares no module-level string list"),
    ("claim: 4 gates in tests", "is a directory"),
    ("claim: 4 tests in pkg/notes.md", "not a Python module"),
    ("claim: 2 files under pkg", "must name the extension"),
    ("claim: 3 *.py files under pkg/roster.py", "is a file"),
    ("claim: 3 tests in junk/broken.py", "does not parse as Python"),
    # `verify.py` assigns `steps` TWICE -- the literal table, then the `--tier` filter's
    # comprehension. Selecting by TYPE rather than by being the first `steps` assignment is the
    # whole difference between 70 and nothing, and picking the wrong one yields an empty count.
    (
        "claim: 4 gates in pkg/roster.py:steps",
        "declares no module-level string list named 'steps'",
    ),
)


def _lines(root: Path, proses: list[str]) -> list[tuple]:
    """The findings of one pass over ROOT's one doc, whose line N+1 is the Nth of PROSES."""
    doc(root, "\n".join(proses))
    got = findings(root)
    assert (got.claims, got.stale, got.problems) == (len(proses), [], []), got
    assert {f[0] for f in got.findings} <= {"docs/note.md"}, got.findings
    return got.findings


def test_a_true_claim_is_silent_and_the_same_claim_with_another_number_is_red(repo) -> None:
    """Both directions on the SAME derivation, every unit, in one tree and two passes. The failure
    mode is symmetric and invisible: a rule matching everything is green on both, and a regex that
    stopped matching is red on both. The printed command is EXECUTED: one that disagrees with the
    gate is worse than none, because it looks checked."""
    root = estate(repo)
    assert _lines(root, [prose for prose, _ in TRUE_AND_FALSE]) == []
    false = [re.sub(r"\d+", "99", prose, count=1) for prose, _ in TRUE_AND_FALSE]
    red = _lines(root, false)
    assert [(f[1].line, f[1].number, f[2], f[5]) for f in red] == [
        (n, 99, "STALE", true) for n, (_, true) in enumerate(TRUE_AND_FALSE, 3)
    ]
    for _file, _claim, _kind, _message, command, value in red:
        assert run_command(command, str(root)) == str(value), command
    assert red[1][4] == "awk 'END{print NR}' no_newline.txt", red[1]
    # the regex `roadmap_state.py` counted with, over the list the gate counted 4 in
    assert len(re.findall(r'"(P\d+ [a-z_0-9]+)"', ROSTER)) == 2


def test_a_marked_claim_the_tree_cannot_answer_is_a_finding_named_for_why(repo) -> None:
    """A claim nobody can re-derive is a number nobody is checking. Reporting
    nothing about it would be the worst answer available: it reads as verified."""
    found = _lines(estate(repo), [prose for prose, _ in UNANSWERABLE])
    assert [(f[1].line, f[2], f[4], f[5]) for f in found] == [
        (n, "UNRESOLVED", None, None) for n in range(3, 3 + len(UNANSWERABLE))
    ]
    for (prose, why), f in zip(UNANSWERABLE, found, strict=True):
        assert why in f[3], (prose, f[3])


def test_a_derived_claim_names_the_structure_its_command_measures(repo) -> None:
    """A claim that named no structure is resolved by there being exactly ONE
    candidate -- forced, not guessed. The printed command must then name the same
    structure the count came from, or it prints something else, and "here is the
    command" is the half of the finding that makes it checkable."""
    root = estate(repo)
    write(root, "solo.py", 'ROSTER = [\n    "a",\n    "b",\n    "c",\n]\n')
    stage(root, "solo.py")
    commit_all(root, "one declared list")
    value, kind, command = numbers(root, "claim: 9 gates in solo.py")
    assert (kind, value) == ("STALE", 3), (kind, value)
    assert command.endswith("solo.py ROSTER"), command
    assert run_command(command, str(root)) == "3"


def test_a_list_holding_a_non_literal_is_not_a_declared_list_and_does_not_crash(repo) -> None:
    """`X = [name, "b"]` beside the list the claim names. `_declared_lists` read `.value` off every
    element, so a `Name` element raised AttributeError and the whole gate died with a traceback --
    over a file whose only fault was holding an ordinary list (found porting it, Phase N1)."""
    write(repo, "pkg/mixed.py", 'name = "a"\nX = [name, "b"]\nD = {**{}, "k": 1}\nY = ["c"]\n')
    stage(repo, "pkg/mixed.py")
    assert not findings(repo, "claim: 1 gates in pkg/mixed.py:Y").findings
    found = findings(repo, "claim: 2 gates in pkg/mixed.py:X").findings
    assert len(found) == 1 and "named 'X'" in found[0][3], found


def test_a_claim_after_a_multi_line_string_on_its_closing_line_is_read(repo) -> None:
    """`_python_prose` blanked a multi-line literal's END row whole -- `range(row, end_row)` reached
    it -- so prose after the closing quotes on that line vanished (found porting it, Phase N1)."""
    estate(repo)
    write(repo, "pkg/end.py", 'X = """\ntext\n"""  # `9` gates in `pkg/roster.py:STEPS`\n')
    stage(repo, "pkg/end.py")
    found = [f for f in findings(repo).findings if f[0] == "pkg/end.py"]
    assert len(found) == 1 and found[0][2] == "STALE", found


# ── what a hook actually reads: the exit code ────────────────────────────────


def test_the_exit_code_follows_the_finding(repo) -> None:
    proc = run_goh(doc(estate(repo), "claim: 3 lines in pkg/notes.md"), "claim-derivation")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_finding_names_the_file_the_line_and_the_command(repo) -> None:
    root = doc(estate(repo), "claim: 99 lines in pkg/notes.md")
    proc = run_goh(root, "claim-derivation")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "docs/note.md:3" in out, out
    assert "claims 99, the tree says 3" in out, out
    assert "re-derive: awk 'END{print NR}' pkg/notes.md" in out, out
    proc = run_goh(root, "claim-derivation", "--json")  # the same verdict
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["findings"][0]["file"] == "docs/note.md", payload
    assert payload["claims"] == 1 and payload["examined"] >= 7, payload


# ── this repository's own claims ─────────────────────────────────────────────


def test_this_repos_own_marked_claims_are_true() -> None:
    """This repository runs the gate over itself (`GOH_CLAIM_DERIVATION=1`), so every number it
    marks in prose is re-derived on every push. (The retired Python checker's `--probe` and its
    regex-narrowing calibration went with it: the native grammar is pinned by the tables above.)"""
    proc = run_goh(REPO_ROOT, "claim-derivation")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert re.search(r"marked claims? re-derived", proc.stdout), proc.stdout


def test_a_broken_python_file_is_scanned_unchanged_rather_than_skipped() -> None:
    """Untokenisable text returns unchanged -- the direction that can FIND a claim
    rather than hide one."""
    body = 'CLAIMS = [ "unterminated\nclaim: 99 lines in pkg/notes.md"\n'
    assert len(parse(body, "pkg/broken.py")) == 1
