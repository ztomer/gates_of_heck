"""lib/declared_reads.py -- what a test reads from the tree at run time (GOH.SELECTAUDIT).

tools/affected_tests.py chooses a test that NAMES a changed path (every part quoted). A test that
reads a whole DIRECTORY names none of the files in it: tests/test_swift_gate_baseline.py copies
`REPO_ROOT / "tests" / "fixtures" / "swift_baseline_project"` whole, so a change to a file under
that fixture chose nothing but the tree readers (2026-11-17, measured on main's selector). Here
the reads are found two ways, and a change to a path one of them covers chooses the file whole:

    INFERRED   a directory spelled as REPO_ROOT (or ROOT, GOH_DIR) and literal parts, directly or
               through a module-level name bound to that spelling, that is the receiver of
               .glob/.rglob/.iterdir (`SRC.glob("*.rs")` -> `crates/goh/src/*.rs`) or the first
               argument of copytree/listdir/walk/scandir (the whole directory)
    DECLARED   a module-level `TREE_READS = ("pattern", ...)`: what no spelling gives away -- a path
               joined from a variable (`REPO_ROOT / rel`), a read made by a helper. A pattern is
               fnmatch-style from the root (`*` crosses `/`); one with no wildcard is that file,
               or every file under it when it ends in `/`.

A repo-wide judge (test_every_/test_no_, a tree walker, test_self_walk.py) is chosen for every
diff already (affected_tests.tree_readers). tests/test_declared_reads.py is the ratchet: a test
that joins REPO_ROOT with a non-literal declares TREE_READS, or is on gates/undeclared_reads.txt,
the list of today's that only shrinks (a listed file that declares, or joins no more, is stale).
"""

from __future__ import annotations

import ast
import fnmatch
import functools
import re
from pathlib import Path

ROOTS = frozenset({"REPO_ROOT", "ROOT", "GOH_DIR", "GOH_ROOT"})
GLOBS = frozenset({"glob", "rglob", "iterdir"})
WHOLE = frozenset({"copytree", "listdir", "walk", "scandir"})
DYNAMIC = "?"  # a chain with a non-literal part
# A file with none of these reads nothing either way: it is never parsed (the pre-step's cost).
MAY_READ = re.compile(r"\.(?:r?glob|iterdir)\(|copytree|listdir|walk\(|scandir|TREE_READS")


def _chain(node: ast.AST) -> str | None:
    """`REPO_ROOT / "a" / "b"` -> "a/b"; a non-literal part -> DYNAMIC; no root -> None."""
    parts: list[str] = []
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        right = node.right
        if not (isinstance(right, ast.Constant) and isinstance(right.value, str)):
            parts = [DYNAMIC]
        elif parts != [DYNAMIC]:
            parts.append(right.value)
        node = node.left
    if not (isinstance(node, ast.Name) and node.id in ROOTS) or not parts:
        return None
    return DYNAMIC if parts == [DYNAMIC] else "/".join(reversed(parts))


def _parse(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def declared(text: str) -> tuple[str, ...]:
    """The file's module-level TREE_READS patterns (literal strings only)."""
    tree = _parse(text)
    for node in tree.body if tree else ():
        target = node.targets[0] if isinstance(node, ast.Assign) and node.targets else None
        if isinstance(target, ast.Name) and target.id == "TREE_READS":
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return ()
            return tuple(p for p in value if isinstance(p, str))
    return ()


def inferred(text: str, root: Path) -> set[str]:
    """The read patterns the file's spelled directory reads give away (see INFERRED)."""
    tree = _parse(text)
    if tree is None:
        return set()
    names: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and (c := _chain(node.value)) not in (None, DYNAMIC):
                names[target.id] = c

    def where(node: ast.AST) -> str | None:
        found = names.get(node.id) if isinstance(node, ast.Name) else _chain(node)
        return found if found not in (None, DYNAMIC) and (root / found).is_dir() else None

    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name in GLOBS and isinstance(fn, ast.Attribute) and (d := where(fn.value)):
            arg = node.args[0] if node.args else None
            pat = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else ""
            out.add(f"{d}/{pat}" if pat and name != "iterdir" else f"{d}/")
        elif name in WHOLE and node.args and (d := where(node.args[0])):
            out.add(f"{d}/")
    return out


def dynamic(text: str) -> bool:
    """Whether the file joins a root with a non-literal part (`REPO_ROOT / rel`)."""
    tree = _parse(text)
    return tree is not None and any(
        isinstance(n, ast.BinOp) and _chain(n) == DYNAMIC for n in ast.walk(tree)
    )


def matches(pattern: str, path: str) -> bool:
    if pattern.endswith("/"):
        return path.startswith(pattern)
    return fnmatch.fnmatchcase(path, pattern)


@functools.lru_cache(maxsize=4096)
def reads(text: str, root: Path) -> tuple[str, ...]:
    if not MAY_READ.search(text):
        return ()
    return tuple(sorted({*declared(text), *inferred(text, root)}))


def choose(root: Path, tests: dict[str, str], changed: list[str], chosen: dict[str, str]) -> None:
    """Add to CHOSEN (whole) each test whose reads cover a changed path, with why."""
    for rel, text in tests.items():
        if rel in chosen:
            continue
        for pattern in reads(text, root):
            if hit := next((p for p in changed if matches(pattern, p)), None):
                chosen[rel] = f"reads {pattern} ({hit})"
                break
