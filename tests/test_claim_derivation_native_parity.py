"""`goh claim-derivation` writes the Python checker's report, byte for byte (Phase N1).

The gate's own suite judges `check()` in-process. The port is judged here, through the CLI, over the
same fixture (`claim_kit.estate`): every derivation true and stale, every refusal, the allowlist
ratchet, both scopes and `--json` -- stdout, stderr and exit code compared whole. The one case the
port reads differently is stated and pinned rather than skipped: a TARGET that tokenizes nowhere
(`junk/broken.py`) is UNRESOLVED on both, with the reason in the tokenizer's words, not the parser's.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from claim_kit import allow, doc, estate
from conftest import REPO_ROOT, commit_all, stage, write

CHECK = REPO_ROOT / "checks" / "check_claim_derivation.py"

PROSE = [
    "claim: 4 gates in pkg/roster.py:STEPS",
    "claim: 9 gates in pkg/roster.py:STEPS",
    "claim: 2 gates in pkg/roster.py:FILTERS",
    "claim: 4 gates in pkg/roster.py",
    "claim: 4 gates in pkg/roster.py:steps",
    "claim: 4 gates in pkg/roster.py:NOPE",
    "claim: 3 lines in pkg/notes.md",
    "claim: 2 lines in no_newline.txt",
    "claim: 7 lines in no_newline.txt",
    "claim: 1 lines in pkg",
    "claim: 1 lines in nothere.md",
    "claim: 1 lines in ../outside.md",
    "claim: 5 tests in tests/test_thing.py",
    "claim: 6 tests in tests",
    "claim: 99 tests in tests",
    "claim: 1 tests in pkg/notes.md",
    "claim: 3 *.py files under pkg",
    "claim: 99 *.py files under pkg",
    "claim: 3 files under pkg",
    "claim: 3 *.py gates in pkg/roster.py",
    "claim: 1 *.py files under pkg/roster.py",
    "`4` gates in `pkg/roster.py:STEPS`",
    "`5` gates in `pkg/roster.py:STEPS`",
    "the docs claim: 4 gates in pkg/roster.py",
    "- claim: 4 gates in pkg/roster.py:STEPS",
    "<!-- claim: 9 gates in pkg/roster.py:STEPS -->",
    "```\nclaim: 9 gates in pkg/roster.py:STEPS\n```",
    "    claim: 9 gates in pkg/roster.py:STEPS",
]


def _run(argv: list[str], repo: Path) -> tuple[int, str, str]:
    r = subprocess.run(argv, cwd=repo, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout, r.stderr


def both(goh: Path, repo: Path, *args: str) -> tuple[tuple, tuple]:
    py = _run([sys.executable, str(CHECK), *args], repo)
    rs = _run([str(goh), "claim-derivation", *args], repo)
    return py, rs


@pytest.mark.parametrize("prose", PROSE)
@pytest.mark.parametrize("args", [[], ["--json"]], ids=["text", "json"])
def test_every_claim_shape_reads_the_same(goh: Path, repo: Path, prose: str, args) -> None:
    doc(estate(repo), prose)
    py, rs = both(goh, repo, *args)
    assert rs == py


def test_a_python_docstring_claim_is_read_and_a_string_literal_is_not(
    goh: Path, repo: Path
) -> None:
    estate(repo)
    write(
        repo,
        "pkg/doc.py",
        '"""Module.\n\nclaim: 9 gates in pkg/roster.py:STEPS\n"""\n'
        'X = """\nclaim: 9 gates in pkg/roster.py:STEPS\n"""\n'
        'def f():\n    """Doc.\n\n    claim: 8 gates in pkg/roster.py:STEPS\n    """\n'
        '    return f"""\nclaim: 7 gates in pkg/roster.py:STEPS\n{X}"""\n',
    )
    stage(repo, "pkg/doc.py")
    commit_all(repo, "doc")
    py, rs = both(goh, repo)
    assert rs == py
    # The module and function docstrings, and the f-string (not a STRING token): three findings.
    assert py[0] == 1 and py[2].count("STALE") == 3, py


@pytest.mark.parametrize("args", [[], ["--staged"]], ids=["full", "staged"])
def test_the_allowlist_ratchet_reads_the_same(goh: Path, repo: Path, args) -> None:
    doc(estate(repo), "claim: 9 gates in pkg/roster.py:STEPS")
    allow(repo, "claim: 9 gates in pkg/roster.py:STEPS")
    assert both(goh, repo, *args)[1] == both(goh, repo, *args)[0]
    allow(repo, "claim: 1 gates in pkg/roster.py:STEPS")
    py, rs = both(goh, repo, *args)
    assert rs == py


def test_staged_and_exclude_read_the_same(goh: Path, repo: Path) -> None:
    doc(estate(repo), "claim: 9 gates in pkg/roster.py:STEPS")
    write(repo, "docs/extra.md", "claim: 1 lines in pkg/notes.md\n")
    stage(repo, "docs/extra.md")
    for args in (["--staged"], ["--exclude", "^docs/"], ["--staged", "--json"]):
        py, rs = both(goh, repo, *args)
        assert rs == py, args


def test_an_untokenizable_target_is_unresolved_on_both(goh: Path, repo: Path) -> None:
    doc(estate(repo), "claim: 1 tests in junk/broken.py")
    (pc, _, perr), (rc, _, rerr) = both(goh, repo)
    assert pc == rc == 1
    assert "junk/broken.py does not parse as Python" in perr
    assert "junk/broken.py does not parse as Python" in rerr


def test_a_scanned_files_invalid_escape_is_not_printed_into_the_report(
    goh: Path, repo: Path
) -> None:
    """`ast.parse` WARNS on `"\\("`; unsuppressed, the warning about someone else's regex landed in
    this gate's output (divoom-control). Both tiers say nothing about it."""
    doc(estate(repo), "claim: 4 gates in pkg/roster.py:STEPS")
    write(repo, "pkg/rx.py", 'import re\nP = re.compile("\\(x")\n')
    stage(repo, "pkg/rx.py")
    commit_all(repo, "rx")
    py, rs = both(goh, repo)
    assert "SyntaxWarning" not in py[1] + py[2], py
    assert rs == py


def test_a_claim_after_a_multi_line_strings_closing_quotes_reads_the_same(
    goh: Path, repo: Path
) -> None:
    estate(repo)
    write(repo, "pkg/end.py", 'X = """\ntext\n"""  # `9` gates in `pkg/roster.py:STEPS`\n')
    stage(repo, "pkg/end.py")
    commit_all(repo, "end")
    py, rs = both(goh, repo)
    assert rs == py and py[0] == 1, py
