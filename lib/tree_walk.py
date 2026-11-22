"""lib/tree_walk.py -- what a tests/ helper's walk reads: the root, or only what it is handed.

GOH.CLONENOTJUDGE (2026-11-21). tools/affected_tests.py made a test a TREE READER (a judge, run on
every land) when it imported a tests/ helper whose source lists or walks a tree. _tree_clone.py
walks the template it builds (`os.walk(tree)`, `Path(tree).rglob(...)`), never the checkout, yet
each file importing `from_template` was a judge: ~455 s serial on every pre-step (GOH.LANDREVIEW).
Importing a walker does not make a file a reader; reaching a walk of the ROOT does. So each
helper def is classified by what its walk reads:

    ROOT    it walks the checkout: its source mentions REPO_ROOT (ROOT, GOH_DIR, GOH_ROOT) or a
            module-level name bound from one or from __file__, or it takes no parameter
            (`os.walk(".")`, `git ls-files` with no -C: the cwd), or a walk's line names none of
            its parameters -- or it calls a ROOT def, or hands a GIVEN def the root
    GIVEN   every walk in it is of its own parameters (`os.walk(tree)`, `(root / top).rglob("*")`):
            it reads what its caller hands it; it records WHICH parameters it walks

and a module-level walk makes the whole helper ROOT. A path is handed from its BASE (`parent / key`
is based at parent, `Path(t).resolve()` at t, a local name at what it was bound to). A file reaches
the root through a helper when it uses one of its ROOT defs (or imports a ROOT-whole helper), or
hands a walked parameter of a GIVEN def a ROOTED path: based at a root name, a name bound from one
(module- or def-level), `__file__`, or a literal (a path from the cwd).
tests/wall_clock_lint.py's `census(REPO_ROOT / "tests")` stays a reader; `from_template(factory,
"key", build, dst)` is not one.
"""

from __future__ import annotations

import ast
import re
from typing import NamedTuple

from lib import test_slice

ROOTS = frozenset({"REPO_ROOT", "ROOT", "GOH_DIR", "GOH_ROOT"})
ROOT = "root"
MODULE = ""  # the walks key of a module-level walk: the whole helper reads the root
# A walk of a tree: a listing of tracked files, a recursive glob or walk, a glob or listing of a
# root directory, or a tool run over the checkout (`cwd=REPO_ROOT`).
TREE_READ = re.compile(
    r"ls-files|ls_files|\.rglob\(|os\.walk\(|REPO_ROOT\b[^\n]*\.(?:glob|iterdir)\("
    r"|cwd=(?:str\()?REPO_ROOT\)?\s*[,)]"
)


class Given(NamedTuple):
    """A def that walks only what it is handed: its parameters in order, and the ones it walks."""

    params: tuple[str, ...]
    walked: frozenset[str]


Walk = str | Given  # ROOT, or Given


def _mentions(node: ast.AST, names: set[str] | frozenset[str]) -> bool:
    return any(isinstance(n, ast.Name) and (n.id in names or n.id == "__file__")
               for n in ast.walk(node))  # fmt: skip


def aliases(body: list[ast.stmt], base: frozenset[str] | set[str] = ROOTS) -> set[str]:
    """The root names plus every name `body` binds (in order) from an expression mentioning one."""
    out = set(base)
    for node in body:
        value = getattr(node, "value", None)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and value and _mentions(value, out):
            out |= test_slice.bound(node)
    return out


def _literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _base(node: ast.AST) -> ast.AST:
    """The path an expression is rooted at: `parent / key` -> parent, `Path(t).resolve()` -> t,
    `str(t)` -> t; a handed path is walked from its base, never from a part joined under it."""
    while True:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            node = node.left
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            node = node.func.value  # t.resolve(), t.joinpath(x)
        elif isinstance(node, ast.Call) and node.args:
            node = node.args[0]  # Path(t), str(t), os.fspath(t)
        elif isinstance(node, ast.Attribute):
            node = node.value  # t.parent
        else:
            return node


def _callee(call: ast.Call) -> str:
    """`f(...)` -> "f"; `h.f(...)` -> "h.f" (a helper bound as a module)."""
    fn = call.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
        return f"{fn.value.id}.{fn.attr}"
    return ""


def _handed(call: ast.Call, g: Given) -> list[ast.AST]:
    """The arguments of CALL that land on a parameter G walks."""
    out = [a for i, a in enumerate(call.args) if i < len(g.params) and g.params[i] in g.walked]
    return out + [k.value for k in call.keywords if k.arg in g.walked]


def _local(node: ast.AST, names: set[str]) -> set[str]:
    """NAMES plus what `node` binds from one (`t = REPO_ROOT / "lib"`)."""
    stmts = [n for n in ast.walk(node) if isinstance(n, (ast.Assign, ast.AnnAssign))]
    return aliases(sorted(stmts, key=lambda n: n.lineno), names)


def _bindings(node: ast.AST) -> dict[str, ast.AST]:
    """{name: value} for each single-name assignment in `node` (the last one wins)."""
    out: dict[str, ast.AST] = {}
    for n in ast.walk(node):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            out[n.targets[0].id] = n.value
        elif isinstance(n, (ast.With, ast.AsyncWith)):  # `with tmp(...) as t`
            for item in n.items:
                if isinstance(item.optional_vars, ast.Name):
                    out[item.optional_vars.id] = item.context_expr
    return out


def _resolved(arg: ast.AST, bound: dict[str, ast.AST], keep: set[str]) -> ast.AST:
    """ARG's base, followed through local names (never through the names in KEEP: the params)."""
    base = _base(arg)
    for _ in range(8):
        if not (isinstance(base, ast.Name) and base.id in bound and base.id not in keep):
            break
        base = _base(bound.pop(base.id))
    return base


def hands(node: ast.AST, given: dict[str, Given], names: set[str]) -> tuple[bool, set[str]]:
    """(whether `node` hands a GIVEN def a rooted argument, the names it hands one otherwise: a
    handed path's base, followed through the def's own local names)."""
    if not given:
        return False, set()
    local = _local(node, names)
    bound = _bindings(node)
    keep = set(_params(node))
    passed: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and (g := given.get(_callee(n))):
            for arg in _handed(n, g):
                base = _resolved(arg, dict(bound), keep)
                if _literal(base) or _mentions(base, local):
                    return True, set()
                if isinstance(base, ast.Name):
                    passed.add(base.id)
    return False, passed


def hands_root(node: ast.AST, given: dict[str, Given], names: set[str]) -> bool:
    return hands(node, given, names)[0]


def _params(node: ast.AST) -> tuple[str, ...]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ()
    a = node.args
    return tuple(x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs])


def _own_walk(node: ast.AST, seg: str, names: set[str]) -> Walk | None:
    """What a unit's own source walks: ROOT, Given, or None (no walk)."""
    hits = list(TREE_READ.finditer(seg))
    if not hits:
        return None
    params = _params(node)
    if not params or _mentions(node, names):
        return ROOT
    lines = seg.splitlines()
    walked: set[str] = set()
    for m in hits:
        line = lines[seg.count("\n", 0, m.start())]
        on = {p for p in params if re.search(rf"\b{re.escape(p)}\b", line)}
        if not on:
            return ROOT
        walked |= on
    return Given(params, frozenset(walked))


def walks(text: str) -> dict[str, Walk]:
    """{def or class name: ROOT | Given} for the helper's top-level units that walk, directly or
    through another of its own; {MODULE: ROOT} when a module-level statement walks. A file that
    does not parse walks the root whole when it names a walk (its walks cannot be told)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {MODULE: ROOT} if TREE_READ.search(text) else {}
    lines = text.splitlines()
    names = aliases(tree.body)
    units = [(n, test_slice.segment(lines, n)) for n in tree.body
             if not isinstance(n, (ast.Import, ast.ImportFrom))]  # fmt: skip
    out: dict[str, Walk] = {}
    for node, seg in units:
        if (kind := _own_walk(node, seg, names)) is None:
            continue
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return {MODULE: ROOT}
        out[node.name] = kind
    changed = True
    while changed:
        changed = False
        for node, _seg in units:
            name = getattr(node, "name", None)
            now = out.get(name) if name else None
            if now == ROOT:
                continue
            refs = test_slice.refs(node) - {name}
            given = {k: v for k, v in out.items() if isinstance(v, Given) and k != name}
            rooted, passed = hands(node, given, names)
            params = _params(node)
            if rooted or any(out.get(r) == ROOT for r in refs):
                kind: Walk | None = ROOT
            elif walked := passed & set(params):
                kind = Given(params, frozenset(walked | (now.walked if now else set())))
            else:
                kind = now
            if kind is None or kind == now:
                continue
            if name is None:
                return {MODULE: ROOT}
            out[name] = kind
            changed = True
    return out


Walking = dict[str, dict[str, Walk]]  # {tests/ helper import name: its walks}


def imported(tree: ast.Module, walking: Walking) -> tuple[set[str], dict[str, Given]]:
    """(the names a file binds to a ROOT walk of a helper in WALKING, the callables it binds to a
    Given one: `f` or `h.f`)."""
    roots: set[str] = set()
    given: dict[str, Given] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in walking:
            w = walking[node.module]
            for a in node.names:
                if a.name == "*":  # every def it walks with, under its own name
                    roots |= {n for n, k in w.items() if k == ROOT}
                    given |= {n: k for n, k in w.items() if isinstance(k, Given)}
                    continue
                kind = ROOT if MODULE in w else w.get(a.name)
                if kind == ROOT:
                    roots.add(a.asname or a.name)
                elif isinstance(kind, Given):
                    given[a.asname or a.name] = kind
        elif isinstance(node, ast.Import):
            for a in node.names:
                if (w := walking.get(a.name)) is None:
                    continue
                local = a.asname or a.name
                if MODULE in w or ROOT in w.values():
                    roots.add(local)
                given |= {f"{local}.{n}": k for n, k in w.items() if isinstance(k, Given)}
    return roots, given


def reaches(text: str, walking: Walking) -> str | None:
    """The helper in WALKING through which TEXT reaches a walk of the root (module doc), or None."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    names = aliases(tree.body)
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        mods = [m for m in _modules(node) if m in walking]
        if not mods:
            continue
        if any(MODULE in walking[m] for m in mods):
            return mods[0]  # importing it runs its walk
        roots, given = imported(ast.Module(body=[node], type_ignores=[]), walking)
        rest = [n for n in tree.body if n is not node]
        if roots & set().union(*(test_slice.refs(n) for n in rest)):
            return mods[0]
        if any(hands_root(n, given, names) for n in rest):
            return mods[0]
    return None


def _modules(node: ast.Import | ast.ImportFrom) -> list[str]:
    return [str(node.module)] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
