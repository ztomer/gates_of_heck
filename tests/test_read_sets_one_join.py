"""Reads are joined in one place (GOH.READUNION): lib/read_sets.py, and nowhere else.

Apart from tests/test_read_sets.py, whose own reads stay few: this one reads every module.
"""

from __future__ import annotations

import re

from conftest import REPO_ROOT

TREE_READS = ("lib/*.py", "tools/*.py", "gates/*.py", "checks/*.py", "scripts/*.py", "tests/*.py")


def test_every_join_of_reads_is_the_one_join() -> None:
    """A second join is how the list was truncated in three places: no module but lib/read_sets.py
    reads or writes a record's `reads`."""
    raw = re.compile(r"""\[["']reads["']\]|\.get\(["']reads["']|["']reads["']:|\breads\s*\|""")
    mine = {"lib/read_sets.py", "tests/_read_audit.py", "tests/_land_verdicts.py"}
    other = {"scripts/claude_cache_probe.py"}  # a probe's own count, no record
    found = sorted(
        str(p.relative_to(REPO_ROOT))
        for d in ("lib", "tools", "gates", "checks", "scripts", "tests")
        for p in (REPO_ROOT / d).glob("*.py")
        if not p.name.startswith("test_") and raw.search(p.read_text(encoding="utf-8"))
    )
    assert set(found) - mine - other == set(), found
    for rel in ("tests/_read_audit.py", "tests/_land_verdicts.py"):  # the plan's parts: a list
        assert not re.search(r"""\.get\(["']reads["']\)|\[["']reads["']\]\[|["']reads["'], \{""",
                             (REPO_ROOT / rel).read_text(encoding="utf-8")), rel  # fmt: skip
