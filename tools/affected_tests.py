#!/usr/bin/env python3
"""The pre-land test set, chosen from the diff (GOH.TREETESTSFIRST).

    affected_tests.py [--root DIR] [--base REV] [--why]   # the test files, one per line
    affected_tests.py --root DIR --changed PATH...         # the same for a named change
    affected_tests.py --run [--base REV]                   # run them: tools/pytest.sh <the set>

WHY. A lane ran the tests it chose by hand, then lost a whole land gate (a slot wait and ~8 min of
suite) to one it had not chosen: GOH.ONEADMISSION's first land (2026-11-16, 1089 s over two
attempts) went red on tests/test_lock_order.py, which RUNS the changed tools/load.py by path
(`REPO_ROOT / "tools" / "load.py"`), and GOH.TAGPUSHGATE's on tests/test_self_walk.py, a tree reader
whose name matches no pattern. The choice was by hand and by name, so it is made here, from the
diff, and tools/land.sh runs the set (GOH_LAND_PRE in .gatesrc) before the land lock and before the
full gate's slot: a test the lane missed fails in seconds.

THE SET is the union of
  * the CHANGED test files themselves (added or modified under tests/);
  * every tests/test_*.py that NAMES a changed path -- its text holds the path, or every component
    of it as a quoted literal (`"tools" / "load.py"`, `os.path.join(ROOT, "tools", "load.py")`)
    -- or IMPORTS a changed Python module (`import admission`, `from lib import admission`, an
    import of a changed tests/ helper), read with ast;
  * the TREE READERS, chosen by what they read: a test file that defines a test_every_/test_no_
    function, or whose source (or a tests/ helper it imports) lists or walks the tracked tree
    (`git ls-files`, `rglob(`, `os.walk(`, a glob or
    iterdir of a REPO_ROOT directory), plus ALWAYS (readers no pattern sees). A changed path
    can break a tree reader whatever it names, so they run on every land.
A deleted file is a changed path too: a test still naming it is the one that will go red.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tui.lib import counted, info, ok

# Tree readers that neither a test name nor a tree-walking call in their own source gives away.
ALWAYS = ("tests/test_self_walk.py", "tests/test_python_from_head.py")
TREE_NAMED = re.compile(r"^\s*def (test_every_|test_no_)", re.MULTILINE)
TREE_READ = re.compile(
    r"ls-files|ls_files|\.rglob\(|os\.walk\(|REPO_ROOT\b[^\n]*\.(?:glob|iterdir)\("
)


def _imports(text: str) -> set[str]:
    """Every module name an import in `text` could bind: `a.b` gives {a.b, a, b}; `from a import b`
    gives {a, a.b, b}. A file that does not parse imports nothing (its own test run says why)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
                names.update(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(node.module.split("."))
            for alias in node.names:
                names.add(f"{node.module}.{alias.name}")
                names.add(alias.name)
    return names


def _module_names(path: str) -> set[str]:
    """The names a test imports `path` by: `lib/admission.py` -> {lib.admission, admission}."""
    p = Path(path)
    if p.suffix != ".py" or p.name == "__init__.py":
        return set()
    return {".".join(p.with_suffix("").parts), p.stem}


def _names_path(text: str, path: str) -> bool:
    """A file at the root (`.gatesrc`, `install.sh`) is named only as `REPO_ROOT / ".gatesrc"`: its
    bare name is every fixture's own copy (80 tests write a `.gatesrc`), not goh's."""
    parts = Path(path).parts
    if len(parts) == 1:
        return re.search(rf"\bREPO_ROOT\s*/\s*[\"']{re.escape(path)}[\"']", text) is not None
    if path in text:
        return True
    return all(f'"{c}"' in text or f"'{c}'" in text for c in parts)


def _tests(root: Path) -> dict[str, str]:
    return {
        f"tests/{p.name}": p.read_text(encoding="utf-8", errors="replace")
        for p in sorted((root / "tests").glob("test_*.py"))
    }


def _helpers(root: Path) -> dict[str, str]:
    """tests/ modules that are not test files, by import name (`_self_walk`)."""
    return {
        p.stem: p.read_text(encoding="utf-8", errors="replace")
        for p in sorted((root / "tests").glob("*.py"))
        if not p.name.startswith("test_") and p.name != "conftest.py"
    }


def tree_readers(root: Path, tests: dict[str, str] | None = None) -> dict[str, str]:
    """{test file: why it is a tree reader}."""
    tests = _tests(root) if tests is None else tests
    walking = {name for name, text in _helpers(root).items() if TREE_READ.search(text)}
    found: dict[str, str] = {}
    for rel, text in tests.items():
        if rel in ALWAYS:
            found[rel] = "a tree reader no pattern sees (ALWAYS)"
        elif TREE_NAMED.search(text):
            found[rel] = "defines a test_every_/test_no_ test"
        elif TREE_READ.search(text):
            found[rel] = "lists or walks the tree"
        elif via := sorted(walking & _imports(text)):
            found[rel] = f"imports {via[0]}, which lists or walks the tree"
    return found


def affected(root: Path, changed: list[str]) -> dict[str, str]:
    """{test file: why it runs before the land} for a change to `changed` (paths from `root`)."""
    tests = _tests(root)
    chosen: dict[str, str] = {}
    for path in changed:
        if path.startswith("tests/test_") and path.endswith(".py") and path in tests:
            chosen.setdefault(path, "changed")
    for rel, text in tests.items():
        if rel in chosen:
            continue
        imported = _imports(text)
        for path in changed:
            if path == rel:
                continue
            if mods := sorted(_module_names(path) & imported):
                chosen[rel] = f"imports {mods[0]} ({path})"
                break
            if _names_path(text, path):
                chosen[rel] = f"names {path}"
                break
    for rel, why in tree_readers(root, tests).items():
        chosen.setdefault(rel, why)
    return dict(sorted(chosen.items()))


def changed_paths(root: Path, base: str) -> list[str]:
    """What HEAD changes against its merge base with `base` (renames as add + delete)."""
    out = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-only", "--no-renames", f"{base}...HEAD"],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},  # a reader never takes index.lock
    ).stdout
    return [line for line in out.splitlines() if line]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=".", help="the repository (default: the cwd)")
    ap.add_argument("--base", default="main", help="the revision the change is measured from")
    ap.add_argument("--changed", nargs="*", help="the changed paths, instead of the diff")
    ap.add_argument("--why", action="store_true", help="say why each file is chosen")
    ap.add_argument(
        "--run", action="store_true", help="run the set with the root's tools/pytest.sh"
    )
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    changed = args.changed if args.changed is not None else changed_paths(root, args.base)
    chosen = affected(root, changed)
    if args.run:
        return run(root, changed, chosen)
    for rel, why in chosen.items():
        print(f"{rel}  # {why}" if args.why else rel)
    return 0


def run(root: Path, changed: list[str], chosen: dict[str, str]) -> int:
    """tools/pytest.sh over the set. An EMPTY set runs nothing: pytest.sh with no file runs the
    whole suite, which is the full gate's job, never the pre-step's. Inside a running goh suite (a
    fixture land of a goh copy) it runs nothing either and says so: pytest.sh refuses a nested
    suite, and the suite around it is already running these tests."""
    if not chosen:
        ok(f"pre-land: no test is chosen by {counted(len(changed), 'changed path')}")
        return 0
    if os.environ.get("GATES_OF_HECK_SUITE_RUNNER") and not os.environ.get(
        "GATES_OF_HECK_SUITE_NESTED_OK"
    ):
        info(
            f"pre-land: {counted(len(chosen), 'test file')} not run: inside a running goh suite "
            f"(runner pid {os.environ['GATES_OF_HECK_SUITE_RUNNER']}), which runs them"
        )
        return 0
    readers = len(tree_readers(root))
    info(
        f"pre-land: {counted(len(chosen), 'test file')} -- {readers} tree readers, the rest chosen "
        f"by {counted(len(changed), 'changed path')}"
    )
    return subprocess.run(
        ["bash", str(root / "tools" / "pytest.sh"), "-q", *chosen],
        check=False,  # no cwd: pytest.sh runs in its own checkout, the root
        stdin=subprocess.DEVNULL,
    ).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
