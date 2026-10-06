"""Prose text -> the DERIVABLE CLAIMS it makes. Pure: no I/O, no git, no repo.

THE CLASS. Three independent adversarial reviews of the games estate converged on
one finding: **the prose is written one step ahead of the mechanism, in files where
the prose is far more convincing than the mechanism is load-bearing.** Every
instance was found by hand, at hours each, and every one is a NUMBER that stopped
being true while the sentence around it kept reading perfectly:

    `roadmap_state.py`     "64 declared gates"   -> verify.py declares 70
    `gate.sh`              "the same 26 gates"   -> verify_gate_names() returns 29
    `check_mcp_server.py`  "477 lines, 18 tests" -> 321 lines, 20 tests
    `README.md`            "86 files, 1059 tests"-> 101 files, 1732 tests
    `.coverage-floor.json` "No floor is recorded"-> floors["unit+app"] = 94.95

A number in prose cannot watch itself, so the only mechanical defence is to
re-derive it. This module is the half that decides WHAT is a claim; `_claim_derive`
is the half that answers what it is.

## WHY THE CLAIM FORM IS EXPLICIT, and the looser rule that was rejected

The obvious rule is "any number in a sentence that also names a path". It was
rejected because it is English interpretation, and a gate that interprets English
is wrong in a direction nobody notices:

* It cannot tell an inventory from an INCIDENT. `rooms.py is 477 lines and took
  three days` names a file and a number and means neither of them.
* It cannot tell a CURRENT claim from a QUOTED one. The estate's own findings
  documents quote the false sentences they are reporting; a loose rule makes every
  finding a finding about itself.
* It cannot tell a claim from a VERSION. `v2.10.0`, `#48-what-90-can` and
  `1.79.3` are numbers beside paths.
* And when it is wrong it is wrong LOUDLY, which is the expensive direction: a
  false positive trains people to switch a gate off, and this repo's own
  `check_md_links.py` documents that lesson in its header -- it deliberately does
  not read fenced blocks or code spans because "a checker that reads them invents
  findings nobody can fix without deleting the example".

So a claim must be MARKED, and there are exactly two spellings, both of which the
estate already reads without being told:

* **the backtick form** -- the number AND the thing it counts are both in
  backticks, because in this estate backticks mean "this is the evidence"
* **the marker form** -- the line carries an explicit `claim:` marker, which is a
  promise rather than a convention, so it also works in a file with no backtick
  habit at all

Both are shown, spelled out, in the fenced block under THE GRAMMAR below.

Everything else is prose, and prose is not this gate's business. That is a real
limitation and it is stated in the checker docstring: the estate's own defects --
`"477 lines and its 18 characterization tests"` -- are BARE numbers, so this gate
would not have caught any of them as they stood. What it does instead is make the
MARKED claim a gate, so the first edit after a defect is the one that re-derives
the number rather than the one that retypes it.

## THE GRAMMAR

    ```
    <N> [ GLOB ] UNIT PREP TARGET

    `70` gates in `tests/e2e/verify.py`
    claim: 70 gates in tests/e2e/verify.py
    claim: 86 *.py files under gaf
    claim: 64 lines in tools/check_mcp_server.py
    claim: 20 tests in tests/test_mcp_server.py

    N       digits. Bare after `claim:`, backticked in the backtick form.
    GLOB    `*.EXT`, and ONLY for the `files` unit -- an extension is what makes
            the count derivable, and "86 files" does not say of what.
    UNIT    one of a CLOSED vocabulary, mapped to a derivation in
            `_claim_derive.CHECKS`: `lines`, `tests`, `declared` (spelled
            `gates`, `steps` or `checks`) and `files`.
    PREP    `in` or `under`.
    TARGET  a repo-relative path, optionally `PATH:NAME` to pick one named
            declaring structure out of a file that has several.
    ```

One claim per line, backtick form first. Nothing here looks at the words around
the claim, at the sentence it sits in, or at whether the number is plausible.
"""

from __future__ import annotations

import ast
import io
import os
import re
import sys
import tokenize
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _md_text import strip_fences

# Which derivation each unit word selects. A closed vocabulary is the point: an
# unknown word is not a claim this gate can keep true, and silently skipping it
# would leave the author believing a number is policed when nothing is.
UNITS = {
    "line": "lines",
    "lines": "lines",
    "test": "tests",
    "tests": "tests",
    "gate": "declared",
    "gates": "declared",
    "step": "declared",
    "steps": "declared",
    "check": "declared",
    "checks": "declared",
    "file": "files",
    "files": "files",
}

_UNIT_WORDS = "|".join(sorted(UNITS, key=len, reverse=True))

# A target: a repo-relative path, optionally `:NAME` selecting one declared
# structure inside it. The character class is closed on purpose -- a claim whose
# target runs into prose ("`README.md` and its docs") is not a claim.
_TARGET = r"[A-Za-z0-9_][A-Za-z0-9_./+-]*(?::[A-Za-z_][A-Za-z0-9_]*)?"
_GLOB = r"\*\.[A-Za-z0-9_+*?-]+"
_MIDDLE = rf"(?:(?P<glob>{_GLOB})\s+)?(?P<unit>{_UNIT_WORDS})\s+(?:in|under)\s+"

# The BACKTICK form: both the number and the target in backticks, because in this
# estate a backtick is the marker for "this is the evidence".
BACKTICK = re.compile(rf"`(?P<num>\d+)`\s+{_MIDDLE}`(?P<target>{_TARGET})`")

# The MARKER form. `claim:` is a promise, so the number and the target may be bare
# -- which is what lets the form work in a shell comment or a `.gatesrc` line.
#
# It must be the FIRST token of the line, after at most a list bullet or a COMMENT
# OPENER -- because a claim is most often written where prose is commented, and
# `# claim: 4 gates in verify.py` in a `.gatesrc` is the shape this form exists
# for. `\bclaim:` alone accepted "the docs claim: 4 gates in pkg/roster.py", which
# is prose ABOUT a claim -- the same shape `check_md_links.py` refuses when a
# document explains markdown link syntax, and a checker that reads it invents a
# finding nobody can fix without deleting the sentence. A marker you cannot see is
# not a marker, and the prefix set below is CLOSED for the same reason the unit
# vocabulary is: each is a real comment or bullet syntax in this estate, and a
# general "skip anything that looks like punctuation" rule would be a guess.
_COMMENT = r"(?:<!--|#+|//|--|;|%)[ \t]*"
_PREFIX = rf"[ \t]*(?:[-*>+][ \t]+)?(?:{_COMMENT})?"
MARKER = re.compile(
    rf"^{_PREFIX}claim:[ \t]*`?(?P<num>\d+)`?[ \t]+{_MIDDLE}`?(?P<target>{_TARGET})`?"
)

# Extensions whose INDENTED blocks are code. Only markup: in a Python file four
# spaces is an indent, and blanking indented lines there would delete the source.
MARKUP = (".md", ".markdown", ".rst")

# Text files a claim may live in. Binary and unreadable blobs are skipped by the
# caller, exactly as every other checker here does.
TEXT_SUFFIXES = MARKUP + (
    ".txt",
    ".py",
    ".sh",
    ".bash",
    ".zsh",
    ".rs",
    ".swift",
    ".kt",
    ".go",
    ".c",
    ".h",
    ".cpp",
    ".m",
    ".mm",
    ".js",
    ".ts",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".cfg",
    ".ini",
    ".gatesrc",
)


class Claim:
    """One marked claim: the number written, and what it says it counts.

    `kind` is a `_claim_derive.CHECKS` key. `glob` is set only for a `files`
    claim, and `malformed` names the one way a marked claim can fail to parse --
    an extension-less file count -- so the checker can report it rather than
    dropping a promise on the floor.
    """

    __slots__ = ("glob", "kind", "line", "malformed", "name", "number", "target", "text")

    def __init__(
        self,
        number: int,
        kind: str,
        glob: str | None,
        target: str,
        lineno: int,
        text: str,
        malformed: str | None = None,
    ) -> None:
        self.number = number
        self.kind = kind
        self.glob = glob
        target_path, sep, name = target.rpartition(":")
        # A rpartition that found no colon leaves the whole string in
        # `target_path` and an empty `name`; `os.path` separators never carry a
        # colon in a repo path, so the two cannot be confused.
        self.target = target_path if sep else target
        self.name = name if sep else ""
        self.line = lineno
        self.text = text
        self.malformed = malformed

    def where(self) -> str:
        """`path:line` for the finding, plus the claim as it was written."""
        return f"line {self.line}: {self.text.strip()}"


def _build(match: re.Match, lineno: int, text: str, promised: bool) -> Claim | None:
    """A match -> a Claim, or None when the shape is not one this gate derives.

    `promised` is True for the `claim:` form. A promise the gate cannot keep is a
    FINDING (a `malformed` claim); a convention it cannot read is not a claim at
    all, and is dropped without a word. That asymmetry is deliberate: the marker
    form is opt-in per line, so honouring it strictly costs the author nothing,
    while the backtick form is opportunistic and must stay silent to stay usable.
    """
    kind = UNITS[match.group("unit")]
    glob = match.group("glob")
    malformed = None
    if glob and kind != "files":
        # `*.py gates` is not one of the shapes; the extension belongs to a file
        # count and nowhere else.
        if not promised:
            return None
        malformed = f"an extension applies to a `files` claim only, not to a `{kind}` one"
    elif kind == "files" and not glob:
        if not promised:
            return None
        malformed = (
            "a `files` claim must name the extension it counts (`*.py`), or it is not derivable"
        )
    return Claim(
        int(match.group("num")), kind, glob, match.group("target"), lineno, text, malformed
    )


def claims_in_line(line: str, lineno: int) -> list[Claim]:
    """Every claim ONE line makes: the backtick form first, then `claim:`.

    At most one per form, so a line carrying both spellings of the same claim
    yields one finding rather than two. A line carrying two DIFFERENT marked
    claims is two lines' worth of prose on one line; the second is not read, which
    is stated here rather than left to be discovered.
    """
    out = []
    hit = BACKTICK.search(line)
    if hit:
        claim = _build(hit, lineno, line, promised=False)
        if claim:
            return [claim]
    hit = MARKER.search(line)
    if hit:
        claim = _build(hit, lineno, line, promised=True)
        if claim:
            out.append(claim)
    return out


def _python_prose(text: str) -> str:
    """Blank every Python string literal that is not a DOCSTRING, keeping line
    structure and columns.

    A claim is prose, and a string literal in code is DATA the program supplies --
    which is exactly where a checker's own fixture lives. Without this the gate
    cannot express its test cases: `checks/_claim_probe.py`'s whole CLAIMS table is
    string literals, and every entry became a finding about this repository on the
    day it landed. An allowlist entry per fixture would be a worse answer, because
    a gate whose test cases must be registered as known-false claims is a gate
    nobody can add cases to.

    The cost is stated rather than hidden: a claim inside a non-docstring string
    literal is not read. Every one of the six defects this gate exists for lives
    in a docstring, a comment or a markdown file, so the class is not lost -- and
    the text of a string the program EMITS is policed where it lands, in the file
    it generates.

    Untokenisable text is returned UNCHANGED, which is the direction that can find
    a claim rather than hide one; a file Python cannot tokenize is broken anyway,
    and the file-length gate has an opinion about it.
    """
    docstrings: set[int] = set()
    try:
        # A scanned file's own invalid escapes are not this gate's report: unsuppressed, ast.parse
        # printed `SyntaxWarning: "\(" is an invalid escape sequence` into the gate's output for
        # someone else's regex (divoom-control, found porting this, Phase N1).
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return text
    for node in ast.walk(tree):
        if not (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    blanked = text.split("\n")
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type != tokenize.STRING or tok.start[0] in docstrings:
                continue
            row, col = tok.start
            end_row, end_col = tok.end
            if row == end_row:
                blanked[row - 1] = (
                    blanked[row - 1][:col] + " " * (end_col - col) + blanked[row - 1][end_col:]
                )
                continue
            blanked[row - 1] = blanked[row - 1][:col] + " " * (len(blanked[row - 1]) - col)
            # The rows strictly BETWEEN the first and the last: `range(row, end_row)` reached the
            # end row too and blanked whatever followed the closing quotes on it.
            for middle in range(row, end_row - 1):
                blanked[middle] = " " * len(blanked[middle])
            blanked[end_row - 1] = " " * end_col + blanked[end_row - 1][end_col:]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    return "\n".join(blanked)


def scannable(rel: str) -> bool:
    """Is this repo-relative path a file a claim may live in?"""
    if os.path.basename(rel) == ".gatesrc":
        return True
    return os.path.splitext(rel)[1].lower() in TEXT_SUFFIXES


def claims_in_file(rel: str, text: str) -> list[Claim]:
    """Every claim in one file, in line order.

    TWO kinds of text are EXAMPLES rather than claims, and both were measured
    rather than assumed:

    * **A fenced code block, in every text file.** That is where documentation
      about THIS gate writes its own grammar, and a gate that reads its own
      documentation is a gate that reports itself. Indented blocks count too, in
      markup only -- in Python an indent is code.
    * **A non-docstring string literal in Python.** See `_python_prose`.

    Inline code spans are NOT skipped, and cannot be: a backtick is this gate's
    own marker, so an inline span carrying one is a claim. That is why the grammar
    is documented with `<N>` placeholders inside fenced blocks.
    """
    suffix = os.path.splitext(rel)[1].lower()
    body = strip_fences(text, indented=suffix in MARKUP)
    if suffix == ".py":
        body = _python_prose(body)
    out = []
    for number, line in enumerate(body.split("\n"), 1):
        if line:
            out.extend(claims_in_line(line, number))
    return out
