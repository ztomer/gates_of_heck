"""The disk shapes a test file writes out by hand, beside git_init_lint's (GOH.FSEVQUIET).

Owner, 2026-11-25: "let's also make the no disk churn a rule so it won't regress once we fix it".
tests/git_init_lint.py holds `git init` and a copytree of goh; this one its named holes and the
databases. A SITE, in tests/*.py, outside a template builder (a function named `_build*`):

  * `sqlite`: `sqlite3.connect(X)` where X is no in-memory database (the `:memory:` literal, or a
    URI text holding `mode=memory` or `vfs=memdb`). A goh.db opened through lib/db_core.py is no
    site: under the run's root it journals in memory (`throwaway`);
  * `goh-clone`: a list or call holding the strings "clone" (with "git" in it, or through a helper
    named `*git`) or "worktree" + "add", and naming REPO_ROOT, ROOT or GOH_ROOT: ~2,000 files made
    and removed. `this_tree` (tests/_this_tree.py) clones one the run built.

tests/disk_shapes.json holds both lists: `seed`, today's count per file, which only shrinks (a
count below it is stale: lower it in the same commit), and `allowed`, a new site with its reason.
Not read: a database or a clone made by a script a test runs; the run's own ceilings see those
(tests/_fs_ceilings.py).
"""

from __future__ import annotations

import ast

from git_init_lint import _name, _strs

GOH = {"REPO_ROOT", "ROOT", "GOH_ROOT"}
IN_MEMORY = (":memory:", "mode=memory", "vfs=memdb")


class _Sites(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found: list[tuple[str, int]] = []
        self._depth = 0

    def _fn(self, node) -> None:
        builder = node.name.startswith("_build")
        self._depth += builder
        self.generic_visit(node)
        self._depth -= builder

    visit_FunctionDef = _fn
    visit_AsyncFunctionDef = _fn

    def _add(self, shape: str, node: ast.AST) -> None:
        if not self._depth:
            self.found.append((shape, node.lineno))

    def visit_List(self, node: ast.List) -> None:
        if _clones(node, _strs(node.elts), False):
            self._add("goh-clone", node)
        self.generic_visit(node)

    visit_Tuple = visit_List

    def visit_Call(self, node: ast.Call) -> None:
        if ast.unparse(node.func) == "sqlite3.connect" and node.args:
            self._database(node)
        elif _clones(node, _strs(node.args), _name(node.func).endswith("git")):
            self._add("goh-clone", node)
        self.generic_visit(node)

    def _database(self, node: ast.Call) -> None:
        if not any(m in ast.unparse(node.args[0]) for m in IN_MEMORY):
            self._add("sqlite", node)


def _clones(node: ast.AST, strs: list[str], helper: bool) -> bool:
    makes = "clone" in strs or ("worktree" in strs and "add" in strs)
    if not makes or not (helper or "git" in strs):
        return False
    return bool({n.id for n in ast.walk(node) if isinstance(n, ast.Name)} & GOH)


def sites(text: str) -> list[tuple[str, int]]:
    """(shape, line) of every site in one file's source, in order."""
    v = _Sites()
    v.visit(ast.parse(text))
    return sorted(v.found, key=lambda s: s[1])


def counts(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for shape, _line in sites(text):
        out[shape] = out.get(shape, 0) + 1
    return out


def faults(now: dict[str, dict[str, int]], lists: dict[str, dict]) -> list[str]:
    """Each way `now` ({file: {shape: count}}) differs from the two lists."""
    out = []
    allowed = {(f, s): v[0] for f, per in lists["allowed"].items() for s, v in per.items()}
    seed = {(f, s): n for f, per in lists["seed"].items() for s, n in per.items()}
    have = {(f, s): n for f, per in now.items() for s, n in per.items()}
    for key in sorted({*allowed, *seed, *have}):
        want, got = seed.get(key, 0) + allowed.get(key, 0), have.get(key, 0)
        if got > want:
            out.append(
                f"{key[0]}: {got} `{key[1]}` sites, {want} listed: use the shared template, an"
                " in-memory database, or say why in disk_shapes.json `allowed`"
            )
        elif got < want:
            out.append(f"{key[0]}: {want} `{key[1]}` sites listed, {got} there: lower the list")
    for f, per in lists["allowed"].items():
        for shape, (_n, why) in per.items():
            if len(why.split()) < 4:
                out.append(f"{f}: `{shape}` is allowed with no reason")
    return out
