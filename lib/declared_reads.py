"""lib/declared_reads.py -- what a test reads from the tree at run time (GOH.SELECTAUDIT).

tools/affected_tests.py chooses a test that NAMES a changed path (every part quoted). A test that
reads a whole DIRECTORY names none of the files in it: tests/test_swift_gate_baseline.py copies
`REPO_ROOT / "tests" / "fixtures" / "swift_baseline_project"` whole, so a change to a file under
that fixture chose nothing but the tree readers (2026-11-17, measured on main's selector). Here
the reads are found two ways, and a change to a path one of them covers chooses the file whole:

    INFERRED   a directory spelled as REPO_ROOT (or ROOT, GOH_DIR) and literal parts, directly or
               through a module-level name bound to that spelling, that is the receiver of
               .glob/.rglob/.iterdir (`SRC.glob("*.rs")` -> `crates/goh/src/*.rs`) or the first
               argument of copytree/listdir/walk/scandir (the whole directory), or the left of
               a join with a non-literal part (`FIXTURES / f"{stem}.log"`: some file under it)
    DECLARED   a module-level `TREE_READS = ("pattern", ...)`: what no spelling gives away -- a path
               joined from a variable (`REPO_ROOT / rel`), a read made by a helper. A pattern is
               fnmatch-style from the root (`*` crosses `/`); one with no wildcard is that file,
               or every file under it when it ends in `/`.

THROUGH WHAT A TEST REACHES (GOH.SELECTLOCKORDER). The reads above are a test's own; a test also
reads what the code it drives reads. Along a chain of tests/ files (lib/test_closure.py's reach):

    a tests/ HELPER's reads, spelled or declared, are the test's: tests/test_proven_made.py runs
               tests/test_proven.py's fixture, which copies gates/ checks/ lib/ tui/ whole
    a SCANNER's declared TREE_READS (a checks/ or other non-test module: what it reads of the tree
               it is pointed at) are the test's when the file reaching it points it at the root
               -- passes REPO_ROOT (or str(REPO_ROOT)) to a call, or runs with cwd=REPO_ROOT.
               tests/test_check_lock_order.py runs checks/check_lock_order.py over REPO_ROOT, which
               reads every lib/*.py; keyed on lib/lock_order.tsv alone it missed GOH.LANDLOGPRUNE's
               raw flock in a new lib/state_bounds.py and the batch gate went red (2026-11-19)

A scanner pointed only at a fixture reads the fixture, so it inherits nothing; a module reached
deeper (lib/ code a tool imports) declares for no test: its reads are the test's own business.

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
MAY_READ = re.compile(
    r"\.(?:r?glob|iterdir)\(|copytree|listdir|walk\(|scandir|TREE_READS|[A-Z]\w* / (?![\"'])"
)


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
    for node in ast.walk(tree):  # `FIXTURES / name`: some file under a spelled directory
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            right = node.right
            literal = isinstance(right, ast.Constant) and isinstance(right.value, str)
            if not literal and (d := where(node.left)):
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


def _root_arg(node: ast.AST) -> bool:
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "str" and node.args:
        node = node.args[0]
    return isinstance(node, ast.Name) and node.id in ROOTS


# Calls that take the root as a LOCATION -- an import path, an env value, a parent -- not as the
# tree to judge: `sys.path.insert(0, str(REPO_ROOT))` points nothing at it.
LOCATING = frozenset({"insert", "append", "setenv", "setitem", "chdir", "relative_to", "Path",
                      "hermetic_env", "print_safe", "join", "relpath", "is_relative_to",
                      "str"})  # fmt: skip


@functools.lru_cache(maxsize=4096)
def drives_root(text: str) -> bool:
    """Whether the file points something at the root: REPO_ROOT (or str(REPO_ROOT)) as a call's
    positional argument (a LOCATING call's excepted), an argv word, or cwd= -- never a path joined
    under it (`REPO_ROOT / "x"`) nor an env value (`GOH_DIR=str(REPO_ROOT)`)."""
    tree = _parse(text) if re.search(r"\b(?:REPO_ROOT|ROOT|GOH_DIR|GOH_ROOT)\b", text) else None
    for node in ast.walk(tree) if tree else ():
        if isinstance(node, (ast.List, ast.Tuple)) and any(_root_arg(e) for e in node.elts):
            return True
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name not in LOCATING and any(_root_arg(a) for a in node.args):
                return True
            if any(k.arg == "cwd" and _root_arg(k.value) for k in node.keywords):
                return True
    return False


def _chains(graph, test: str):
    """(file, the file reaching it) for each file TEST reaches along a chain of tests/ files."""
    for node, chain in graph.reach(test)[0].items():
        parents = (test, *chain[:-1])
        if node in graph.texts and all(p.startswith("tests/") for p in parents):
            yield node, parents[-1]


def _seen(graph, test: str, node: str) -> str:
    """`node`'s source as TEST reaches it: the slice of a test module it imports names from
    (lib/test_slice.py, GOH.SELECTTESTIMPORTS), else the whole file."""
    seen = getattr(graph, "seen", None)
    return seen(test, node) if seen else graph.texts.get(node, "")


def driven(graph, test: str) -> list[str]:
    """The non-test files TEST's chains point at the root (module doc): the scanners whose
    declared TREE_READS are its reads."""
    return [
        node
        for node, by in _chains(graph, test)
        if not node.startswith("tests/") and drives_root(_seen(graph, test, by))
    ]


# A scanner that lists the tracked tree (itself or through a checks/ helper): what it reads of the
# root no spelling shows, so a test pointing it there needs its TREE_READS (tests/
# test_declared_reads.py's ratchet, gates/undeclared_scanners.txt).
LISTS = re.compile(r"ls-files|ls_files|tree_files\(|os\.walk\(|\.rglob\(")


def lists_tree(graph, rel: str) -> bool:
    return any(
        LISTS.search(graph.code(f))
        for f in (rel, *(d for d in graph.imports(rel) if d.startswith("checks/_")))
    )


def through(graph, test: str, root: Path) -> list[tuple[str, str]]:
    """(pattern, the file it is read through) for what TEST reads by what it drives (module doc):
    the reads of each tests/ helper on a chain of tests/ files, and the declared TREE_READS of
    each scanner such a chain's last file points at the root."""
    out = [(p, n) for n, _ in _chains(graph, test) if n.startswith("tests/")
           for p in reads(_seen(graph, test, n), root)]  # fmt: skip
    for node in driven(graph, test):
        if "TREE_READS" in graph.texts[node]:
            out += [(p, node) for p in declared(graph.texts[node])]
    return out


def choose(root: Path, tests: dict[str, str], changed: list[str], chosen: dict[str, str],
           graph=None) -> None:  # fmt: skip
    """Add to CHOSEN (whole) each test whose reads cover a changed path, with why: its own reads,
    and with GRAPH (lib/test_closure.py's Closure) the reads through what it drives."""
    for rel, text in tests.items():
        if rel in chosen:
            continue
        own = [(p, "") for p in reads(text, root)]
        for pattern, via in own + (through(graph, rel, root) if graph is not None else []):
            if hit := next((p for p in changed if matches(pattern, p)), None):
                chosen[rel] = f"reads {pattern} ({hit})" + (f" through {via}" if via else "")
                break
