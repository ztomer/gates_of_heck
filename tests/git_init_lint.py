"""The per-test repositories and goh-tree copies a test file makes for itself (GOH.GITINITGATE).

Each `git init` a test runs is ~60 new paths (`.git/` with its hooks, objects fan-out and refs) and
each copy of goh's tree hundreds more, every one an fseventsd event and a Spotlight candidate; a dot
prefix does not help (612 events against 607, GOH.FSCHURN3). The suite has a shared template for
both (`template` / `from_template` in tests/_tree_clone.py, the `repo` fixture in conftest.py): the
tree is built ONCE per run and each test gets a clone. A SITE is one of, in tests/*.py:

  * a call to `fast_init` (tests/_fast_git.py, the suite's cheap `git init`: still a new `.git`);
  * `init` as git's subcommand: a list or call whose string arguments hold "git" ... "init", a call
    to a helper named `git`/`_git`/`*_git` with "init" among its string arguments, or a string
    constant holding `git init` / `git -C <dir> init`;
  * a copy of goh's own tree: a `copytree` call whose source names `REPO_ROOT`.

A site inside a function whose name starts with `_build` is a template builder -- what the shared
template runs ONCE per run -- and is no site. `tests/git_init_seed.json` holds today's count per
file; tests/test_git_init_ratchet.py refuses a count above it (a NEW per-test repo: use the shared
template) and one below it (the seed is lowered in the same commit, so the list only shrinks).

What this does NOT read, so a reader knows the hole: a repo made by a shell script a test runs, a
`git clone` of a local repo (cheaper than an init, still a tree), and a copy of goh's tree spelled
without `REPO_ROOT`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

GIT_INIT_TEXT = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?init\b")
COPIERS = {"copytree"}


def _strs(nodes) -> list[str]:
    return [n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_init_seq(strs: list[str]) -> bool:
    return "git" in strs and "init" in strs[strs.index("git") + 1 :]


class _Sites(ast.NodeVisitor):
    def __init__(self) -> None:
        self.lines: list[int] = []
        self._depth = 0  # inside a `_build*` template builder

    def _fn(self, node) -> None:
        builder = node.name.startswith("_build")
        self._depth += builder
        self.generic_visit(node)
        self._depth -= builder

    visit_FunctionDef = _fn
    visit_AsyncFunctionDef = _fn

    def _add(self, node: ast.AST) -> None:
        if not self._depth:
            self.lines.append(node.lineno)

    def visit_List(self, node: ast.List) -> None:
        if _is_init_seq(_strs(node.elts)):
            self._add(node)
            return  # one site, however it is called
        self.generic_visit(node)

    visit_Tuple = visit_List

    def visit_Call(self, node: ast.Call) -> None:
        name, strs = _name(node.func), _strs(node.args)
        helper = name in ("git", "_git") or name.endswith("_git")
        copy = name in COPIERS and bool(node.args) and "REPO_ROOT" in ast.unparse(node.args[0])
        if name == "fast_init" or (helper and "init" in strs) or _is_init_seq(strs) or copy:
            self._add(node)
        self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr) -> None:
        if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            self.generic_visit(node)  # a docstring or a bare string runs nothing

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and GIT_INIT_TEXT.search(node.value):
            self._add(node)


def sites(text: str) -> list[int]:
    """The line of every site in one file's source, in order."""
    v = _Sites()
    v.visit(ast.parse(text))
    return sorted(v.lines)


def census(tests: Path) -> dict[str, list[int]]:
    """Every tests/*.py with a site: its name -> the sites' lines."""
    out = {}
    for f in sorted(tests.glob("*.py")):
        found = sites(f.read_text(encoding="utf-8"))
        if found:
            out[f.name] = found
    return out
