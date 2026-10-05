"""`check_claim_derivation.py` -- WHICH TREE it looks at, and what is not a claim.

The gate's grammar and its four derivations are in
`tests/test_check_claim_derivation.py`. This is the half that answers a question a
gate can be wrong about with its rules perfectly correct:

* **The allowlist is a RATCHET.** An entry excuses one claim; an entry matching
  nothing is STALE and fails, because a claim that got fixed must take its
  exemption with it. A list that can only grow is permission, and the estate has
  two of those to remember.
* **`--staged` judges the INDEX**, for the claim text AND the number it is measured
  against. Reading either from the worktree lets an unstaged edit decide a commit's
  verdict -- and lets a file that is not in the commit be measured as though it were.
* **An empty scope is a REFUSAL at --full and a named non-run at --staged.** A gate
  reporting "0 files clean" over zero files has reported compliance over nothing,
  the defect this estate has found in three repos and in two of its own gates.
* **Two kinds of text are EXAMPLES**: a fenced block, and in Python a non-docstring
  string literal. The second is where a checker's own fixture lives, and a gate that
  cannot express its test cases cannot be tested -- on the day this landed, the
  probe's own CLAIMS table was 35 findings about this repository.

Trees come from `tests/claim_kit.py`, the same ones the gate suite judges.
"""

from __future__ import annotations

import json
import sys

import pytest
from claim_kit import allow, allow_raw, doc, estate, findings, write
from conftest import REPO_ROOT, commit_all, run_check, stage

sys.path.insert(0, str(REPO_ROOT / "checks"))
from _claim_allow import load as load_allowlist
from check_claim_derivation import adopted, check, main

# ── the allowlist ratchet ────────────────────────────────────────────────────


def test_an_allowlisted_claim_is_excused_and_counts_as_debt(repo) -> None:
    """`status: unreviewed` is DEBT, not a decision, and every run reports the count
    so a list of debt never reads as a clean list."""
    root = estate(repo)
    prose = "claim: 99 lines in pkg/notes.md"
    doc(root, prose)
    allow(root, prose)
    verdict = check(str(root), None, False)
    assert (verdict.findings, verdict.stale, verdict.problems) == ([], [], []), verdict
    assert (verdict.excused, verdict.debt) == (1, 1), verdict


def test_a_decided_entry_is_excused_without_being_debt(repo) -> None:
    root = estate(repo)
    prose = "claim: 99 lines in pkg/notes.md"
    doc(root, prose)
    allow(root, prose, status="legitimate", reason="the sentence is true until the split")
    verdict = check(str(root), None, False)
    assert verdict.excused == 1 and verdict.debt == 0, verdict


def test_an_entry_whose_claim_moved_is_stale_and_fails(repo) -> None:
    """The half that keeps the file from becoming permission: a claim that got
    FIXED must take its exemption with it."""
    root = estate(repo)
    allow(root, "claim: 99 lines in pkg/notes.md")
    doc(root, "claim: 3 lines in pkg/notes.md")
    verdict = check(str(root), None, False)
    assert verdict.findings == [] and verdict.problems == [] and verdict.excused == 0
    assert len(verdict.stale) == 1
    assert verdict.stale[0]["claim"] == "claim: 99 lines in pkg/notes.md"


def test_a_duplicate_entry_is_stale_because_the_first_takes_every_match(repo) -> None:
    root = estate(repo)
    prose = "claim: 99 lines in pkg/notes.md"
    doc(root, prose)
    write(root, "claim_derivation_allow.json", "")
    stage(root, "claim_derivation_allow.json")
    commit_all(root, "allow")
    entry = {"path": "docs/note.md", "claim": prose, "reason": "fixture"}
    write(
        root,
        "claim_derivation_allow.json",
        json.dumps({"entries": [entry, dict(entry)]}, indent=2) + "\n",
    )
    stage(root, "claim_derivation_allow.json")
    commit_all(root, "two entries")
    verdict = check(str(root), None, False)
    assert verdict.findings == [] and verdict.excused == 1 and len(verdict.stale) == 1, verdict


def test_whitespace_is_not_what_an_entry_is_matched_on(repo) -> None:
    """`ruff format` must not be able to revoke an exemption -- the reason
    `check_no_kill_by_name` collapses whitespace, and the same reason here."""
    root = estate(repo)
    prose = "claim: 99 lines in pkg/notes.md"
    doc(root, prose)
    allow(root, "claim:   99   lines   in   pkg/notes.md")
    verdict = check(str(root), None, False)
    assert (verdict.findings, verdict.stale, verdict.problems, verdict.excused) == (
        [],
        [],
        [],
        1,
    ), verdict


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",
        '{"entries": {}}',
        json.dumps({"entries": [{"path": "a.md"}]}),
        json.dumps({"entries": [{"path": "a.md", "claim": "c", "reason": "r", "status": "maybe"}]}),
    ],
)
def test_an_allowlist_this_gate_cannot_read_is_a_problem_not_a_pass(repo, payload) -> None:
    """An exemption list this gate could not read is an exemption list silently
    applying nothing -- which reads as a clean tree."""
    root = estate(repo)
    allow_raw(root, payload)
    verdict = check(str(root), None, False)
    assert verdict.problems and not verdict.findings and not verdict.stale, verdict


def test_an_absent_allowlist_is_not_a_problem(repo) -> None:
    verdict = findings(estate(repo))
    assert (verdict.problems, verdict.stale, verdict.excused, verdict.debt) == ([], [], 0, 0)
    assert load_allowlist(str(estate(repo / "second")), False) == ([], [])


# ── which tree: the git view ─────────────────────────────────────────────────


def test_staged_judges_the_index_and_the_worktree_is_not_the_tree(repo) -> None:
    """The claim text AND the number it is measured against both come from the
    index at --staged. Reading either from the worktree lets an unstaged edit
    decide a commit's verdict -- which is the bug `_gitutil.content_bytes`'s
    fallback would otherwise cause here."""
    root = estate(repo)
    write(root, "pkg/notes.md", "# notes\n\nA file about the package.\nAnd a fourth line.\n")
    write(root, "docs/note.md", "# note\n\nclaim: 3 lines in pkg/notes.md\n")
    stage(root, "docs/note.md")
    commit_all(root, "the index holds the claim, the worktree grew the file")
    assert check(str(root), None, True).findings == []  # 3 lines in the INDEX
    assert len(check(str(root), None, False).findings) == 1  # 4 in the worktree


def test_staged_refuses_a_target_that_is_not_in_the_commit(repo) -> None:
    root = estate(repo)
    write(root, "pkg/new.py", "x = 1\n")  # deliberately NOT added
    write(root, "docs/note.md", "# note\n\nclaim: 1 lines in pkg/new.py\n")
    # Staged and NOT committed: `listed_files(staged=True)` reads the diff, so a
    # committed fixture would put nothing in scope and the gate would be answering
    # a different question than the one under test.
    stage(root, "docs/note.md")
    found = check(str(root), None, True).findings
    assert len(found) == 1 and "not in the index" in found[0][3], found
    assert check(str(root), None, False).findings == []  # the worktree knows the file


def test_nothing_staged_is_a_named_non_run_and_nothing_tracked_is_a_refusal(repo) -> None:
    """Two different facts that print the same sentence otherwise. Nothing staged is
    a fact about a commit; nothing tracked is a fact about the GATE."""
    root = estate(repo)
    empty = check(str(root), None, True)
    assert empty.problems == [] and empty.findings == [], empty
    assert "nothing staged" in " ".join(empty.notes), empty
    proc = run_check(root, "checks/check_claim_derivation.py", "--exclude", ".")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "refusing to report clean over zero files" in proc.stdout + proc.stderr


# The spellings that mean "opted in", pinned against the shell's
# `[ -n "${GOH_CLAIM_DERIVATION:-}" ]` after sourcing `.gatesrc`. `=0` is in this
# list on purpose: the house test is PRESENCE, not truth, so `=0` opts in -- the
# same trap `gatesrc::an_opt_in_is_present_and_non_empty_and_nothing_else_is`
# pins on the Rust side, and the two tiers must agree or the pipeline and the
# binary run different gates.
ADOPTED = (
    "GOH_MAX_LINES=500\nGOH_CLAIM_DERIVATION=1\n",
    "GOH_CLAIM_DERIVATION='1'\n",
    'GOH_CLAIM_DERIVATION="1"\n',
    "GOH_CLAIM_DERIVATION=1  # a trailing comment\n",
    "GOH_CLAIM_DERIVATION='1'  # a comment after the value\n",
    "export GOH_CLAIM_DERIVATION=1\n",
    "GOH_CLAIM_DERIVATION=0\n",
)
NOT_ADOPTED = (
    "GOH_MAX_LINES=500\n",
    "",
    "GOH_CLAIM_DERIVATION=\nGOH_MAX_LINES=500\n",
    "# GOH_CLAIM_DERIVATION=1\nGOH_MAX_LINES=500\n",
    "GOH_CLAIM_DERIVATIONS=1\n",
)


@pytest.mark.parametrize("gatesrc", ADOPTED)
def test_an_opted_in_tree_with_no_marked_claim_refuses(repo, gatesrc) -> None:
    """The empty-scope rule, and why the checker reads the repo's own `.gatesrc`:
    `check_empty_scope.py` builds a skeleton with no content and the REAL
    `.gatesrc`, so there "opted in with nothing marked" is a gate inspecting
    nothing -- and a gate that cannot say so reports compliance over an empty
    population, the defect this estate has found in three repos and in two of its
    own gates."""
    root = estate(repo)
    write(root, ".gatesrc", gatesrc)
    stage(root, ".gatesrc")
    commit_all(root, "opted in")
    verdict = check(str(root), None, False)
    assert verdict.problems and "adopted" in verdict.problems[0], verdict
    proc = run_check(root, "checks/check_claim_derivation.py")
    assert proc.returncode == 1, proc.stdout + proc.stderr


@pytest.mark.parametrize("gatesrc", NOT_ADOPTED)
def test_a_repo_that_has_not_adopted_the_convention_is_reported_not_failed(repo, gatesrc) -> None:
    """A repo that has not adopted the convention has no marked claims, and that is
    a fact about the repo rather than a fault in the gate -- so the run is green and
    says which key turns the convention on."""
    root = estate(repo)
    doc(root, "no marked claim here, and none is expected\n")
    write(root, ".gatesrc", gatesrc)
    stage(root, ".gatesrc")
    commit_all(root, "not adopted")
    verdict = check(str(root), None, False)
    assert verdict.problems == [] and verdict.findings == [], verdict
    proc = run_check(root, "checks/check_claim_derivation.py")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_this_repos_own_gatesrc_is_read_as_adopted() -> None:
    """The real file, with its `export`, its quotes and its comments -- a parser
    that only works on a fixture is a parser that has never met a `.gatesrc`."""
    body = (REPO_ROOT / ".gatesrc").read_text(encoding="utf-8")
    assert "GOH_CLAIM_DERIVATION" in body, body
    assert adopted(str(REPO_ROOT), False) is True


def test_an_unparsable_exclude_is_a_usage_error_not_a_wide_scan(repo) -> None:
    """Exempting the whole tree while still claiming to have examined it is the
    worse of the two answers, so it is exit 2 and not a pass."""
    root = estate(repo)
    doc(root, "claim: 99 lines in pkg/notes.md")
    with pytest.raises(ValueError, match="bad --exclude"):
        check(str(root), "[unclosed", False)
    assert main(["--root", str(root), "--exclude", "[unclosed"]) == 2


# ── what is an EXAMPLE, and what is data ─────────────────────────────────────


def test_a_fenced_block_is_an_example_in_markup_and_in_python(repo) -> None:
    """This gate's own docstring, and the docstring of its grammar module, are
    written in fenced blocks precisely so they are not findings about this
    repository. Both directions are here because the failure is symmetric."""
    root = estate(repo)
    write(root, "docs/guide.md", "# g\n\n```\nclaim: 99 lines in pkg/notes.md\n```\n")
    write(root, "pkg/notes.py", '"""Note.\n\n```\nclaim: 99 lines in pkg/notes.md\n```\n"""\n')
    stage(root, "docs/guide.md", "pkg/notes.py")
    commit_all(root, "examples")
    assert check(str(root), None, False).findings == []


def test_an_indented_block_is_an_example_in_markup_only(repo) -> None:
    """Four spaces are a code block in markdown and an INDENT in Python -- so the
    same rule cannot be applied to both, and applying it to Python would delete
    the file rather than mask an example."""
    root = estate(repo)
    write(root, "docs/guide.md", "# g\n\n    claim: 99 lines in pkg/notes.md\n")
    stage(root, "docs/guide.md")
    commit_all(root, "indented example")
    assert check(str(root), None, False).findings == []
    write(root, "pkg/deep.py", "def f():\n    # claim: 99 lines in pkg/notes.md\n    pass\n")
    stage(root, "pkg/deep.py")
    commit_all(root, "indented in python")
    found = check(str(root), None, False).findings
    assert len(found) == 1 and found[0][0] == "pkg/deep.py", [f[3] for f in found]


def test_a_non_docstring_string_literal_is_data_not_prose(repo) -> None:
    """Where a checker's own fixture lives. Without this the gate cannot express
    its test cases, and every one of them is a finding in this repo."""
    root = estate(repo)
    write(root, "pkg/cases.py", 'CASES = ("claim: 99 lines in pkg/notes.md",)\n')
    stage(root, "pkg/cases.py")
    commit_all(root, "fixture data")
    assert check(str(root), None, False).findings == []
    # ...and the same text on its OWN LINE inside a docstring IS a claim, which is
    # the half that makes the rule safe rather than a blanket exemption for .py.
    write(root, "pkg/cases.py", '"""Cases.\n\nclaim: 99 lines in pkg/notes.md\n"""\n')
    stage(root, "pkg/cases.py")
    commit_all(root, "moved into a docstring")
    found = check(str(root), None, False).findings
    assert len(found) == 1 and found[0][2] == "STALE", [f[3] for f in found]


def test_a_binary_file_is_not_scanned(repo) -> None:
    """Unreadable content is skipped by every checker here, and this one says so
    by never counting the file rather than by failing on it."""
    root = estate(repo)
    (root / "blob.bin").write_bytes(b"\x00\x01claim: 99 lines in pkg/notes.md\x00")
    stage(root, "blob.bin")
    commit_all(root, "binary")
    verdict = check(str(root), None, False)
    assert verdict.findings == [] and verdict.examined == 8, verdict
