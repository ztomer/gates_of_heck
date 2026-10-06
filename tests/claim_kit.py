"""Fixture builders for the two `check_claim_derivation` suites.

A kit rather than a `conftest.py` fixture, because the two suites need the SAME
trees with different slices of them judged: `tests/test_check_claim_derivation.py`
is the gate (the grammar, the four derivations, the printed commands, the exit
codes) and `tests/test_check_claim_derivation_scope.py` is everything that decides
WHICH tree the gate looks at (the allowlist ratchet, `--staged`, `--exclude`, the
empty scope) plus the two kinds of text that are EXAMPLES rather than claims.

One definition of the fixture, because a second copy is a second fixture -- and a
fixture that disagrees with the gate is the failure this estate has already paid
for twice (SUPERSOTA R3: a check written against a fixture invented beside it).

Pure builders plus git. Every tree is staged as it is built, because the checker
reads git SCOPE: a directory on disk is not a repository it can see. The gate is the
native `goh claim-derivation` (the Python checker is retired, Phase N3): `findings`
reads its `--json` back into the verdict shape these suites assert on, and `parse`
asks the grammar alone through `--parse-claims`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from conftest import commit_all, run_goh, stage, write

# The number of derivations (`crates/goh/src/claims/derive.rs::derive`), pinned there by
# `every_unit_names_a_derivation_and_there_are_four`.
GATES = 4

# A package with two declared lists (so `declared` can be asked for one by name and
# refused for the other, which is the ambiguity the gate must refuse rather than
# resolve), tests at three depths and two kinds (so a shallow counter gets it
# wrong), a nested directory (so `files` must use git's pathspec rather than a
# top-level walk), a file with no trailing newline (so the count is demonstrably
# not `wc -l`'s), and a file Python cannot parse (so a refusal has a second cause
# to be told apart from a count of zero).
ROSTER = (
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
)
TESTS = (
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
    "def helper():\n"
    "    pass\n"
)


def estate(repo: Path) -> Path:
    """The fixture tree, written, staged and committed."""
    write(repo, "pkg/roster.py", ROSTER)
    write(repo, "pkg/__init__.py", "")
    write(repo, "pkg/notes.md", "# notes\n\nA file about the package.\n")
    write(repo, "tests/test_thing.py", TESTS)
    write(repo, "tests/nested/test_deep.py", "def test_deep():\n    pass\n")
    write(repo, "no_newline.txt", "alpha\nbeta")
    # A directory of its own, so the file Python cannot parse cannot move the
    # `files` count under pkg.
    write(repo, "junk/broken.py", "def oops(:\n    pass\n")
    stage(repo, "pkg/roster.py", "pkg/__init__.py", "pkg/notes.md", "tests/test_thing.py")
    stage(repo, "tests/nested/test_deep.py", "no_newline.txt", "junk/broken.py")
    commit_all(repo, "estate")
    return repo


def doc(repo: Path, body: str, name: str = "docs/note.md") -> Path:
    """A markdown file carrying `body` on its own line, staged and committed."""
    write(repo, name, f"# note\n\n{body}\n")
    stage(repo, name)
    commit_all(repo, "note")
    return repo


def verdict(repo: Path, *args: str) -> SimpleNamespace:
    """One `goh claim-derivation --json` pass over `repo`, as the verdict the suites read:
    `findings` are `(file, claim, kind, detail, command, value)` with `claim.line` and
    `claim.number`; `stale`, `problems`, `notes`, `claims`, `examined`, `excused`, `debt`."""
    r = run_goh(repo, "claim-derivation", "--root", str(repo), "--json", *args)
    if r.returncode == 2 or not r.stdout.strip():
        raise AssertionError(f"claim-derivation refused (exit {r.returncode}): {r.stderr}")
    doc_ = json.loads(r.stdout)
    rows = [
        (
            f["file"],
            SimpleNamespace(line=f["line"], number=f["number"]),
            f["kind"],
            f["detail"],
            f["command"],
            f["value"],
        )
        for f in doc_["findings"]
    ]
    return SimpleNamespace(
        findings=rows,
        stale=doc_["stale_allow_entries"],
        problems=doc_["problems"],
        notes=doc_["notes"],
        claims=doc_["claims"],
        examined=doc_["examined"],
        excused=doc_["allowlisted"],
        debt=doc_["unreviewed"],
        code=r.returncode,
    )


def findings(repo: Path, body: str | None = None) -> SimpleNamespace:
    """One pass over `repo`, optionally with `body` written into it."""
    if body is not None:
        doc(repo, body)
    return verdict(repo)


def parse(text: str, rel: str = "docs/x.md") -> list[SimpleNamespace]:
    """The claims the grammar reads in `text` as file `rel` -- no tree is asked."""
    r = run_goh(Path.cwd(), "claim-derivation", "--parse-claims", rel, input=text)
    assert r.returncode == 0, r.stderr
    return [SimpleNamespace(**c) for c in json.loads(r.stdout)]


def run_command(command: str, cwd: str) -> str:
    """Run a finding's printed re-derivation and return its stdout: the command is EXECUTED,
    never trusted."""
    out = subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True)
    return out.stdout.strip()


def numbers(repo: Path, prose: str) -> tuple[int, str, str]:
    """(value, kind, command) for a claim that IS reported -- used on the RED
    fixtures, where the absence of a finding would be the bug."""
    found = findings(repo, prose).findings
    if len(found) != 1:
        raise AssertionError(f"{prose!r} produced {[f[3] for f in found]!r}, wanted one finding")
    _, _, kind, _message, command, value = found[0]
    return value, kind, command


def silent(repo: Path, prose: str) -> None:
    """No finding at all -- the GREEN direction, asserted as an absence rather
    than as a count, because a checker that reports zero because it matched
    nothing and one that reports zero because the tree agrees print the same
    thing."""
    verdict = findings(repo, prose)
    if (verdict.findings, verdict.stale, verdict.problems) != ([], [], []):
        raise AssertionError(f"{prose!r} reported {[f[3] for f in verdict.findings]!r}")
    if verdict.claims != 1:
        raise AssertionError(f"{prose!r} was not even read: {verdict.claims} claim(s) in the tree")


def allow(repo: Path, claim: str, path: str = "docs/note.md", **extra) -> None:
    """`claim_derivation_allow.json` with ONE entry, committed."""
    write(repo, "claim_derivation_allow.json", "")
    stage(repo, "claim_derivation_allow.json")
    commit_all(repo, "allow")
    entry = {"path": path, "claim": claim, "status": "unreviewed", "reason": "fixture"}
    entry.update(extra)
    write(repo, "claim_derivation_allow.json", json.dumps({"entries": [entry]}, indent=2) + "\n")
    stage(repo, "claim_derivation_allow.json")
    commit_all(repo, "allow entry")


def allow_raw(repo: Path, payload: str) -> None:
    """A committed `claim_derivation_allow.json` holding the given text, verbatim --
    for the shapes the gate must REFUSE to read rather than apply nothing."""
    write(repo, "claim_derivation_allow.json", payload)
    stage(repo, "claim_derivation_allow.json")
    commit_all(repo, "allowlist payload")
