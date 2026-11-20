"""lib/test_closure.py: the dependency graph tools/affected_tests.py chooses a land's tests by.

Split out of tools/affected_tests.py (GOH.PREPARITY, at its 500-line cap) with no change of rule:
file -> what it imports or sources (lib/source_closure.py, the one dependency reader), the
scripts a test or tests/ or checks/ helper names by path, and what a reached SHELL script runs by a
variable-rooted path (`uses`). `Closure.base()` is what pytest loads before every test, the data
its code opens included (LOAD_READ); a base file a test imports itself brings its imports.
A test module another file imports is reached as the SLICE the importer uses (lib/test_slice.py,
GOH.SELECTTESTIMPORTS): its imports whole, but only the slice's code names or reads a path.
Pinned by tests/test_affected_tests.py.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

try:
    from checks import _prose_mask
    from lib import declared_reads, source_closure, test_slice
except ImportError:  # lib/ itself on the path
    import declared_reads  # type: ignore[no-redef]
    import source_closure  # type: ignore[no-redef]
    import test_slice  # type: ignore[no-redef]

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
# DATA the base's code opens while pytest runs -- at import, in a session or collection hook, as an
# xdist worker takes a share token -- which no import or spelling reaches, DECLARED on the code
# line that opens it by a trailing `load-read` comment naming the file from the root (GOH.
# BASEDATAREADS: a broken lib/lock_order.tsv crashed xdist for every test, and a change to it chose
# only its declared readers). In the base when its opener is, so a main move reruns and the gate
# carries nothing across it; a change chooses every test (`load_reads`). Checked against what a
# real pooled, sharded load opens, both ways, by tests/test_base_data_reads.py. A comment, not a
# table here: a path in this file's code is a named edge of every step closure that reaches it.
LOAD_READ = re.compile(r"^[ \t]*[^#\s][^#\n]*#\s*load-read:\s*([\w./-]+)[ \t]*$", re.MULTILINE)
ENTRY = "tests/conftest.py"  # what pytest loads before every test; the base is what it reaches
# Tests whose reads are not all known (lib/declared_reads.py's ratchet: a REPO_ROOT joined with a
# non-literal). Such a test reaches every test module it imports whole, never as a slice, and
# such a module is reached whole when the slice taken holds the unknown join: the wider closure
# is the only cover their unknown reads have (GOH.SELECTTESTIMPORTS: the replay of CLS.CIPROFILE's
# land broke gates/push_gate.sh, and tests/test_hold_gates.py, which copies
# `REPO_ROOT / "hooks" / name`, went red; the slice alone dropped it).
UNDECLARED = "gates/undeclared_reads.txt"
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


def _shell(rel: str) -> bool:
    """Whether `rel` is a shell script or a bin/ command: what a Python tool runs by path."""
    return rel.endswith((".sh", ".bash")) or Path(rel).parent.name == "bin"


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
        self._cuts: dict[str, dict[str, tuple[str, frozenset[str]]]] = {}
        self._sliced: dict[tuple[str, frozenset[str]], tuple[str, frozenset[str]] | None] = {}
        self._cut_code: dict[tuple[str, frozenset[str]], str] = {}
        self._below: dict[str, dict[str, tuple[str, ...]]] = {}
        self._loaded: dict[str, str] | None = None
        listed = root / UNDECLARED
        self.undeclared = (
            {ln.strip() for ln in listed.read_text().splitlines() if ln.strip() and ln[0] != "#"}
            if listed.is_file()
            else set()
        )

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
        from the root or from the file's own directory: what it runs, besides what it sources. A
        Python file outside tests/ and checks/ (whose `named` edges are these) uses the shell
        scripts its code names by path, when `reach` meets it RUN (reached by a name, never
        imported): tools/landq.py runs `ROOT / "tools" / "land.sh"` (GOH.SELECTTESTIMPORTS:
        tests/test_landq_batch.py, which runs tools/landq.py, went red on a broken
        tools/land.sh, which it reached before only through a test module's own tests)."""
        if rel not in self._uses:
            found: list[str] = []
            if rel.endswith(".py") and not rel.startswith(("tests/", "checks/")):
                found = [s for s in self.scripts if _shell(s) and s != rel and self.names(rel, s)]
            elif rel.endswith((".sh", ".bash")) or Path(rel).parent.name == "bin":
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

    def slice(self, rel: str, names: frozenset[str] | None) -> tuple[str, frozenset[str]] | None:
        """(source, held names) of test module `rel` for the `names` an importer takes from it
        (lib/test_slice.py), or None: the whole module."""
        if names is None or not test_slice.is_test_module(rel):
            return None
        key = (rel, names)
        if key not in self._sliced:
            cut = test_slice.slice_of(self.texts.get(rel, ""), names)
            if cut and rel in self.undeclared and declared_reads.dynamic(cut[0]):
                cut = None  # UNDECLARED: the slice holds the unknown read
            self._sliced[key] = cut
        return self._sliced[key]

    def _cut_named(self, rel: str, cut: tuple[str, frozenset[str]]) -> list[str]:
        """`named` for the slice `cut` of `rel`: the scripts its code names."""
        code = self._masked(rel, cut)
        return [s for s in self.scripts if s != rel and self._names_in(code, s)]

    def _masked(self, rel: str, cut: tuple[str, frozenset[str]]) -> str:
        key = (rel, cut[1])
        if key not in self._cut_code:
            self._cut_code[key] = _prose_mask.python_code_only(cut[0])
        return self._cut_code[key]

    @staticmethod
    def _names_in(code: str, path: str) -> bool:
        return Path(path).name in code and _names_path(code, path)

    def cut(self, test: str, node: str) -> tuple[str, frozenset[str]] | None:
        """The slice of `node` that `test` reaches (its own closure, `reach(test)`), or None when
        it reaches the whole file."""
        self.reach(test)
        return self._cuts.get(test, {}).get(node)

    def seen(self, test: str, node: str) -> str:
        """The source of `node` as `test` reaches it: the slice it uses, or the whole file."""
        cut = self.cut(test, node)
        return self.texts.get(node, "") if cut is None else cut[0]

    def names_seen(self, test: str, node: str, path: str) -> bool:
        """Whether the code of `node`, as `test` reaches it, names `path`."""
        cut = self.cut(test, node)
        return (
            self.names(node, path) if cut is None else self._names_in(self._masked(node, cut), path)
        )

    def base(self) -> set[str]:
        """tests/conftest.py and all it reaches: what pytest loads before EVERY test (the reference
        kits, suite home, the gate libraries they copy). A change in it is in every test's closure
        so the closure cannot choose among tests by it (it chose 443 of 464 files); such a change
        is the full gate's, and the tests that import or name the changed file, or import a base
        file importing it (`base_reach`), are chosen by the diff. The DATA its code opens is in it
        too (LOAD_READ), and a change to that chooses every test: every test reads it."""
        if self._base is None:
            self._base = set()
            if ENTRY in self.texts:
                reached = self.reach(ENTRY, (), runs=False)[0]
                self._base = {ENTRY, *reached}
            # ... and what it READS at entry: a land's suite takes its environment from the
            # workflow (tests/ci_shape.py, GOH.CIPARITY), which no import or spelling reaches,
            # and the data its loaded code opens (LOAD_READ).
            self._base |= {p for p in ENTRY_READS if (self.root / p).is_file()}
            self._base |= set(self.load_reads())
        return self._base

    def load_reads(self) -> dict[str, str]:
        """{data file: its opener} for each LOAD_READ a base file declares on the line opening it,
        when the file is here. Every test reads it, so a change to it chooses them all."""
        if self._loaded is None:
            base = self._base if self._base is not None else self.base()
            self._loaded = {
                data: opener
                for opener in sorted(base)
                for data in LOAD_READ.findall(self.texts.get(opener, ""))
                if (self.root / data).is_file()
            }
        return self._loaded

    def base_reach(self, test: str) -> dict[str, tuple[str, ...]]:
        """{base file: the chain to it} for what `test` IMPORTS of the base itself, not through
        tests/conftest.py: a base file stays read by its readers (GOH.BASEDATAREADS:
        tests/test_affected_binary_reach.py imports tools/affected_tests.py, which imports
        lib/binary_runners.py, and a change there chose neither binary test). Import edges only,
        within the base: a base file's names and runs are every test's, so `reach` keeps them
        out (followed, they took a gates/push_gate.sh change from 87 files to 149)."""
        if test not in self._below:
            base = self.base()
            reached, read = self.reach(test)
            todo = [
                (dep, (*reached.get(src, ()), dep))
                for src in (test, *sorted(read))
                for dep in self.imports(src)
                if dep in base and dep != ENTRY
            ]
            below: dict[str, tuple[str, ...]] = {}
            while todo:
                dep, chain = todo.pop(0)
                if dep in below or dep == test:
                    continue
                below[dep] = chain
                todo += [(d, (*chain, d)) for d in self.imports(dep) if d in base and d != ENTRY]
            self._below[test] = below
        return self._below[test]

    def reach(
        self, test: str, skip: set[str] | None = None, runs: bool = True
    ) -> tuple[dict[str, tuple[str, ...]], set[str]]:
        """({file: the chain from the test to it}, the files among them it IMPORTS rather than
        runs), for everything the test depends on, nearest first, the base excepted (`skip` None)
        or the files in `skip`; the base files it imports itself are `base_reach`'s. Imports are
        followed at any depth; a name is followed only out of
        a test or a tests/ or checks/ helper, and the script it names brings its imports and
        sources, not the scripts that script mentions in turn. With `runs`, a shell script's
        variable-rooted uses are edges too (Closure.uses), a base file among them included: pytest
        loading it is no run of its command line, which the script's process is (GOH.PREPARITY:
        lib/gate_identity.py, imported by tests/_land_verdicts.py, run by gates/_rust_proven.sh).
        A test module reached by imports alone is reached as the slice its importers use
        (lib/test_slice.py): the union of their names, the whole file when any takes all of it,
        and always whole for a test with undeclared reads, or of one (UNDECLARED)."""
        if skip is None and runs and test in self._reach:  # a test's own closure, asked twice
            return self._reach[test]  # a land: by the closure rule, then by declared_reads
        memo = skip is None and runs
        skip = self.base() if skip is None else skip
        chains: dict[str, tuple[str, ...]] = {}
        read: set[str] = set()  # reached by imports alone: their own code is read, so it counts
        names: dict[str, frozenset[str] | None] = {}  # a test module's names taken; None: whole
        cuts: dict[str, tuple[str, frozenset[str]]] = {}
        slicing = test not in self.undeclared  # UNDECLARED: its unknown reads keep it all
        ran: set[str] = set()  # reached by a name: run as a process, not imported into one
        # (file, chain, reached by imports alone, a use edge on the chain already)
        todo: list[tuple[str, tuple[str, ...], bool, bool]] = [(test, (), True, False)]
        while todo:
            rel, chain, by_import, used = todo.pop(0)
            cut = cuts.get(rel) if by_import else None

            def visit(dep: str, by_import: bool, used: bool, chain=chain, took=None) -> None:
                if dep == test or dep in skip:
                    return
                widened = False
                if by_import and test_slice.is_test_module(dep):
                    old = names.get(dep, frozenset())
                    new = None if old is None or took is None else old | took
                    if dep not in names or new != old:
                        names[dep] = new
                        widened = dep in read
                        if (sliced := self.slice(dep, new)) is None:
                            cuts.pop(dep, None)
                        else:
                            cuts[dep] = sliced
                elif not by_import:
                    names[dep] = None
                    cuts.pop(dep, None)
                if dep not in chains or (by_import and dep not in read) or widened:
                    chains.setdefault(dep, (*chain, dep))
                    if by_import:
                        read.add(dep)
                    todo.append((dep, chains[dep], by_import, used))

            text = self.texts.get(rel, "")
            for dep in self.imports(rel):
                took = (
                    test_slice.taken(text, dep, cut[1] if cut else None)
                    if slicing and test_slice.is_test_module(dep)
                    else None
                )
                visit(dep, by_import, used, took=took)
            # A shell script's runs, never a mention -- ONE use per chain: a used script's sources
            # come with it, its own runs do not (followed, land.sh -> gate_profile.sh ->
            # push_gate.sh -> install.sh -> ... chose ~100 more files a land).
            # ... and a Python tool RUN by path (named, never imported, nor imported by what is run)
            # runs the scripts its code names; a module imported names them as data
            # (lib/run_rule.py's table, imported by lib/gate_identity.py, names gates/push_gate.sh).
            run_py = rel in ran or not rel.endswith(".py")
            for dep in self.uses(rel) if runs and not used and run_py else ():
                if dep in skip and dep != test:
                    chains.setdefault(dep, (*chain, dep))  # run, not loaded: counted, not followed
                else:
                    visit(dep, False, True)
            if by_import:
                for dep in self.named(rel) if cut is None else self._cut_named(rel, cut):
                    if dep != test and dep not in skip:
                        ran.add(dep)
                    visit(dep, False, used)
        if memo:
            self._reach[test] = (chains, read)
            self._cuts[test] = {n: c for n, c in cuts.items() if n in read}
        return chains, read


def transitive(graph: Closure, test: str, changed: list[str]) -> str | None:
    """Why `test` is chosen by something it reaches (not by its own text), or None. Data pytest's
    load opens (LOAD_READ) every test reaches (GOH.BASEDATAREADS)."""
    opened = graph.load_reads()
    if wide := next((p for p in changed if p in opened), None):
        return f"{wide} is opened before every test ({opened[wide]})"
    reached, read = graph.reach(test)
    below = graph.base_reach(test)
    for path in changed:
        if path in reached or path in below:
            chain = reached.get(path) or below[path]
            return f"reaches {path}" + (f" through {' -> '.join(chain)}" if len(chain) > 1 else "")
    for node, chain in reached.items():
        if node not in read:
            continue  # a script the test runs: what it sources counts, what it mentions does not
        for path in changed:
            if path != node and graph.names_seen(test, node, path):
                via = node + (f" through {' -> '.join(chain)}" if len(chain) > 1 else "")
                return f"reaches {via}, which names {path}"
    return None
