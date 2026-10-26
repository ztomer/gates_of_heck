"""A second `def test_x` in one scope silently replaces the first, which then never runs (G17).

THE CLASS. Python binds a name once per scope, so two `def test_x` (or two classes of one name) leave
only the LAST one for pytest to collect: the first is a test that exists in the file, reads as
coverage, and never runs. ruff's F811 (redefined-while-unused) sees it, but F811 is also the rule
that fires on a pytest fixture imported from a shared module and then taken as a test parameter
(90 findings in this tree, every one of that shape and none a shadowed test), so `pyproject.toml`
exempts those nine files from it. This test is the check that does not depend on ruff and does not
have the exemption: no function or class name is bound twice in one body anywhere in the tree.
Property setters, `typing.overload` stubs and `singledispatch` registrations rebind a name on
purpose and are not findings.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from conftest import REPO_ROOT

# A decorator that rebinds the name it decorates on purpose.
REBINDERS = {"setter", "getter", "deleter", "overload", "register"}


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def shadowed(source: str) -> list[tuple[str, list[int]]]:
    """Every `(name, [lines])` a body in `source` binds twice with `def` or `class`."""
    found = []
    for node in ast.walk(ast.parse(source)):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        seen: dict[str, list[int]] = {}
        for stmt in body:
            if not isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            if any(_decorator_name(d) in REBINDERS for d in stmt.decorator_list):
                continue
            seen.setdefault(stmt.name, []).append(stmt.lineno)
        found += [(name, lines) for name, lines in seen.items() if len(lines) > 1]
    return found


def test_the_scan_sees_a_shadowed_test_and_spares_a_rebinding_decorator() -> None:
    """Prove the scan can fail: a planted duplicate is found, a property setter is not."""
    planted = "def test_a():\n    pass\n\n\ndef test_a():\n    pass\n"
    assert shadowed(planted) == [("test_a", [1, 5])]
    in_class = "class T:\n    def test_a(self): ...\n    def test_a(self): ...\n"
    assert shadowed(in_class) == [("test_a", [2, 3])]
    setter = (
        "class C:\n    @property\n    def v(self): ...\n    @v.setter\n    def v(self, x): ...\n"
    )
    assert shadowed(setter) == []


def test_no_tracked_python_file_binds_a_def_or_class_twice_in_one_scope() -> None:
    listed = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    # No git (an unpacked export): every .py outside the build and VCS directories.
    tracked = (
        listed.stdout.split()
        if listed.returncode == 0
        else [
            str(p.relative_to(REPO_ROOT))
            for p in REPO_ROOT.rglob("*.py")
            if not {"target", ".git"} & set(p.parts)
        ]
    )
    files = [f for f in tracked if not f.startswith("tests/fixtures/")]
    assert len(files) > 100, f"the scan read {len(files)} files: the listing stopped matching"
    bad = {}
    for rel in files:
        path = Path(REPO_ROOT / rel)
        if path.exists() and (hits := shadowed(path.read_text())):
            bad[rel] = hits
    assert not bad, f"a second def/class of one name replaces the first (it never runs): {bad}"
