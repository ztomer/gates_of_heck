"""GOH.TABLEGEN: a table that only copies a fact the code holds is generated, never hand-kept.

Lanes learned the copy rules at land: a script with no docs/map.md row, a lock_order.tsv row naming
a file that no longer takes the lock, a stale 450-line headroom entry, a base file with no row in
tests/base_files.tsv (each a red over 2026-11-17..24). lib/tables.py writes each such table, or its
mechanical part, from the tree; this test holds the committed copy to what it writes, so the fix
for every one of those reds is one command: `python3 lib/tables.py --write`.
"""
# goh-land-guard:

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import _tree_graph
import pytest
from conftest import REPO_ROOT

from lib import map_table, tables, test_closure

MAP = """# map

## Gates (`gates/`)

| Script | Purpose | Invoked | Test |
|---|---|---|---|
| `a.sh` | A. | hooks | `test_a.py` |
| `gone.sh` | Gone. | hooks | `test_gone.py` |

## Checks (`checks/`)

| Script | Purpose | Invoked | Test |
|---|---|---|---|
| `check_x.py` | X. | gates | `test_x.py` |

## Lib (`lib/`)

| Module | Purpose | Invoked | Test |
|---|---|---|---|
| `old.py` | Old. | gates | `test_old.py` |

## Tools

prose
"""


def _tree(tmp: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp / rel).write_text(text, encoding="utf-8")
    return tmp


@pytest.mark.parametrize("table", tables.TABLES, ids=lambda t: t.path)
def test_every_generated_table_is_as_its_generator_writes_it(table: tables.Table) -> None:
    if table.path == "tests/base_files.tsv":  # the worker's one graph, not a second build of it
        graph = _tree_graph.real()
        table = tables.Table(table.name, table.path, lambda root, text: tables.base_rows(
            text, graph.base(), graph.load_reads(), test_closure.ENTRY_READS))  # fmt: skip
    found = tables.check(REPO_ROOT, (table,))
    assert found == [], "\n".join(found)


def test_a_new_script_gets_a_row_and_a_deleted_one_loses_it(tmp_path: Path) -> None:
    root = _tree(tmp_path, {
        "docs/map.md": MAP, "gates/a.sh": "#!/bin/bash\n# A.\n", "checks/check_x.py": '"""X."""\n',
        "lib/old.py": '"""Old."""\n',
        "lib/new_thing.py": '"""lib/new_thing.py -- What the new thing does.\n\nMore.\n"""\n',
        "tools/land.sh": 'python3 "$ROOT/lib/new_thing.py"\n',
        "tests/test_new_thing.py": "from lib import new_thing\n",
    })  # fmt: skip
    out = map_table.render(root, MAP)
    row = next(ln for ln in out.splitlines() if ln.startswith("| `new_thing.py`"))
    assert row == ("| `new_thing.py` | What the new thing does. | `tools/land.sh` |"
                   " `test_new_thing.py` |"), row  # fmt: skip
    assert "`gone.sh`" not in out and "`a.sh`" in out, out
    assert map_table.render(root, out) == out, "the generator is not a fixed point"


def test_a_row_whose_cells_differ_from_its_header_is_a_fault() -> None:
    """GOH.MAPROWCOLS: a short row read by column was accepted silently."""
    text = MAP.replace(
        "| `old.py` | Old. | gates | `test_old.py` |", "| `old.py` | Old. | `test_old.py` |"
    )
    assert map_table.faults(text) == ["docs/map.md:20: 3 cells, its table's header has 4"]
    piped = MAP.replace("| A. |", "| A `x|y` form. |")
    assert map_table.faults(piped) == ["docs/map.md:7: 5 cells, its table's header has 4"]
    assert map_table.faults(MAP) == []


def test_a_delimiter_row_below_the_rows_moves_under_its_header() -> None:
    row = "| `old.py` | Old. | gates | `test_old.py` |"
    text = MAP.replace(f"|---|---|---|---|\n{row}", f"{row}\n|---|---|---|---|")
    assert text != MAP
    assert map_table.normalise(text) == MAP


def test_the_lock_table_lists_exactly_the_files_that_take_each_lock() -> None:
    text = (
        "# head\nland\t20\tyes\trepo\ttools/land.sh,lib/gone.py\tthe land\n"
        "load\t30\tyes\tmachine\tlib/load_slot.py\tthe load\n"
    )
    holders = {
        "tools/land.sh": {"land"},
        "lib/new.py": {"land", "load"},
        "lib/load_slot.py": {"load"},
    }
    want = ("# head\nland\t20\tyes\trepo\ttools/land.sh,lib/new.py\tthe land\n"
            "load\t30\tyes\tmachine\tlib/load_slot.py,lib/new.py\tthe load\n")  # fmt: skip
    assert tables.lock_rows(text, holders) == want
    assert tables.lock_rows(want, holders) == want


def test_a_lock_no_file_takes_keeps_its_row_for_the_lock_check_to_refuse() -> None:
    text = "stale\t5\tno\trepo\tlib/gone.py\tnothing takes it\n"
    assert tables.lock_rows(text, {}) == text


def test_the_headroom_list_drops_what_is_no_longer_over_and_never_adds() -> None:
    text = "# ratchet\na.py\nb.py\n"
    assert tables.headroom_rows(text, {"a.py", "c.py"}) == "# ratchet\na.py\n"


def test_the_base_table_has_one_row_per_base_file_in_its_class() -> None:
    text = "# head\nlib/a.py\tbase-code\nlib/gone.py\tbase-code\n"
    base = {"lib/a.py", "lib/data.tsv", "tests/conftest.py"}
    out = tables.base_rows(text, base, {"lib/data.tsv": "lib/a.py"}, entry_reads=())
    assert out == ("# head\nlib/a.py\tbase-code\nlib/data.tsv\tload-read\n"
                   "tests/conftest.py\tsuite-wide\n")  # fmt: skip


def test_check_names_the_stale_table_and_the_one_command_that_fixes_it(tmp_path: Path) -> None:
    stale = tables.Table("demo", "demo.txt", lambda root, text: "fresh\n")
    (tmp_path / "demo.txt").write_text("old\n")
    found = tables.check(tmp_path, (stale,))
    assert found == [
        "demo.txt: not as lib/tables.py writes it (demo); run `python3 lib/tables.py --write`"
    ]
    assert tables.write(tmp_path, (stale,)) == ["demo.txt"]
    assert (tmp_path / "demo.txt").read_text() == "fresh\n" and tables.check(
        tmp_path, (stale,)
    ) == []


def test_every_table_a_copy_rule_demands_is_generated_or_says_why_not() -> None:
    named = {t.path for t in tables.TABLES} | set(tables.NOT_GENERATED)
    assert {
        "docs/map.md",
        "lib/lock_order.tsv",
        "tests/cap_headroom_allow.txt",
        "tests/base_files.tsv",
        "gates/verdict_free_keys.txt",
        "tests/linux_safe.txt",
    } <= named
    assert all(len(why) > 20 for why in tables.NOT_GENERATED.values())


def test_the_command_line_checks_and_exits_red_on_a_stale_table() -> None:
    argv = [sys.executable, str(REPO_ROOT / "lib" / "tables.py"), "--list"]
    done = subprocess.run(
        argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False
    )
    assert done.returncode == 0 and "docs/map.md" in done.stdout, done
