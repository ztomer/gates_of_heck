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

A second question, the same callees (4.2): a spawn with a `cwd` runs in ANOTHER directory, and a
child that inherits the hook's environment inherits the hook's repository -- GIT_DIR and
GIT_INDEX_FILE name the real one, so a house check run inside a fixture lists the real tree. No
argv census sees that: the argv names a checker. So a spawn with a `cwd` states its environment,
and one that is the caller's own (`os.environ`, a copy of it, or none given) is a finding unless
the file or directory declares that it may inherit, with its reason (the gated repo itself; a
process that drops git's local variables at its start). A named environment built elsewhere
(`env=env`) is trusted: what the census cannot see, said here.

The result carries how many files it READ, so a census aimed at the wrong directory says it read
none instead of reporting a clean tree, and the exemptions no site matched any more, so a
declaration cannot outlive its call.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field

from _gitutil import tree_files

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
    fixture_sites: list = field(
        default_factory=list
    )  # spawns in another directory with the hook's env
    stale_inherit: list = field(
        default_factory=list
    )  # inherit declarations with no such spawn under them


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


def _inherits(env) -> bool:
    """Whether an `env=` expression is the caller's own environment: `os.environ`, or a copy or
    extension of it (`dict(os.environ, ...)`, `{**os.environ, ...}`, `os.environ.copy()`)."""
    if env is None or (isinstance(env, ast.Constant) and env.value is None):
        return True
    if isinstance(env, ast.Dict):
        return any(k is None and _is_environ(v) for k, v in zip(env.keys, env.values))
    if isinstance(env, ast.Call):
        func = env.func
        if isinstance(func, ast.Attribute) and func.attr == "copy":
            return _is_environ(func.value)
        if getattr(func, "id", None) == "dict":
            return any(_is_environ(a) for a in env.args)
    return _is_environ(env)


def _is_environ(node) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and getattr(node.value, "id", None) == "os"
    )


def directory_spawns(text: str) -> list:
    """(line, source) of every raw subprocess spawn in `text` that runs in another directory
    (`cwd=`) with the caller's environment. Raises SyntaxError as `git_spawns` does."""
    tree = ast.parse(text)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or callee(node) not in RAW_CALLEES:
            continue
        kw = {k.arg: k.value for k in node.keywords}
        if "cwd" in kw and _inherits(kw.get("env")):
            found.append(
                (node.lineno, " ".join((ast.get_source_segment(text, node) or "").split()))
            )
    return sorted(found)


def _declared(rel: str, inherit) -> str | None:
    """The `inherit` key covering `rel`: the file itself, or a directory above it."""
    for key in inherit:
        if rel == key or rel.startswith(key.rstrip("/") + "/"):
            return key
    return None


def census(root, sources, exempt=None, prune=PRUNE, inherit=None) -> Census:
    """Raw git spawns in the `*.py` under each of `sources` (relative to `root`), minus the files
    `exempt` declares ({root-relative path: reason}); and the spawns in another directory with the
    caller's environment, minus the files and directories `inherit` declares (the same shape).

    A source directory that does not exist reads nothing; the caller judges `read`."""
    declared = dict(exempt or {})
    inheriting = dict(inherit or {})
    out = Census()
    spawning = set()
    used = set()
    # git's own listing (tracked plus untracked-not-ignored), never a disk walk: a walk descends
    # build output and vendored trees git already ignores. Outside a work tree (a fixture),
    # tree_files falls back to a walk that prunes them.
    tops = tuple(s.strip("/") + "/" for s in sources)
    for rel in tree_files(str(root)):
        parts = rel.split("/")
        if not rel.endswith(".py") or not rel.startswith(tops) or prune.intersection(parts):
            continue
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as fh:
                text = fh.read()
            sites, elsewhere = git_spawns(text), directory_spawns(text)
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as e:
            out.unreadable.append((rel, f"{type(e).__name__}: {e}"))
            continue
        out.read += 1
        cover = _declared(rel, inheriting)
        if elsewhere and cover:
            used.add(cover)
        elif elsewhere:
            out.fixture_sites.extend(Site(rel, line, src) for line, src in elsewhere)
        if sites:
            spawning.add(rel)
        if rel in declared:
            continue
        out.sites.extend(Site(rel, line, src) for line, src in sites)
    out.stale = sorted(rel for rel in declared if rel not in spawning)
    out.stale_inherit = sorted(key for key in inheriting if key not in used)
    return out
