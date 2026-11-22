"""The base files' table is whole and true (tests/base_files.tsv, the test-once design 2.3,
GOH.CARRYBASECODE).

The land gate judges a change to a base file by its readers only when the table classes it
`base-code` (tests/_carry_shadow.py `choose`); a base file with no row is wide. So every file of
`Closure.base()` -- what pytest loads before every test -- has exactly one row, each row names a
file that exists and is still in the base (a stale row fails), and each class is the one the rule
computes: `suite-wide` for pytest's own configuration, a conftest.py and the workflow the suite
takes its environment from (`ENTRY_READS`), `load-read` for the data the base opens as it loads.
"""

from __future__ import annotations

import sys
from pathlib import Path

import _carry_shadow as cs
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import test_closure


def _rows(root: Path) -> list[tuple[str, str]]:
    text = (root / cs.BASE_TABLE).read_text(encoding="utf-8")
    return [tuple(ln.split("\t")[:2]) for ln in text.splitlines()  # type: ignore[misc]
            if ln.strip() and not ln.startswith("#")]  # fmt: skip


def faults(root: Path, graph) -> list[str]:
    """What is wrong with ROOT's table against GRAPH: each one a line."""
    rows, base, loads = _rows(root), graph.base(), graph.load_reads()
    named = [p for p, _ in rows]
    out = [f"{p}: two rows" for p in sorted({p for p in named if named.count(p) > 1})]
    out += [f"{p}: loaded before every test, no row in {cs.BASE_TABLE}"
            for p in sorted(base - set(named))]  # fmt: skip
    for path, got in rows:
        if not (root / path).is_file():
            out.append(f"{path}: the row names a missing file")
        elif path not in base:
            out.append(f"{path}: no longer loaded before every test (a stale row)")
        wide = path in cs.SUITE_WIDE or Path(path).name == "conftest.py"
        want = cs.SUITE if wide or path in test_closure.ENTRY_READS else (
            cs.LOAD if path in loads else cs.BASE_CODE)  # fmt: skip
        if got != want:
            out.append(f"{path}: classed {got!r}, the rule says {want!r}")
    return out


def test_every_base_file_has_one_true_row() -> None:
    assert faults(REPO_ROOT, test_closure.Closure(REPO_ROOT)) == []


class _Graph:
    def __init__(self, base: set[str], loads: dict[str, str] | None = None) -> None:
        self._base, self._loads = base, loads or {}

    def base(self) -> set[str]:
        return self._base

    def load_reads(self) -> dict[str, str]:
        return self._loads


def test_a_missing_row_a_missing_file_and_a_wrong_class_each_fail(tmp_path: Path) -> None:
    """Calibrated: each defect the gate exists for turns it red."""
    (tmp_path / "tests").mkdir()
    for f in ("tests/conftest.py", "lib/a.py", "lib/data.tsv", "lib/old.py"):
        (tmp_path / f).parent.mkdir(exist_ok=True)
        (tmp_path / f).write_text("")
    (tmp_path / cs.BASE_TABLE).write_text(
        "# head\ntests/conftest.py\tbase-code\nlib/gone.py\tbase-code\nlib/data.tsv\tload-read\n"
        "lib/old.py\tbase-code\n"
    )
    graph = _Graph({"tests/conftest.py", "lib/a.py", "lib/data.tsv"}, {"lib/data.tsv": "lib/a.py"})
    assert faults(tmp_path, graph) == [
        "lib/a.py: loaded before every test, no row in tests/base_files.tsv",
        "tests/conftest.py: classed 'base-code', the rule says 'suite-wide'",
        "lib/gone.py: the row names a missing file",
        "lib/old.py: no longer loaded before every test (a stale row)",
    ]
