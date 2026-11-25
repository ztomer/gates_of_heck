"""lib/test_slice.py: the part of a test module another file uses when it imports names from it.

GOH.SELECTTESTIMPORTS. A test that imports `_repo` or a fixture from tests/test_x.py runs that
helper's code, not test_x's own tests: lib/test_closure.py followed the import as if the importer
read everything test_x reads, and a change to gates/push_gate.sh chose 114 test files, most of
them only because they import a helper from a test that runs the gate. Here: the code of the
names one file imports from a test module, and of everything in that module those names reach.

THE SLICE of test module M for the names N an importer takes from it is the source of
  * every import statement of M (pytest loads what M imports when the importer imports M);
  * every module-level statement that runs a call at import (`TEXT = SCRIPT.read_text()`,
    `sys.path.insert(...)`, `pytestmark = ...`) or binds nothing (an `if`, a bare expression):
    it runs whatever the importer takes, a call no PURE name makes (`str`, `Path`); and
    `TREE_READS` (lib/declared_reads.py), which declares what the module's helpers read;
  * the definitions and module-level names in N, and every one of M's own top-level or class-level
    names they mention, at any depth: a function's body, a fixture's arguments (the fixtures it
    asks for), a class whole;
and it is the whole module (None) when the slice cannot be told: `from M import *`, M bound as a
module and used other than as `M.name`, M loaded by a name no static import shows
(`importlib.import_module`), a file that does not parse, or a slice that calls `globals()`,
`vars()`, `eval` or `exec` or reads `sys.modules`. A def M does not export is left out with its
decorators: they shape only that test, which the importer does not collect.

The part of a test module another file uses when it imports names from it (GOH.SELECTTESTIMPORTS):
`slice_of(text, names)` is the module's imports, its import-time calls, and the definitions the
names reach through the module's own names (a fixture's argument fixtures, a class whole);
`taken(text, target)` the names an importer takes (`from test_x import a`, `test_x.a`); the whole
module for `import *`, a module handed on, a run-time name, `globals()`/`sys.modules`.
`test_closure.py` reaches an imported test module as that slice, except for a test on
`gates/undeclared_reads.txt` (or a slice holding such a join), which keeps the whole file: a change
to gates/push_gate.sh chose 114 test files, 87 after.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

# Calls with no read and no effect: a module-level binding made only of these is a value, which
# matters to the importer only when its slice uses it.
PURE = frozenset(
    {
        "str", "int", "float", "bool", "bytes", "tuple", "list", "dict", "set", "frozenset",
        "Path", "PurePath", "PurePosixPath", "resolve", "absolute", "joinpath", "with_name",
        "with_suffix", "with_stem", "relative_to", "as_posix", "dirname", "basename", "abspath",
        "realpath", "normpath", "join", "format", "compile", "get", "getenv", "frozen",
        "dataclass", "field", "namedtuple", "TypeVar", "cast", "partial", "upper", "lower",
        "strip", "split", "replace", "fspath", "len", "range", "sorted",
    }
)  # fmt: skip
# Module-level names a slice always holds: lib/declared_reads.py's TREE_READS declares what the
# module's helpers read that no spelling gives away, so it is the reads of every slice of it.
KEEP = frozenset({"TREE_READS"})
# Calls that look one of the module's own names up at run time: no slice can follow them (an import
# by a literal, `__import__("_hook_goh")`, is an edge of lib/source_closure.py, not a lookup here).
DYNAMIC = frozenset({"globals", "vars", "eval", "exec"})


def _looks_up(node: ast.AST) -> bool:
    """Whether `node` reads the module's names by a string: a DYNAMIC call or `sys.modules`."""
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and _call_name(n) in DYNAMIC:
            return True
        if (
            isinstance(n, ast.Attribute)
            and n.attr == "modules"
            and getattr(n.value, "id", "") == "sys"
        ):
            return True
    return False


def refs(node: ast.AST) -> set[str]:
    """Every name `node` mentions: bare names, attributes (`self.walk`) and argument names (the
    fixtures a test asks for)."""
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
    return out


def bound(node: ast.stmt) -> set[str]:
    """The names a module- or class-level statement binds."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
        elif isinstance(n, ast.alias):
            out.add((n.asname or n.name).split(".")[0])
    return out


def segment(lines: list[str], node: ast.AST) -> str:
    """`node`'s source, its decorators included (a parametrize over `REPO_ROOT.rglob(...)`)."""
    first = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
    return "\n".join(lines[first - 1 : node.end_lineno])


def _call_name(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def _runs(node: ast.stmt) -> bool:
    """Whether a module-level statement does something at import whatever the importer takes."""
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return True
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return False  # defined, not run: its body runs when the importer calls it
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return False  # a docstring
    if KEEP & bound(node):
        return True
    if not bound(node):
        return True
    return any(isinstance(n, ast.Call) and _call_name(n) not in PURE for n in ast.walk(node))


@lru_cache(maxsize=4096)
def _parse(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def slice_of(text: str, names: frozenset[str]) -> tuple[str, frozenset[str]] | None:
    """(the slice's source, the module-level names it holds) of module TEXT for the NAMES an
    importer takes from it (module doc); None for the whole module."""
    tree = _parse(text)
    if tree is None:
        return None
    lines = text.splitlines()
    units = [(bound(n), n) for n in tree.body]
    defined = set().union(*(b for b, _ in units)) if units else set()
    need = set(names) & defined
    keep = [_runs(n) or bool(b & need) for b, n in units]
    grew = True
    while grew:  # a kept unit needs what it mentions; a unit binding a needed name is kept
        grew = False
        for i, (_, n) in enumerate(units):
            if keep[i] and (more := (refs(n) & defined) - need):
                need |= more
                grew = True
        for i, (b, _) in enumerate(units):
            if not keep[i] and b & need:
                keep[i] = grew = True
    kept = [n for i, (_, n) in enumerate(units) if keep[i]]
    if any(_looks_up(n) for n in kept):
        return None
    return "\n".join(segment(lines, n) for n in kept) + "\n", frozenset(need)


def is_test_module(rel: str) -> bool:
    """Whether `rel` is a pytest test module (`tests/test_x.py`): what this slices."""
    name = Path(rel).name
    return name.startswith("test_") and name.endswith(".py")


def _spellings(target: str) -> tuple[set[str], str]:
    """(the module names an importer may spell tests/test_x.py by, its stem)."""
    p = Path(target).with_suffix("")
    return {p.name, ".".join(p.parts)}, p.name


def taken(text: str, target: str, within: frozenset[str] | None = None):
    """The names an importer's TEXT takes from module TARGET (a frozenset), or None (all of it).

    WITHIN, when the importer is itself a slice, is the module-level names that slice holds: a
    module-level import binding none of them hands nothing on (pytest still loads TARGET, whose
    import-time statements the empty slice keeps); an import inside a function is the function's.
    No static import of TARGET at all (a name built at run time) is the whole module."""
    tree = _parse(text)
    if tree is None:
        return None
    mods, stem = _spellings(target)
    top = {id(n) for n in tree.body}
    out: set[str] = set()
    modules: set[str] = set()  # names TARGET is bound to as a module
    seen = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module in mods or (node.level and node.module == stem):
                pairs = [(a.name, a.asname or a.name) for a in node.names]
                as_module = False
            elif node.module in {"tests", None} and any(a.name == stem for a in node.names):
                pairs = [(a.name, a.asname or a.name) for a in node.names if a.name == stem]
                as_module = True
            else:
                continue
        elif isinstance(node, ast.Import):
            pairs = [(a.name, a.asname or a.name) for a in node.names if a.name in mods]
            if any("." in name and name == local for name, local in pairs):
                return None  # `import tests.test_x`: bound as `tests`, used as `tests.test_x.y`
            as_module = True
        else:
            continue
        if not pairs:
            continue
        seen = True
        for name, local in pairs:
            if name == "*":
                return None
            if within is not None and id(node) in top and local not in within:
                continue
            (modules if as_module else out).add(local if as_module else name)
    if not seen:
        return None
    if modules:
        parents = {id(c): p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and n.id in modules:
                parent = parents.get(id(n))
                if not (isinstance(parent, ast.Attribute) and parent.value is n):
                    return None  # the module handed on, patched or iterated: all of it
                out.add(parent.attr)
    return frozenset(out)
