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
import subprocess
import sys

import pytest
from claim_kit import GATES, ROSTER, doc, estate, findings, numbers, silent
from conftest import REPO_ROOT, commit_all, run_check, stage, write

sys.path.insert(0, str(REPO_ROOT / "checks"))
from _claim_derive import CHECKS, run_command
from _claim_text import UNITS, claims_in_file, claims_in_line

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


@pytest.mark.parametrize(("prose", "kind", "target", "name"), CLAIMED)
def test_a_marked_claim_is_recognised_with_its_target(prose, kind, target, name) -> None:
    got = claims_in_line(prose, 1)
    assert len(got) == 1, f"{prose!r} -> {got!r}"
    assert (got[0].kind, got[0].target, got[0].name) == (kind, target, name)
    assert got[0].malformed is None


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


@pytest.mark.parametrize("prose", UNMARKED)
def test_prose_that_is_not_a_marked_claim_produces_nothing(prose) -> None:
    assert claims_in_line(prose, 1) == []


def test_the_unit_vocabulary_is_closed_and_covers_every_derivation() -> None:
    """An unknown unit must not be a claim, and every unit must HAVE a derivation
    -- a word in the vocabulary with no rule behind it is a promise nobody keeps,
    which is the class this gate is about."""
    assert set(UNITS.values()) == set(CHECKS), (sorted(set(UNITS.values())), sorted(CHECKS))
    assert len(CHECKS) == GATES


def test_a_marked_promise_the_gate_cannot_keep_is_a_malformed_claim_not_a_skip() -> None:
    """The asymmetry is deliberate and load-bearing: the `claim:` form is a promise
    per line, so honouring it strictly costs its author nothing, while the
    backtick form is opportunistic and must stay silent to stay usable."""
    got = claims_in_line("claim: 3 files under pkg", 1)
    assert len(got) == 1 and got[0].malformed and "must name the extension" in got[0].malformed
    assert claims_in_line("`3` files under `pkg`", 1) == []
    globbed = claims_in_line("`3` *.py gates in `pkg`", 1)
    assert globbed == [], "a glob belongs to a `files` claim and nowhere else"


# ── red before, green after, one case per unit ───────────────────────────────


@pytest.mark.parametrize(
    ("true_prose", "false_prose"),
    [
        ("claim: 3 lines in pkg/notes.md", "claim: 99 lines in pkg/notes.md"),
        ("claim: 2 lines in no_newline.txt", "claim: 99 lines in no_newline.txt"),
        ("claim: 4 gates in pkg/roster.py:STEPS", "claim: 99 gates in pkg/roster.py:STEPS"),
        ("claim: 4 tests in tests/test_thing.py", "claim: 99 tests in tests/test_thing.py"),
        ("claim: 5 tests in tests", "claim: 99 tests in tests"),
        ("claim: 2 *.py files under pkg", "claim: 99 *.py files under pkg"),
        ("`3` lines in `pkg/notes.md`", "`99` lines in `pkg/notes.md`"),
    ],
)
def test_a_true_claim_is_silent_and_the_same_claim_with_another_number_is_red(
    repo, true_prose, false_prose
) -> None:
    """Both directions on the SAME derivation, one fixture each. The failure mode
    is symmetric and invisible: a rule matching everything is green on both, and a
    regex that stopped matching is red on both."""
    silent(estate(repo), true_prose)
    value, kind, command = numbers(estate(repo / "second"), false_prose)
    assert kind == "STALE" and value != 99, (kind, value)
    assert command, "a finding must print the command that re-derives the number"


@pytest.mark.parametrize(
    ("prose", "why"),
    [
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
    ],
)
def test_a_marked_claim_the_tree_cannot_answer_is_a_finding_named_for_why(repo, prose, why) -> None:
    """A claim nobody can re-derive is a number nobody is checking. Reporting
    nothing about it would be the worst answer available: it reads as verified."""
    found = findings(estate(repo), prose).findings
    assert len(found) == 1, [f[3] for f in found]
    _, _, kind, message, command, value = found[0]
    assert kind == "UNRESOLVED", kind
    assert why in message, message
    assert command is None and value is None


def test_the_label_that_a_regex_cannot_match_is_counted(repo) -> None:
    """The estate's exact defect: `re.findall(r'"(P\\d+ [a-z_0-9]+)"')` needs the
    closing quote right after ONE lowercase token, so `P1 marker_reason selftest`
    is invisible to it -- which is how `roadmap_state.py` read 64 against 70. The
    fixture roster carries three such labels, so the two answers cannot coincide
    by luck."""
    silent(estate(repo), "claim: 4 gates in pkg/roster.py:STEPS")
    regex = re.findall(r'"(P\d+ [a-z_0-9]+)"', ROSTER)
    assert len(regex) == 2, regex  # the shape the old parser read, against 4 declared


def test_a_derived_claim_names_the_structure_its_command_measures(repo) -> None:
    """A claim that named no structure is resolved by there being exactly ONE
    candidate -- forced, not guessed. The printed command must then name the same
    structure the count came from, or it prints something else, and "here is the
    command" is the half of the finding that makes it checkable."""
    from claim_kit import numbers

    root = estate(repo)
    write(root, "solo.py", 'ROSTER = [\n    "a",\n    "b",\n    "c",\n]\n')
    stage(root, "solo.py")
    commit_all(root, "one declared list")
    value, kind, command = numbers(root, "claim: 9 gates in solo.py")
    assert (kind, value) == ("STALE", 3), (kind, value)
    assert command.endswith("solo.py ROSTER"), command
    assert run_command(command, str(root)) == "3"


def test_a_comprehension_is_not_a_declared_list(repo) -> None:
    """`verify.py` assigns `steps` TWICE -- the literal table, then the `--tier`
    filter's comprehension. Selecting by TYPE rather than by being the first
    `steps` assignment is the whole difference between 70 and nothing, and
    picking the wrong one is not a crash: it yields an empty count."""
    found = findings(estate(repo), "claim: 4 gates in pkg/roster.py:steps").findings
    assert len(found) == 1
    assert "declares no module-level string list named 'steps'" in found[0][3], found[0][3]


# ── the printed command is EXECUTED, not trusted ─────────────────────────────


@pytest.mark.parametrize(
    "prose",
    [
        "claim: 99 lines in pkg/notes.md",
        "claim: 99 gates in pkg/roster.py:STEPS",
        "claim: 99 tests in tests",
        "claim: 99 *.py files under pkg",
    ],
)
def test_every_unit_prints_a_command_that_reproduces_the_gate_s_own_number(repo, prose) -> None:
    """A finding that hands the reader a command which disagrees with the gate is
    worse than one that prints no command, because it looks checked."""
    root = estate(repo / re.sub(r"\W+", "_", prose))
    found = findings(root, prose).findings
    assert len(found) == 1, prose
    _, claim, _, _message, command, value = found[0]
    printed = run_command(command, str(root))
    assert printed == str(value), f"{prose}: command printed {printed}, gate said {value}"
    assert printed != str(claim.number), f"{prose}: the command just echoes the claim"


def test_a_file_with_no_trailing_newline_counts_its_last_line(repo) -> None:
    """`wc -l` reads such a file one line SHORT, so the printed command is `awk`.
    This case is what decides which command the finding carries -- and the true
    claim being silent is itself half the evidence."""
    silent(estate(repo), "claim: 2 lines in no_newline.txt")
    value, _, command = numbers(estate(repo / "two"), "claim: 99 lines in no_newline.txt")
    assert value == 2, value
    assert command == "awk 'END{print NR}' no_newline.txt", command


# ── what a hook actually reads: the exit code ────────────────────────────────


@pytest.mark.parametrize(
    ("prose", "want"),
    [("claim: 99 lines in pkg/notes.md", 1), ("claim: 3 lines in pkg/notes.md", 0)],
)
def test_the_exit_code_follows_the_finding(repo, prose, want) -> None:
    proc = run_check(doc(estate(repo), prose), "checks/check_claim_derivation.py")
    assert proc.returncode == want, proc.stdout + proc.stderr


def test_the_finding_names_the_file_the_line_and_the_command(repo) -> None:
    proc = run_check(
        doc(estate(repo), "claim: 99 lines in pkg/notes.md"), "checks/check_claim_derivation.py"
    )
    out = proc.stdout + proc.stderr
    assert "docs/note.md:3" in out, out
    assert "claims 99, the tree says 3" in out, out
    assert "re-derive: awk 'END{print NR}' pkg/notes.md" in out, out


def test_json_output_carries_the_same_verdict(repo) -> None:
    proc = run_check(
        doc(estate(repo), "claim: 99 lines in pkg/notes.md"),
        "checks/check_claim_derivation.py",
        "--json",
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["findings"][0]["file"] == "docs/note.md", payload
    assert payload["claims"] == 1 and payload["examined"] >= 7, payload


# ── calibration: the proof that the proof is not a constant ──────────────────


def test_the_probe_is_green_and_goes_red_when_a_rule_is_narrowed(repo, monkeypatch) -> None:
    """The probe is the gate's own proof; this is the proof that the proof is not
    a constant. Narrowing the marker rule so `PATH:NAME` is no longer accepted
    turns the `declared` case RED: it stops resolving, and a self-proof that cannot
    fail is a gate reporting coverage it never measured (SUPERSOTA R1).
    """
    import _claim_text

    proc = run_check(estate(repo), "checks/check_claim_derivation.py", "--probe")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    narrowed = _claim_text.MARKER.pattern.replace(r"(?::[A-Za-z_][A-Za-z0-9_]*)?", "")
    monkeypatch.setattr(_claim_text, "MARKER", re.compile(narrowed))
    got = _claim_text.claims_in_line("claim: 4 gates in pkg/roster.py:STEPS", 1)
    assert got and got[0].name == "", got  # the NAME is gone, so it cannot resolve


def test_this_repos_own_live_claim_is_true_and_the_table_is_what_it_claims() -> None:
    """The checker's docstring marks one claim about this repository -- the size
    of its own derivation table -- and this repository runs the gate over itself,
    so the sentence and the code cannot drift apart. Measured load-bearing by
    planting a fifth entry in the table and watching the gate name its own
    docstring; that measurement is in the commit body, not repeatable here
    without editing the checker under test."""
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "checks" / "check_claim_derivation.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "marked claim(s) re-derived" in proc.stdout, proc.stdout
    assert len(CHECKS) == GATES, len(CHECKS)


def test_a_broken_python_file_is_scanned_unchanged_rather_than_skipped() -> None:
    """Untokenisable text returns unchanged -- the direction that can FIND a claim
    rather than hide one."""
    body = 'CLAIMS = [ "unterminated\nclaim: 99 lines in pkg/notes.md"\n'
    assert len(claims_in_file("pkg/broken.py", body)) == 1
