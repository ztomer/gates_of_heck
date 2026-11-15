"""The fixture shapes that copy part of goh by hand, so a dependency the copy forgot goes unseen.

GOH.CLASS.HARNESS (2026-11-15): about 28 fixture items in a month were a fixture that copied a gate
and not what it sources -- four suites broke in one round when lib/bash5.sh became every script's
first line (bcb4016). The one builder is `lib/source_closure.copy_closure(dest, "gates/x.sh")`,
which reads the script's `source`/`.` lines and imports.

What this READS, in every tests/**/*.py but the fixture subjects under tests/fixtures/: a call to
`shutil.copy`/`copy2`/`copyfile`/`copytree`, `os.symlink`, `Path.symlink_to`, or
`X.write_bytes(Y.read_bytes())`/`X.write_text(Y.read_text())`, whose SOURCE is a path in the goh
tree -- an expression naming `REPO_ROOT`, `ROOT`, `GOH_ROOT`, or a name the file binds (at any
level) to one of those. What it does NOT read: a copy through a helper in another module (that
helper's own module is read), a path built from a string (`Path("/Users/.../gates")`), and a copy
of a file the test itself wrote.
"""

from __future__ import annotations

import ast
from pathlib import Path

BASES = {"REPO_ROOT", "ROOT", "GOH_ROOT"}
COPIES = {"copy", "copy2", "copyfile", "copytree", "symlink"}


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _repo_names(tree: ast.AST) -> set[str]:
    derived = set(BASES)
    assigns = [n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign))]
    grew = True
    while grew:
        grew = False
        for a in assigns:
            value = a.value
            if value is None or not (_names(value) & derived):
                continue
            targets = a.targets if isinstance(a, ast.Assign) else [a.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id not in derived:
                    derived.add(t.id)
                    grew = True
    return derived


def findings(source: str) -> list[int]:
    """The line of every hand copy of the goh tree in `source`."""
    tree = ast.parse(source)
    repo = _repo_names(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
        src: ast.AST | None = None
        if name in (*COPIES, "symlink_to") and node.args:
            src = node.args[0]
        elif name in ("write_bytes", "write_text") and node.args:
            arg = node.args[0]
            if (
                isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Attribute)
                and arg.func.attr in ("read_bytes", "read_text")
            ):
                src = arg.func.value
        if src is not None and _names(src) & repo:
            found.append(node.lineno)
    return sorted(found)


def census(tests: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    for path in sorted(tests.rglob("*.py")):
        rel = path.relative_to(tests).as_posix()
        if rel.startswith("fixtures/"):
            continue
        n = len(findings(path.read_text(encoding="utf-8")))
        if n:
            out[rel] = n
    return out
