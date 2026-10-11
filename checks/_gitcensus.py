"""The house census of where a git argv may be built (BACKLOG 4.1).

A repo that routes its git calls through one seam (a `run_git`, a `git_env.run`) proves the
routing with a census: every OTHER place a git process is spawned is either declared, with its
reason, or a finding. Four consumers wrote that census by hand, three different ways, and two of
them were wrong in a way that passes:

  * matching the ARGV (a list whose first element is "git") flags the seam's own calls --
    `git_env.run(["git", ...])` is exactly the call it exists to approve -- so the gate can never
    go green, and a gate that can never go green reports nothing (measured, CadGoose2);
  * approving a FILE that mentions the seam once clears a raw `["git", ...]` beside it.

So this census asks about the CALLEE, per call: a spawn is raw when it goes to `subprocess`
itself (`subprocess.run`, a bare `run` imported from it, `Popen`, ...) and any string constant in
its arguments names git. A call through anything else is a wrapper's business. What it cannot
see, said here rather than discovered: a git call assembled at runtime out of parts, one made
from a shell script, and one through `os.system`/`os.exec*`; a consumer lists its shell sites
itself.

The result carries how many files it READ, so a census aimed at the wrong directory says it read
none instead of reporting a clean tree, and the exemptions no site matched any more, so a
declaration cannot outlive its call.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field

SPAWNERS = frozenset({"run", "call", "check_call", "check_output", "Popen"})
# A call is RAW only when it goes to subprocess itself, qualified or imported bare.
RAW_CALLEES = frozenset({"subprocess." + n for n in SPAWNERS} | SPAWNERS)
PRUNE = frozenset(
    {".git", ".build", "build", "target", "third_party", "node_modules", "__pycache__", ".venv"}
)


@dataclass(frozen=True)
class Site:
    """One raw git spawn: the root-relative file, its line, and the call's source on one line."""

    path: str
    line: int
    source: str


@dataclass
class Census:
    sites: list = field(default_factory=list)  # undeclared raw git spawns, as Site
    read: int = 0  # Python files parsed under the sources
    unreadable: list = field(default_factory=list)  # (rel, why): not Python this census can read
    stale: list = field(default_factory=list)  # declared files that no longer spawn git raw


def callee(node: ast.Call) -> str:
    """The called function's name as written: `subprocess.run`, `run`, `git_env.run`."""
    func = node.func
    if isinstance(func, ast.Attribute):
        base = getattr(func.value, "id", None)
        return f"{base}.{func.attr}" if base else func.attr
    return getattr(func, "id", "") or ""


def _names_git(arg) -> bool:
    for sub in ast.walk(arg):
        if (
            isinstance(sub, ast.Constant)
            and isinstance(sub.value, str)
            and sub.value.split()[:1] == ["git"]
        ):
            return True
    return False


def git_spawns(text: str) -> list:
    """(line, source) of every raw subprocess spawn in `text` whose arguments name git.

    Raises SyntaxError when `text` is not Python: a file the census cannot parse is reported,
    never read as clean."""
    tree = ast.parse(text)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or callee(node) not in RAW_CALLEES:
            continue
        if any(_names_git(a) for a in [*node.args, *(k.value for k in node.keywords)]):
            src = " ".join((ast.get_source_segment(text, node) or "").split())
            found.append((node.lineno, src))
    return sorted(found)


def census(root, sources, exempt=None, prune=PRUNE) -> Census:
    """Raw git spawns in the `*.py` under each of `sources` (relative to `root`), minus the files
    `exempt` declares ({root-relative path: reason}).

    A source directory that does not exist reads nothing; the caller judges `read`."""
    declared = dict(exempt or {})
    out = Census()
    spawning = set()
    for rel_dir in sources:
        top = os.path.join(root, rel_dir)
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = sorted(d for d in dirnames if d not in prune)
            for name in sorted(filenames):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, root)
                try:
                    with open(path, encoding="utf-8") as fh:
                        sites = git_spawns(fh.read())
                except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as e:
                    out.unreadable.append((rel, f"{type(e).__name__}: {e}"))
                    continue
                out.read += 1
                if sites:
                    spawning.add(rel)
                if rel in declared:
                    continue
                out.sites.extend(Site(rel, line, src) for line, src in sites)
    out.stale = sorted(rel for rel in declared if rel not in spawning)
    return out
