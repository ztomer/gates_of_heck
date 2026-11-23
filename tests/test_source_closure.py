"""lib/source_closure.py: a script's dependency closure is read from its own source lines, and the
fixture builder copies exactly that (GOH.CLASS.HARNESS); and no test copies part of goh by hand
beyond tests/fixture_copy_seed.json, which only shrinks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import fixture_copy_lint
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import source_closure as sc

TESTS = Path(__file__).resolve().parent
SEED = TESTS / "fixture_copy_seed.json"
GUARD = '. "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" || exit; goh_bash5 "$@"\n'


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return tmp_path


def test_every_spelling_of_the_scripts_own_directory_is_followed(tmp_path) -> None:
    root = _tree(
        tmp_path,
        {
            "gates/g.sh": "#!/usr/bin/env bash\n"
            + GUARD
            + '. "${BASH_SOURCE[0]%/*}/_a.sh" || exit 1   # trailing words\n'
            + 'HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
            + 'GOH="$(cd "$HERE/.." && pwd -P)"\n'
            + '. "$HERE/_b.sh"\n'
            + 'if true; then source "$GOH/tui/lib.sh"; fi\n'
            + '# . "$HERE/_commented.sh"\n',
            "lib/bash5.sh": "",
            "gates/_a.sh": '. "${GOH_DIR}/lib/c.sh"\n',
            "gates/_b.sh": "",
            "gates/_commented.sh": "",
            "tui/lib.sh": "",
            "lib/c.sh": "",
        },
    )
    files, unresolved = sc.closure(["gates/g.sh"], root=root, goh=root)
    assert files == [
        "gates/_a.sh",
        "gates/_b.sh",
        "gates/g.sh",
        "lib/bash5.sh",
        "lib/c.sh",
        "tui/lib.sh",
    ]
    assert unresolved == []


def test_a_path_the_script_does_not_place_is_named_never_guessed(tmp_path) -> None:
    root = _tree(
        tmp_path,
        {
            "gates/g.sh": '. ./.gatesrc\n. "$ROOT/.gatesrc"\n. "$_copy"\n',
            ".gatesrc": "",
        },
    )
    files, unresolved = sc.closure(["gates/g.sh"], root=root, goh=root)
    assert files == ["gates/g.sh"], "a consumer's .gatesrc is the caller's, not this tree's"
    assert len(unresolved) == 3 and all(u.startswith("gates/g.sh: ") for u in unresolved)


def test_an_unassigned_here_is_the_sourced_siblings_own_directory(tmp_path) -> None:
    root = _tree(tmp_path, {"gates/_p.sh": '. "$HERE/_q.sh"\n', "gates/_q.sh": ""})
    assert sc.closure(["gates/_p.sh"], root=root, goh=root)[0] == ["gates/_p.sh", "gates/_q.sh"]


def test_goh_dir_in_a_consumer_is_gohs_and_left_out_of_its_fixture(tmp_path) -> None:
    consumer = _tree(tmp_path / "c", {"tools/check.sh": '. "$GOH_DIR/tui/lib.sh"\n'})
    goh = _tree(tmp_path / "goh", {"tui/lib.sh": ""})
    files, unresolved = sc.closure(["tools/check.sh"], root=consumer, goh=goh)
    assert files == ["tools/check.sh"] and unresolved == []


def test_python_imports_are_followed_beside_the_file_and_on_gohs_paths(tmp_path) -> None:
    root = _tree(
        tmp_path,
        {
            "checks/check_x.py": "import _helper\nfrom tui.lib import ok\nimport os\n"
            "import killtree\n",
            "checks/_helper.py": "from . import sib\nfrom .pkg import mod\n",
            "checks/sib.py": "",
            "checks/pkg/__init__.py": "",
            "checks/pkg/mod.py": "",
            "tui/__init__.py": "",
            "tui/lib.py": "",
            "lib/killtree.py": "def broken(:\n",
        },
    )
    files, _ = sc.closure(["checks/check_x.py"], root=root)
    assert files == [
        "checks/_helper.py",
        "checks/check_x.py",
        "checks/pkg/__init__.py",
        "checks/pkg/mod.py",
        "checks/sib.py",
        "lib/killtree.py",
        "tui/__init__.py",
        "tui/lib.py",
    ]


def test_a_shebang_names_a_shell_script_and_data_has_no_dependencies(tmp_path) -> None:
    root = _tree(
        tmp_path,
        {
            "hooks/pre-commit": "#!/usr/bin/env bash\n" + GUARD,
            "hooks/notes": ". not-a-script\n",
            "lib/bash5.sh": "",
            "data.json": "{}",
        },
    )
    assert sc.closure(["hooks/pre-commit"], root=root)[0] == ["hooks/pre-commit", "lib/bash5.sh"]
    assert sc.closure(["hooks/notes", "data.json"], root=root)[0] == ["data.json", "hooks/notes"]
    assert sc.closure(["missing.sh"], root=root) == ([], [])


def test_the_builder_copies_the_closure_and_what_is_named_beside_it(tmp_path) -> None:
    # An anchor in the real tree: the guard, the house interpreter and the TUI, none of them listed.
    files = sc.copy_closure(tmp_path, "tools/release_goh.sh", extra=("Cargo.toml",))
    for rel in ("tools/release_goh.sh", "lib/bash5.sh", "lib/house_python.sh", "tui/lib.sh"):
        assert rel in files and (tmp_path / rel).read_bytes() == (REPO_ROOT / rel).read_bytes()
    assert (tmp_path / "Cargo.toml").is_file()


def test_the_copy_detector_sees_every_shape_and_nothing_else() -> None:
    src = (
        "import os, shutil\n"
        "from conftest import REPO_ROOT\n"
        "SCRIPT = REPO_ROOT / 'gates' / 'x.sh'\n"
        "def test_x(tmp_path):\n"
        "    shutil.copy2(SCRIPT, tmp_path / 'x.sh')\n"  # 5
        "    shutil.copytree(REPO_ROOT / 'lib', tmp_path / 'lib')\n"  # 6
        "    (tmp_path / 'lib').symlink_to(REPO_ROOT / 'lib')\n"  # 7
        "    os.symlink(SCRIPT, tmp_path / 'y')\n"  # 8
        "    (tmp_path / 'z').write_bytes(SCRIPT.read_bytes())\n"  # 9
        "    (tmp_path / 'w').write_text(SCRIPT.read_text())\n"  # 10
        "    shutil.copy2(tmp_path / 'a', tmp_path / 'b')\n"
        "    (tmp_path / 'z').write_bytes(b'x')\n"
        "    os.symlink(shutil.which('git'), tmp_path / 'git')\n"
    )
    assert fixture_copy_lint.findings(src) == [5, 6, 7, 8, 9, 10]


def test_no_test_copies_part_of_goh_by_hand_beyond_the_seed() -> None:
    seed = json.loads(SEED.read_text(encoding="utf-8"))["unreviewed"]
    now = fixture_copy_lint.census(TESTS)
    assert "test_install.py" in now, "the census lost a known hand copy: the detector is blind"
    grown = {f: (seed.get(f, 0), n) for f, n in now.items() if n > seed.get(f, 0)}
    assert not grown, (
        "a fixture copies part of goh by hand; use source_closure.copy_closure(dest, rel), which "
        f"reads what the script sources -- file: (seed, now): {grown}"
    )
    stale = {f: (n, now.get(f, 0)) for f, n in seed.items() if now.get(f, 0) < n}
    assert not stale, f"lower these seed entries to the file's count -- file: (seed, now): {stale}"


def test_a_reader_names_the_directories_modules_are_looked_for_in(tmp_path) -> None:
    """tools/affected_tests.py reads the tests' closure, whose runner puts tools/ on sys.path."""
    root = _tree(tmp_path, {"tests/t.py": "import tool\n", "tools/tool.py": ""})
    assert sc.direct(root / "tests/t.py", root, root)[0] == []
    found = sc.direct(root / "tests/t.py", root, root, (*sc.PY_DIRS, "tools"))[0]
    assert [p.relative_to(root).as_posix() for p in found] == ["tools/tool.py"]


def test_a_module_loaded_by_a_literal_name_is_followed_and_a_runtime_one_is_counted(
    tmp_path,
) -> None:
    """GOH.PROVENCLOSURE: a gate that loads its scanner with importlib read as importing nothing
    (the gap GOH.LANDERSTALECODE found), so a key on its closure missed a change to the scanner."""
    root = _tree(
        tmp_path,
        {
            "lib/runner.py": "import importlib\nm = importlib.import_module('scan')\n"
            "n = __import__('other')\nk = importlib.import_module(name_from_argv)\n",
            "checks/scan.py": "X = 1\n",
            "lib/other.py": "Y = 2\n",
        },
    )
    files, _ = sc.closure(["lib/runner.py"], root)
    assert files == ["checks/scan.py", "lib/other.py", "lib/runner.py"]
    import ast

    assert sc.dynamic_imports(ast.parse((root / "lib/runner.py").read_text())) == 1


def test_a_data_file_the_code_names_is_an_edge_and_prose_naming_it_is_not(tmp_path) -> None:
    """The `named` edge, one rule with tools/affected_tests.py: a literal path in CODE is a read; a
    docstring or comment saying it is not. Off by default (a fixture copies what is sourced)."""
    root = _tree(
        tmp_path,
        {
            "checks/check_x.py": '"""Reads lib/prose_only.json, says the docstring."""\n'
            "# and lib/comment_only.json in a comment\n"
            "SHAPES = ROOT / 'lib' / 'shapes.json'\nRULES = 'lib/rules.toml'\n",
            "lib/shapes.json": "{}\n",
            "lib/rules.toml": "\n",
            "lib/prose_only.json": "{}\n",
            "lib/comment_only.json": "{}\n",
        },
    )
    assert sc.closure(["checks/check_x.py"], root)[0] == ["checks/check_x.py"]
    files, _ = sc.closure(["checks/check_x.py"], root, named_edges=True)
    assert files == ["checks/check_x.py", "lib/rules.toml", "lib/shapes.json"]
