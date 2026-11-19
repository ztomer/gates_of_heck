"""lib/test_closure.py: the dependency graph tools/affected_tests.py chooses a land's tests by.

Split out of tools/affected_tests.py (GOH.PREPARITY, at its 500-line cap) with no change of rule:
file -> what it imports or sources (lib/source_closure.py, the one dependency reader), the
scripts a test or tests/ or checks/ helper names by path, and what a reached SHELL script runs by a
variable-rooted path (`uses`). `Closure.base()` is what pytest loads before every test.
Pinned by tests/test_affected_tests.py.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

try:
    from checks import _prose_mask
    from lib import source_closure
except ImportError:  # lib/ itself on the path
    import source_closure  # type: ignore[no-redef]

    from checks import _prose_mask

# The one naming rule (lib/source_closure.py): the `named` edge of a proven step's closure too.
_names_path = source_closure.names_path

# A path rooted at a variable in a shell script's code (`"$ROOT/lib/x.py"`, `${HERE}/../y.sh`): the
# script resolves it to USE the file -- run, source or hand it on -- where a bare path in a message
# is a mention (GOH.PREPARITY: gates/_rust_proven.sh runs `$PROVEN_GOH_ROOT/lib/gate_identity.py`).
ROOTED = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?((?:/[A-Za-z0-9_.-]+)+)")

# Where a test's runner finds a module: beside the file, the root, and these (tools/ is on the
# sys.path of the tests that import a tool).
PY_DIRS = (*source_closure.PY_DIRS, "tools")
# Read by the suite's entry, every test (base): gate.yml and the local actions its steps use, whose
# GITHUB_ENV writes are the suite's environment too (tests/ci_shape.py `reads`, GOH.CIWALL2).
ENTRY_READS = (
    ".github/workflows/gate.yml",
    ".github/actions/goh-setup/action.yml",
    ".github/actions/goh-floors/action.yml",
)
SKIP_DIRS = {".git", "target", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}


def _files(root: Path) -> dict[str, str]:
    """{path: text} of every Python and shell file under `root` (bin/ commands included)."""
    out: dict[str, str] = {}
    for current, dirnames, names in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(names):
            p = Path(current, name)
            if p.suffix in (".py", ".sh", ".bash") or p.parent.name == "bin":
                out[p.relative_to(root).as_posix()] = p.read_text(
                    encoding="utf-8", errors="replace"
                )
    return out


class Closure:
    """The dependency graph of a tree, built lazily: file -> what it imports or sources
    (lib/source_closure.py), and file -> the scripts its text names."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.texts = _files(root)
        self.scripts = [p for p in self.texts if not Path(p).name.startswith("test_")]
        self._edges: dict[str, list[str]] = {}
        self._named: dict[str, list[str]] = {}
        self._uses: dict[str, list[str]] = {}
        self._names: dict[tuple[str, str], bool] = {}
        self._base: set[str] | None = None
        self._code: dict[str, str] = {}
        self._reach: dict[str, tuple[dict[str, tuple[str, ...]], set[str]]] = {}

    def code(self, rel: str) -> str:
        """`rel`'s code with its prose blanked (checks/_prose_mask.py): a docstring or comment
        that mentions a script is no run of it."""
        if rel not in self._code:
            text = self.texts.get(rel, "")
            mask = (
                _prose_mask.python_code_only if rel.endswith(".py") else _prose_mask.shell_code_only
            )
            self._code[rel] = mask(text)
        return self._code[rel]

    def names(self, rel: str, path: str) -> bool:
        """Whether the code of `rel` names `path`."""
        key = (rel, path)
        if key not in self._names:
            self._names[key] = (
                Path(path).name in self.texts.get(rel, "")  # the cheap test first
                and _names_path(self.code(rel), path)
            )
        return self._names[key]

    def imports(self, rel: str) -> list[str]:
        """The files `rel` imports or sources that exist in the tree, in order."""
        if rel not in self._edges:
            found, _ = source_closure.direct(self.root / rel, self.root, self.root, PY_DIRS)
            edges = []
            for dep in found:
                try:
                    edge = dep.relative_to(self.root).as_posix()
                except ValueError:
                    continue  # outside the tree
                if edge != rel and edge not in edges:
                    edges.append(edge)
            self._edges[rel] = edges
        return self._edges[rel]

    def uses(self, rel: str) -> list[str]:
        """The tree's scripts a SHELL file's code names by a variable-rooted path (ROOTED), resolved
        from the root or from the file's own directory: what it runs, besides what it sources."""
        if rel not in self._uses:
            found: list[str] = []
            if rel.endswith((".sh", ".bash")) or Path(rel).parent.name == "bin":
                here = Path(rel).parent
                for m in ROOTED.finditer(self.code(rel)):
                    tail = m.group(1).lstrip("/")
                    for spelled in (os.path.normpath(here / tail), os.path.normpath(tail)):
                        cand = Path(spelled).as_posix()
                        if cand != rel and cand in self.texts and cand not in found:
                            found.append(cand)
                            break
            self._uses[rel] = found
        return self._uses[rel]

    def named(self, rel: str) -> list[str]:
        """The scripts `rel` names by path (it runs them): tests/ and checks/ files only."""
        if rel not in self._named:
            runs = rel.startswith(("tests/", "checks/"))
            self._named[rel] = [
                s for s in (self.scripts if runs else []) if s != rel and self.names(rel, s)
            ]
        return self._named[rel]

    def base(self) -> set[str]:
        """tests/conftest.py and all it reaches: what pytest loads before EVERY test (the reference
        kits, suite home, the gate libraries they copy). A change in it is in every test's closure,
        so the closure cannot choose among tests by it (it chose 443 of 464 files); such a change
        is the full gate's, and the tests that import or name the changed file are chosen by the
        diff as before."""
        if self._base is None:
            self._base = set()
            if "tests/conftest.py" in self.texts:
                reached = self.reach("tests/conftest.py", (), runs=False)[0]
                self._base = {"tests/conftest.py", *reached}
            # ... and what it READS at entry: a land's suite takes its environment from the
            # workflow (tests/ci_shape.py, GOH.CIPARITY), which no import or spelling reaches.
            self._base |= {p for p in ENTRY_READS if (self.root / p).is_file()}
        return self._base

    def reach(
        self, test: str, skip: set[str] | None = None, runs: bool = True
    ) -> tuple[dict[str, tuple[str, ...]], set[str]]:
        """({file: the chain from the test to it}, the files among them it IMPORTS rather than
        runs), for everything the test depends on, nearest first, the base excepted (`skip` None)
        or the files in `skip`. Imports are followed at any depth; a name is followed only out of
        a test or a tests/ or checks/ helper, and the script it names brings its imports and
        sources, not the scripts that script mentions in turn. With `runs`, a shell script's
        variable-rooted uses are edges too (Closure.uses), a base file among them included: pytest
        loading it is no run of its command line, which the script's process is (GOH.PREPARITY:
        lib/gate_identity.py, imported by tests/_land_verdicts.py, run by gates/_rust_proven.sh)."""
        if skip is None and runs and test in self._reach:  # a test's own closure, asked twice
            return self._reach[test]  # a land: by the closure rule, then by declared_reads
        memo = skip is None and runs
        skip = self.base() if skip is None else skip
        chains: dict[str, tuple[str, ...]] = {}
        read: set[str] = set()  # reached by imports alone: their own code is read, so it counts
        # (file, chain, reached by imports alone, a use edge on the chain already)
        todo: list[tuple[str, tuple[str, ...], bool, bool]] = [(test, (), True, False)]
        while todo:
            rel, chain, by_import, used = todo.pop(0)

            def visit(dep: str, by_import: bool, used: bool, chain=chain) -> None:
                if dep == test or dep in skip:
                    return
                if dep not in chains or (by_import and dep not in read):
                    chains.setdefault(dep, (*chain, dep))
                    if by_import:
                        read.add(dep)
                    todo.append((dep, chains[dep], by_import, used))

            for dep in self.imports(rel):
                visit(dep, by_import, used)
            # A shell script's runs, never a mention -- ONE use per chain: a used script's sources
            # come with it, its own runs do not (followed, land.sh -> gate_profile.sh ->
            # push_gate.sh -> install.sh -> ... chose ~100 more files a land).
            for dep in self.uses(rel) if runs and not used else ():
                if dep in skip and dep != test:
                    chains.setdefault(dep, (*chain, dep))  # run, not loaded: counted, not followed
                else:
                    visit(dep, False, True)
            if by_import:
                for dep in self.named(rel):
                    visit(dep, False, used)
        if memo:
            self._reach[test] = (chains, read)
        return chains, read


def transitive(graph: Closure, test: str, changed: list[str]) -> str | None:
    """Why `test` is chosen by something it reaches (not by its own text), or None."""
    reached, read = graph.reach(test)
    for path in changed:
        if path in reached:
            chain = reached[path]
            return f"reaches {path}" + (f" through {' -> '.join(chain)}" if len(chain) > 1 else "")
    for node, chain in reached.items():
        if node not in read:
            continue  # a script the test runs: what it sources counts, what it mentions does not
        for path in changed:
            if path != node and graph.names(node, path):
                via = node + (f" through {' -> '.join(chain)}" if len(chain) > 1 else "")
                return f"reaches {via}, which names {path}"
    return None
