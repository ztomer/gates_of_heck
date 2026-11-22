#!/usr/bin/env python3
"""The static half of the lock order (GOH.LOCKORDER): goh's held-while-acquiring graph sorts.

    checks/check_lock_order.py [ROOT]     # exit 0 and the order, or 1 naming each finding

THE INCIDENT (2026-11-15). A ship held the land lock and waited for the load slot; a land held the
load slot (it ran inside `tools/load.py`) and waited for the land lock. Each order was written down
in a different place, and nothing put the two side by side. This check does, from the code:

  1. EVERY ACQUISITION GOES THROUGH THE HELPER. A raw `fcntl.flock`/`fcntl.lockf` (Python, or the
     inline Python a shell runs) outside lib/lock_order.py, or a Rust file lock with no
     `lock-order: NAME` mark on its line, is red: a lock the table does not see cannot be ordered.
  2. THE TABLE NAMES EXACTLY THE ACQUIRERS. Every helper call names a lock of lib/lock_order.tsv,
     every file the table lists for a lock acquires it, and every file that acquires it is listed
     (a stale entry is as red as a missing one).
  3. THE GRAPH. In each code file, a lock acquired at line i is held for what follows it: a later
     acquisition of M is the edge L -> M, and a later reference to another goh file G (a script
     path, `import G`, a `.gatesrc` key whose command names G, or `X ... -- G` where X holds a
     lock for the command it runs) adds L -> every lock G may take, transitively. The locks are
     sorted topologically; a cycle is red, named lock by lock with the line behind each edge; an
     edge against the table's ranks is red too; a lock re-taken under itself is red unless the
     table marks it re-entrant (its own rule lets a holder's child through).

  4. A `goh SUB` call is the house script SUB runs, and a detached call (`--detach`, `setsid`,
     `start_new_session=True`) forms no edge (checks/_lock_order_calls.py, GOH.LOCKORDERBIN2).

The graph over-approximates (a reference after an acquisition counts as made under it) and never
under-approximates within goh's tree; invocations outside the tree (a coordinator's command line)
are the runtime helper's to refuse. Calibrated by tests/test_check_lock_order.py, which re-plants
the 2026-11-15 order and watches it go red.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout: run as is
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import ast
import itertools
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "lib"), str(ROOT)]
import lock_order  # noqa: E402 -- lib/ is on the path from the line above

from tui.lib import counted, err, ok  # noqa: E402 -- and the root

sys.path.insert(0, str(ROOT / "checks"))
import _lock_order_calls as calls  # noqa: E402 -- bin/goh and detached calls (GOH.LOCKORDERBIN2)

# What `load` reads of the tree it is pointed at: a test running this check over goh's root is
# chosen by a change under any of them (lib/declared_reads.py, GOH.SELECTLOCKORDER).
# (bin/ was listed too, and goh tracks nothing there: the installed binary is no source.)
TREE_READS = (".gatesrc", "crates/", "gates/", "hooks/", "lib/", "scripts/", "tools/", "checks/")
SCANNED = TREE_READS[1:]
HELPERS = ("lib/lock_order.py", "lib/lock_order.sh", "checks/check_lock_order.py")
SITE = re.compile(
    r"""(?:lock_order\.(?:flock|check|refusal|holding|with_held)\(\s*(?:[^"']*?,\s*)?["']"""
    r"""|_order_check\(\s*["']|goh_lock_(?:take|check|mark)\s+|lock-order:\s*)([a-z_]+)"""
)
MARK = re.compile(
    r"""(?:lock_order\.(?:holding|with_held)\(\s*["']|goh_lock_(?:take|mark)\s+)([a-z_]+)"""
)
RAW_PY = re.compile(r"\bfcntl\.(?:flock|lockf)\s*\(")
RAW_RS = re.compile(r"\.(?:try_)?lock(?:_shared)?\(\)")
RS_FILE = re.compile(r"\bFile::(?:open|create)\b|\bOpenOptions\b")
RUNS = re.compile(r"lock-order-runs:((?:\s+GOH_[A-Z0-9_]+)+)")
KEY = re.compile(r"^(GOH_[A-Z0-9_]+)=(.*)$")


@dataclass
class Tree:
    root: Path
    files: dict[str, list[str]]  # relative path -> lines
    sites: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    refs: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    marks: dict[str, set[str]] = field(default_factory=dict)  # held for its children


def _tracked(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True,
                         text=True, check=True, stdin=subprocess.DEVNULL).stdout  # fmt: skip
    return [p for p in out.split("\0") if p]


def _code(path: str, lines: list[str]) -> list[tuple[int, str]]:
    """(line number, text) of the lines that are code: comments and Python docstrings dropped."""
    out, in_doc = [], False
    py, rs = path.endswith(".py"), path.endswith(".rs")
    for n, line in enumerate(lines, 1):
        s = line.strip()
        if py and s.count('"""') % 2 == 1:
            in_doc = not in_doc
            continue
        if in_doc or (py and s.startswith('"""')):
            continue
        if s.startswith("//") and rs:
            out.append((n, line)) if "lock-order:" in s else None
            continue
        if s.startswith("#") and not rs:
            continue
        out.append((n, line))
    return out


def load(root: Path) -> Tree:
    files = {}
    for rel in _tracked(root):
        if rel == ".gatesrc" or (rel.startswith(SCANNED) and rel.endswith((".py", ".sh", ".rs"))):
            path = root / rel
            if path.is_file():
                files[rel] = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return Tree(root, files)


def raw_findings(tree: Tree) -> list[str]:
    out = []
    for rel, lines in tree.files.items():
        if rel in HELPERS:
            continue
        code = _code(rel, lines)
        for n, line in code:
            if not rel.endswith(".rs") and RAW_PY.search(line):
                out.append(f"{rel}:{n}: a raw flock -- take it through lib/lock_order.py "
                           "(`lock_order.flock(fd, NAME)`) or lib/lock_order.sh "
                           "(`goh_lock_take`)")  # fmt: skip
        if rel.endswith(".rs") and any(RS_FILE.search(t) for _, t in code):
            for n, line in enumerate(lines, 1):
                if RAW_RS.search(line) and "lock-order:" not in line and "Mutex" not in line:
                    near = "\n".join(lines[max(0, n - 4) : n])
                    if RS_FILE.search(near):
                        out.append(f"{rel}:{n}: a Rust file lock with no `// lock-order: NAME` "
                                   f"mark naming its lib/lock_order.tsv entry")  # fmt: skip
    return out


def _index(tree: Tree) -> dict[str, list[str]]:
    """How a goh file is referred to -> the files it may mean: its basename (a script path) and,
    for Python, its module name (an import)."""
    names: dict[str, list[str]] = defaultdict(list)
    for rel in tree.files:
        if rel.endswith((".py", ".sh")) and rel not in HELPERS:
            names[Path(rel).name].append(rel)
    for name, scripts in calls.house_subcommands(tree.root, set(tree.files)).items():
        names[name].extend(scripts)
    return names


_STRING = re.compile(r"\"(?:[^\"\\\\]|\\\\.)*\"|'(?:[^'\\\\]|\\\\.)*'")


def _mentions(text: str, names: dict[str, list[str]]) -> set[str]:
    """The goh files TEXT refers to: a path (`tools/land.sh`: the files it ends with), a bare
    script name (`land.sh`: every file of that name), or a Python import of a module."""
    hit = set()
    for token in re.findall(r"[A-Za-z0-9_./-]*[A-Za-z0-9_]\.(?:sh|py)\b", text):
        name, rest = Path(token).name, token.lstrip("./")
        cands = names.get(name, ())
        if "/" in rest:
            cands = [c for c in cands if c.endswith(rest) or rest.endswith(c)] or []
        hit.update(cands)
    code = _STRING.sub('""', text)  # a word in a string is no import (GOH.LOCKORDERWORD)
    for mod in re.findall(r"import_module\(\s*[\"']([A-Za-z_]\w*)", text):
        hit.update(names.get(f"{mod}.py", ()))
    for mod in re.findall(r"(?:^\s*import|^\s*from)\s+([A-Za-z_]\w*)", code):
        hit.update(names.get(f"{mod}.py", ()))
    for mod in re.findall(r"\b([a-z_][a-z0-9_]*)\.[A-Za-z_]\w*\(", code):  # a module's function
        hit.update(names.get(f"{mod}.py", ()))
    return hit


_TRAILING = re.compile(r"\s#(?!!)[^\"']*$")


def _uncommented(rel: str, line: str) -> str:
    """LINE as code that may run something: a trailing comment dropped, and every string literal
    with a space in it (a message, advice, a pattern -- an argv word has none) blanked."""
    if not rel.endswith(".rs"):
        line = _TRAILING.sub("", line)
    out, i = [], 0
    while i < len(line):  # left to right, so a closing quote never opens the next literal
        q = line[i]
        if q not in "\"'":
            out.append(q)
            i += 1
            continue
        j = i + 1
        while j < len(line) and line[j] != q:
            j += 2 if line[j] == "\\" else 1
        body = line[i + 1 : j]
        out.append(q * 2 if any(ch.isspace() for ch in body) else line[i : j + 1])
        i = j + 1
    return "".join(out)


def scan(tree: Tree) -> list[str]:
    """Fill tree.sites and tree.refs; return the table findings (rule 2)."""
    table = lock_order.table(tree.root / "lib" / "lock_order.tsv")
    names = _index(tree)
    keys: dict[str, set[str]] = {}
    for line in tree.files.get(".gatesrc", []):
        if m := KEY.match(line.strip()):
            keys[m.group(1)] = _mentions(m.group(2), names)
    keys = {k: v for k, v in keys.items() if v}
    alt = "|".join(map(re.escape, keys))
    # Read, not named: `$KEY`/`${KEY` in a shell, a quoted `"KEY"` (a lookup) in Python.
    keyed = re.compile(rf"""(?:\$\{{?|["'])({alt})\b""") if keys else None
    holders = {}  # a file that runs `X ... -- CMD` holding X's locks: filled after the sites
    out = []
    for rel, lines in tree.files.items():
        if rel in HELPERS or rel == ".gatesrc":
            continue
        sites, refs = [], []
        detached = calls.detached(rel, lines, _uncommented)
        for n, line in _code(rel, lines):
            for name in SITE.findall(line):
                if name not in table:
                    out.append(f"{rel}:{n}: lock {name!r} is not in lib/lock_order.tsv")
                    continue
                sites.append((n, name))
            if n in detached:
                continue  # a child of its own session holds none of this file's locks
            code = _uncommented(rel, line)
            for g in (_mentions(code, names) | calls.subcommands(rel, code, names)) - {rel}:
                refs.append((n, g))
            for key in " ".join(RUNS.findall(line)).split():  # a key's command, run from data
                refs.extend((n, g) for g in keys.get(key, set()) - {rel})
            if keyed and "GOH_" in line:  # a key's command runs where the key is read
                for key in keyed.findall(line):
                    refs.extend((n, g) for g in keys[key] - {rel})
        tree.sites[rel] = sites
        tree.refs[rel] = refs
        tree.marks[rel] = {m for _, line in _code(rel, lines) for m in MARK.findall(line)}
    for rel, sites in tree.sites.items():
        holders[rel] = {name for _, name in sites}
    for name, lock in table.items():
        acquirers = {rel for rel, names_ in holders.items() if name in names_}
        for rel in lock.files:
            if rel not in acquirers:
                out.append(f"lib/lock_order.tsv: '{name}' lists {rel}, which does not acquire it")
        for rel in sorted(acquirers - set(lock.files)):
            out.append(f"{rel}: acquires '{name}', but lib/lock_order.tsv does not list it there")
    return out


def _wrapped(tree: Tree, names: dict[str, list[str]]) -> list[tuple[str, int, str, str]]:
    """(file, line, wrapper, command file): every `X ... -- G` line, X and G goh files."""
    out = []
    for rel, lines in tree.files.items():
        for n, raw in _code(rel, lines):
            line = _uncommented(rel, raw)
            if " -- " not in line:
                continue
            head, _, tail = line.partition(" -- ")
            for x in _mentions(head, names):
                out.extend((rel, n, x, g) for g in _mentions(tail, names))
    return out


def _closure(tree: Tree, own: dict[str, set[str]], imports: bool = False) -> dict[str, set[str]]:
    """OWN per file, grown by every file it refers to (IMPORTS: only Python modules a Python file
    loads into its own process): the fixpoint."""
    got = {rel: set(own.get(rel, ())) for rel in tree.files}
    changed = True
    while changed:
        changed = False
        for rel, refs in tree.refs.items():
            for _, g in refs:
                if imports and not (rel.endswith(".py") and g.endswith(".py")):
                    continue
                if not got[g] <= got[rel]:
                    got[rel] |= got[g]
                    changed = True
    return got


def graph(tree: Tree) -> dict[tuple[str, str], str]:
    """The held-while-acquiring edges, each with the line behind it."""
    names = _index(tree)
    reach = _closure(tree, {rel: {n for _, n in s} for rel, s in tree.sites.items()})
    # What a wrapper holds for the command it runs: the locks marked held for children
    # (`lock_order.holding`/`with_held`, `goh_lock_take`/`goh_lock_mark`) in its own process: its
    # file and the modules it imports -- a script it runs holds its own, and they end with it.
    marks = _closure(tree, tree.marks, imports=True)
    edges: dict[tuple[str, str], str] = {}

    def add(h: str, hn: int, m: str, rel: str, n: int, via: str | None) -> None:
        if h == m and via is not None:
            return  # a lock re-taken only through another file: its own nesting rule's case
        where = f"{rel}:{hn} holds '{h}', then {rel}:{n} "
        where += f"takes '{m}'" if via is None else f"runs {via}, which takes '{m}'"
        edges.setdefault((h, m), where)

    for rel, sites in tree.sites.items():
        refs = tree.refs.get(rel, [])
        for hn, h in sites:
            held = _extent(rel, tree.files[rel], hn)
            for n, m in sites:  # the same name again in one file is the same acquisition
                if n in held and m != h:  # (a check, a try-lock, then the wait)
                    add(h, hn, m, rel, n, None)
            for n, g in refs:
                if n in held:
                    for m in reach[g]:
                        add(h, hn, m, rel, n, g)
    for rel, n, x, g in _wrapped(tree, names):
        for h in marks[x]:
            for m in reach[g]:
                if h != m:
                    edges.setdefault((h, m), f"{rel}:{n} runs {g} under {x}, which holds '{h}' "
                                             f"for its command; {g} takes '{m}'")  # fmt: skip
    return edges


def _extent(rel: str, lines: list[str], hn: int) -> set[int]:
    """The lines a lock taken at line HN is held over. A shell's: the rest of the file (an fd held
    to exit). Python's: the rest of the function it is taken in (the module, at top level), and
    every function of the same file called there, transitively."""
    if not rel.endswith(".py"):
        return set(range(hn, len(lines) + 1))
    try:
        parsed = ast.parse("\n".join(lines))
    except SyntaxError:
        return set(range(hn, len(lines) + 1))
    funcs: dict[str, tuple[int, int]] = {}
    inner = (0, len(lines))  # the innermost function around HN; the module when there is none
    for node in ast.walk(parsed):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            span = (node.lineno, node.end_lineno or node.lineno)
            funcs[node.name] = span
            if span[0] <= hn <= span[1] and span[0] >= inner[0]:
                inner = span
    enclosing = (hn, inner[1])
    out: set[int] = set()
    todo = [enclosing]
    seen: set[tuple[int, int]] = set()
    while todo:
        lo, hi = todo.pop()
        if (lo, hi) in seen:
            continue
        seen.add((lo, hi))
        out.update(range(lo, hi + 1))
        for n in range(lo, hi + 1):
            for name in re.findall(r"\b([A-Za-z_]\w*)\(", lines[n - 1] if n <= len(lines) else ""):
                if name in funcs:
                    todo.append(funcs[name])
    return out


def _cycle(edges: dict[tuple[str, str], str], reentrant: set[str]) -> list[str] | None:
    """One cycle of the graph (self-loops of re-entrant locks excepted), or None: a DFS."""
    succ: dict[str, list[str]] = defaultdict(list)
    for a, b in sorted(edges):
        if a == b and a in reentrant:
            continue
        succ[a].append(b)
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(v: str) -> list[str] | None:
        state[v] = 1
        stack.append(v)
        for w in succ[v]:
            if state.get(w) == 1:
                return [*stack[stack.index(w) :], w]
            if w not in state and (found := visit(w)):
                return found
        stack.pop()
        state[v] = 2
        return None

    for v in sorted(succ):
        if v not in state and (found := visit(v)):
            return found
    return None


def topo(edges: dict[tuple[str, str], str], locks: list[str]) -> list[str]:
    """The locks in an order every edge keeps (Kahn's; ties in LOCKS' order: deterministic)."""
    indeg = dict.fromkeys(locks, 0)
    succ: dict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        if a != b and b not in succ[a]:
            succ[a].add(b)
            indeg[b] = indeg.get(b, 0) + 1
            indeg.setdefault(a, 0)
    pos = {v: i for i, v in enumerate(locks)}
    ready = sorted((v for v, d in indeg.items() if d == 0), key=pos.get)
    out = []
    while ready:
        v = ready.pop(0)
        out.append(v)
        for w in sorted(succ[v]):
            indeg[w] -= 1
            if indeg[w] == 0:
                ready.append(w)
                ready.sort(key=pos.get)
    return out


def check(root: Path, plant: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
    """(findings, the topological order) for the tree at ROOT, each of PLANT's files (path -> text)
    read as if tracked there: the calibration's seam (tests/test_check_lock_order.py)."""
    tree = load(root)
    for rel, text in (plant or {}).items():
        tree.files[rel] = text.splitlines()
    table = lock_order.table(root / "lib" / "lock_order.tsv")
    findings = raw_findings(tree) + scan(tree)
    edges = graph(tree)
    reentrant = {n for n, lock in table.items() if lock.reentrant}
    if cyc := _cycle(edges, reentrant):
        steps = [f"    {a} -> {b}: {edges[(a, b)]}" for a, b in itertools.pairwise(cyc)]
        findings.append("a lock cycle -- " + " -> ".join(cyc) + ":\n" + "\n".join(steps))
    for (a, b), where in sorted(edges.items()):
        if a != b and a in table and b in table and table[a].rank >= table[b].rank:
            findings.append(f"'{a}' (rank {table[a].rank}) is held while '{b}' (rank "
                            f"{table[b].rank}) is taken, against lib/lock_order.tsv: "
                            f"{where}")  # fmt: skip
    return findings, topo(edges, sorted(table, key=lambda n: table[n].rank))


def main(argv: list[str]) -> int:
    root = Path(argv[0] if argv else ".").resolve()
    if not (root / "lib" / "lock_order.tsv").is_file():  # nothing to judge is never a pass
        err(f"lock order: no lib/lock_order.tsv under {root} -- this check judges goh's own locks")
        return 2
    findings, order = check(root)
    for f in findings:
        err(f"lock order: {f}")
    if findings:
        err(f"lock order: {counted(len(findings), 'finding')}; the table is lib/lock_order.tsv")
        return 1
    ok(f"lock order: the held-while-acquiring graph sorts: {' < '.join(order)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
