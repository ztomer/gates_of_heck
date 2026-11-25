"""`goh map` (lib/map_table.py, GOH.MAPGEN): the script map is rendered from the files' own headers.

docs/map.md was one committed table every lane edited, and three lanes went red on a conflict in it
in one day (2026-11-24). These tests hold the render to the tree (a row per script, callers from
code and never from prose, a header that speaks for another path), the refusals, the old commits
`--rev` still answers for, and the proof that two lanes that each add a script share no file.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, commit_all, git

from lib import map_table

TREE_READS = ("docs/map.md",)

FILES = {
    "gates/a.sh": "#!/bin/bash\n# a.sh -- what A | B does,\n# over two lines.\n#\n# More of A.\n"
    "# shellcheck disable=SC1091\n\n# not the header\n. lib/new_thing.py\n",
    "checks/check_x.py": '"""What X does."""\n',
    "lib/new_thing.py": '"""lib/new_thing.py -- What the thing does.\n\nMore.\n\n'
    '`lib/data/`: the data the thing reads.\n"""\n',
    "lib/data/one.json": "{}\n",
    "lib/prose.py": '"""Says new_thing.py in its prose only."""\n# and new_thing.py here\n',
    "tools/land.sh": '#!/bin/bash\n# Lands.\npython3 "$ROOT/lib/new_thing.py"\n',
    "crates/goh/src/emoji.rs": "//! Emoji are refused.\n//\n// `goh emoji`: the emoji gate.\n\n"
    "fn f() {}\n",
    "tests/test_new_thing.py": "from lib import new_thing\n",
}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for rel, text in FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


def test_every_script_is_a_row_said_by_its_own_header(root: Path) -> None:
    rows = map_table.render(root).splitlines()
    assert (
        "| `new_thing.py` | What the thing does. | `gates/a.sh`, `tools/land.sh` |"
        " `test_new_thing.py` |" in rows
    ), rows
    a = next(r for r in rows if r.startswith("| `a.sh`"))
    assert a.startswith("| `a.sh` | what A \\| B does, over two lines. |"), a
    assert (
        "| `goh emoji` | `crates/goh/src/emoji.rs` | the emoji gate. | no test names it |" in rows
    )
    assert "* `lib/data/` — the data the thing reads." in rows, rows
    assert "* `tools/land.sh` — Lands." in rows, rows
    for r in rows:  # GOH.MAPROWCOLS: a short row read by column was accepted silently
        assert not r.startswith("|") or len(r.replace("\\|", "").split("|")) == 6, r


def test_a_word_prints_the_whole_header_its_callers_and_its_tests(root, capsys) -> None:
    assert map_table.show(root, ["new_thing"]) == 0
    out = capsys.readouterr().out
    assert "More." in out and "`lib/data/`: the data" in out, out
    assert "Named by: `gates/a.sh`, `tools/land.sh`\n" in out, "prose is no call: " + out
    assert "Tests: `test_new_thing.py`\n" in out, out
    assert map_table.show(root, ["lib/data/"]) == 0
    assert "the data the thing reads" in capsys.readouterr().out
    assert map_table.show(root, ["goh emoji"]) == 0
    assert "the emoji gate" in capsys.readouterr().out
    assert map_table.show(root, ["no_such"]) == 1
    assert "nothing in the tree is named no_such" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("rel", "text", "why"),
    [
        ("lib/bare.py", "x = 1  # a comment is no docstring\n", "no docstring or header comment"),
        ("lib/bare.py", "# a comment above the code\nx = 1\n", "no docstring or header comment"),
        ("gates/bare.sh", "#!/bin/bash\n# shellcheck disable=SC1\nx=1\n# late\n", "no docstring"),
        (
            "lib/says.py",
            '"""Says.\n\n`lib/gone/`: the dir that left.\n"""\n',
            "speaks for lib/gone/",
        ),
    ],
)
def test_a_script_that_says_nothing_or_speaks_for_nothing_is_refused(root, rel, text, why) -> None:
    assert map_table.faults(root) == []
    (root / rel).write_text(text)
    found = map_table.faults(root)
    assert len(found) == 1 and found[0].startswith(f"{rel}: ") and why in found[0], found


def test_a_paragraph_about_an_example_path_is_no_entry(root) -> None:
    (root / "lib/ex.py").write_text('"""Ex.\n\n`x`: a symbol.\n\n`foo bar`: a phrase.\n"""\n')
    assert map_table.faults(root) == []


def test_a_rev_before_the_move_answers_from_its_committed_map(repo, capsys) -> None:
    (repo / "docs").mkdir()
    (repo / "docs/map.md").write_text("| Script | Purpose |\n|---|---|\n| `a.sh` | A. |\n")
    commit_all(repo)
    out = repo.parent / "out"
    out.mkdir()
    assert map_table.at_rev(repo, "HEAD", out) == ""
    assert map_table.show(out, ["a.sh"]) == 0
    assert capsys.readouterr().out == "| `a.sh` | A. |\n"
    assert "no-such-rev" in map_table.at_rev(repo, "no-such-rev", out)


def test_the_command_judges_the_checkout_its_export_runs_for(root, tmp_path, monkeypatch) -> None:
    export = tmp_path / "export"
    export.mkdir()
    (export / ".goh-head").write_text("")
    monkeypatch.setattr(map_table, "ROOT", export)
    monkeypatch.setenv("GOH_LIVE_ROOT", str(root))
    seen: list[Path] = []
    monkeypatch.setattr(map_table, "show", lambda r, words: seen.append(r) or 0)
    assert map_table.main(["a.sh"]) == 0 and seen == [root]


def test_no_copy_of_the_map_is_committed() -> None:
    page = (REPO_ROOT / "docs" / "map.md").read_text(encoding="utf-8")
    assert "|" not in page and len(page.splitlines()) <= 10 and "`goh map`" in page, page
    assert "map.md" not in (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")


def test_two_branches_that_each_add_a_script_merge_clean_with_no_driver(repo) -> None:
    """The map's conflicts were two lanes each adding a row; a script is its own row now."""
    (repo / "docs").mkdir()
    (repo / "lib").mkdir()
    (repo / "docs/map.md").write_text((REPO_ROOT / "docs" / "map.md").read_text(encoding="utf-8"))
    (repo / "lib/base.py").write_text('"""Base."""\n')
    commit_all(repo)
    base = git(repo, "rev-parse", "HEAD").strip()
    for name in ("one", "two"):
        git(repo, "checkout", "-q", "-b", name, base)
        (repo / f"lib/{name}.py").write_text(f'"""What {name} does."""\n')
        assert map_table.faults(repo) == []
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "--no-verify", "-m", name)
    git(repo, "merge", "-q", "--no-verify", "-m", "both", "one")  # raises on a conflict
    out = map_table.render(repo)
    assert "| `one.py` | What one does. |" in out and "| `two.py` | What two does. |" in out, out


def test_help_names_every_form() -> None:
    argv = [sys.executable, str(REPO_ROOT / "lib/map_table.py"), "--help"]
    done = subprocess.run(
        argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False
    )
    assert done.returncode == 0 and "--rev" in done.stdout and "--check" in done.stdout, done
