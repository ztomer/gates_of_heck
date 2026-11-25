"""What each test file READ of the tree while it ran, so a carry can refuse a read no rule declares.

GOH.READKEYVERDICT. A pass is carried to another tree when tools/affected_tests.py, asked about the
diff between them, does not choose it (tests/_land_verdicts.py): the selector's rules -- what a test
names, imports, reaches, globs or declares (`TREE_READS`, `JUDGE_READS`, lib/declared_reads.py) --
are its read set, and an unchanged read set keeps the verdict. A test that opens a file none of
those rules sees has a read set the carry cannot know, and a change to that file would be carried
past green. So every run that records (the land's keys) watches what its tests open:

  * an audit hook (sys.addaudithook) notes what a test reads under the run's root between its
    setup and its teardown, in the process the test runs in (an xdist worker is told to by its
    controller, `WORKER_KEY`), as lib/read_tokens.py's words (GOH.LANDREADS): a file it opens (a
    module's bytecode is its source), the NAMES of a directory it lists (an import's own scan of
    one is no listing: the module it looks for is the word), all under a directory it opens or
    hands a child process, a file it hands one, a module an import looks for;
  * the words ride the teardown report to the controller (`READS_PROP`), the TEST's own, or its
    FILE's when what it read may be kept for the next test (`_tell`): read in a wider fixture, by
    an import, or below a module whose code keeps state. A functools cache an earlier test filled
    gives each later test that hits it what its filling read (`_memo`); a loaded module that
    `importlib.import_module` hands back again, the files it is made of (`_import_module`);
  * the record of the run's tree keeps them (`reads`, lib/reuse.py record_suite), and a carry from
    it refuses each node that read a word the diff moves though the selector did not choose it
    (`refused`, lib/read_tokens.py `moved`): it runs, and the line names the test and the path.

Records made of several runs are joined in one place, lib/read_sets.py (GOH.READUNION): the lists
united, a file any of whose passed tests nobody watched left out, and no older record believed. The
Linux worker's shard watches too, and its junit carries back what each test read
(tests/_shard_run.py). What it cannot see: what a CHILD process reads beyond the paths it is handed
(a gate script a test runs; its own reads are the selector's closure of that script), a file's
size or mode read by `stat` alone, and a process-wide cache other than those above that one test
FILE filled and another hit (a template a helper builds once a run, tests/_tree_clone.py: the
helper's reads are its users' by the selector's closure).
Pinned by tests/test_read_audit.py.
"""

from __future__ import annotations

import ast
import functools
import importlib
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import _shard_run  # tests/: a remote shard's session, watched too, says what it collected
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
read_sets = importlib.import_module("lib.read_sets")  # the one join (GOH.READUNION)
read_tokens = importlib.import_module("lib.read_tokens")  # what a read is of (GOH.LANDREADS)
sys.path.pop(0)

READS_PROP = "goh_reads"  # a teardown report's user property: the paths its file's test read
WORKER_KEY = "goh_read_audit"  # the controller -> worker word: audit this worker's tests
SKIP = (".git/", "target/", ".pytest_cache/")
SPAWNS = ("subprocess.Popen", "os.posix_spawn", "os.exec")
EVENTS = frozenset(("open", "os.listdir", "os.scandir", "import", "goh.clone_tree", *SPAWNS))
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_CACHED = type(functools.cache(len))

_state: dict[str, object] = {"on": False, "roots": (), "current": None, "hooked": False,
                             "hits": {}, "wide": [], "busy": False}  # fmt: skip
_state_of: dict[str, bool] = {}  # a file of the root: whether its code keeps state
_wide: dict[int, set[str]] = {}  # a wider fixture (its def): what setting it up read
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
ADDS = frozenset(("add", "append", "extend", "insert", "setdefault", "update"))
_memo: dict[object, set[str]] = {}  # a functools cache of this process: what its misses read
_said: dict[str, list[list[str]]] = {}  # the controller's: node -> what its reports said it read


def _keeps(tree: ast.Module) -> bool:
    """Whether a module's own code keeps state: a `global`, or a function that stores into, or adds
    to, what its module or class holds or it imported. What it reads once may answer later tests."""
    held: set[str] = {"cls"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            return True
        if isinstance(node, ast.ClassDef):
            held.add(node.name)
    for node in [
        *tree.body,
        *(n for c in tree.body if isinstance(c, ast.ClassDef) for n in c.body),
    ]:
        for target in getattr(node, "targets", None) or [getattr(node, "target", None)]:
            held |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)} if target else set()
        held |= {(a.asname or a.name).split(".")[0] for a in getattr(node, "names", ())
                 if isinstance(a, ast.alias)}  # fmt: skip
    for fn in (n for n in ast.walk(tree) if isinstance(n, FUNCTIONS)):
        for node in ast.walk(fn):
            at: ast.AST | None = None
            if isinstance(node, ast.Subscript | ast.Attribute) and isinstance(node.ctx, ast.Store):
                at = node.value
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                at = node.func.value if node.func.attr in ADDS else None
            while isinstance(at, ast.Subscript | ast.Attribute):
                at = at.value
            if isinstance(at, ast.Name) and at.id in held - sys.stdlib_module_names:
                return True
    return False


def _stateful(filename: str) -> bool:
    if filename not in _state_of:
        _state_of[filename] = True  # what cannot be read keeps state
        was, _state["busy"] = _state["busy"], True
        try:
            _state_of[filename] = _keeps(ast.parse(Path(filename).read_bytes()))
        finally:
            _state["busy"] = was
    return _state_of[filename]


def _tell(word: str, frame) -> None:
    """WORD read: the running test's own, or its whole file's (FILE) when what it read may be kept
    for the next test: in a wider fixture, an import, or below a module that keeps state. Each
    functools cache that was being filled remembers it (`_memo`)."""
    roots: tuple[str, ...] = _state["roots"]  # type: ignore[assignment]
    wide = bool(_state["wide"]) or frame is None
    while frame is not None:
        name = frame.f_code.co_filename
        if name.startswith("<frozen importlib"):
            wide = True
        elif name.startswith(roots):
            found = frame.f_globals.get(frame.f_code.co_name)
            if type(found) is _CACHED:
                _memo.setdefault(found, set()).add(word)
            wide = wide or _stateful(name)
        frame = frame.f_back
    current = _state["current"]
    if current is not None:
        current.add(read_sets.FILE + word if wide else word)  # type: ignore[union-attr]
    for got in _state["wide"]:  # type: ignore[union-attr]
        got.add(word)


def _below(path: str) -> str | None:
    """PATH (absolute) from the root, "" the root itself; None outside it, or under SKIP."""
    roots: tuple[str, ...] = _state["roots"]  # type: ignore[assignment]
    root = next((r for r in roots if path == r or path.startswith(r + os.sep)), None)
    if root is None:
        return None
    rel = path[len(root) + 1 :].replace(os.sep, "/")
    return None if f"{rel}/".startswith(SKIP) else rel


def _source(path: str) -> str:
    """The source of the bytecode at PATH, which an import reads in its place."""
    at, name = os.path.split(path)
    if os.path.basename(at) == "__pycache__":  # pytest's own rewrite (its tag) is here too
        return os.path.join(os.path.dirname(at), name.split(".")[0] + ".py")
    try:
        return os.path.abspath(importlib.util.source_from_cache(path))
    except (ValueError, NotImplementedError):
        return path


def _note(event: str, args: tuple) -> None:
    """The audit hook: what one event read under the root, as a word of lib/read_tokens.py."""
    if event not in EVENTS or not args or not _state["roots"] or _state["busy"]:
        return
    try:
        raw = args[0]
        if event == "import":
            if _state["current"] is not None:
                _tell(read_tokens.IMPORT + raw, None)
            return
        if event in SPAWNS:
            _handed(args)
            return
        if raw is None or isinstance(raw, int):
            return
        given = os.fsdecode(raw)
        path = os.path.abspath(given)
        # A relative open may be relative to a directory fd the event does not name (shutil.rmtree
        # opens each directory it removes by name, dir_fd=its parent): only a file that IS there,
        # from the cwd, is read from the cwd.
        if event == "open" and not os.path.isabs(given) and not os.path.isfile(path):
            return
        if path.endswith((".pyc", ".pyo")):
            path = _source(path)
        rel = _below(path)
        if rel is None or "__pycache__" in rel or rel.endswith((".pyc", ".pyo")):
            return
        frame = sys._getframe(1)
        if event == "goh.clone_tree":  # clonefile(2) of a file, or of a directory whole
            _tell(read_tokens.under(rel) if os.path.isdir(path) else rel, frame)
        elif event != "open":
            if not frame.f_code.co_filename.startswith("<frozen importlib"):  # a finder's own scan
                _tell(read_tokens.names(rel), frame)
        elif len(args) > 2 and isinstance(args[2], int) and args[2] & _DIRECTORY:
            _tell(read_tokens.under(rel), frame)
        elif rel:
            _tell(rel, frame)
    except Exception:  # noqa: BLE001 -- the audit never fails a test
        return


def _handed(args: tuple) -> None:
    """A child's argv: each word that is a path under the root, from its cwd, is read whole."""
    argv = args[1] if len(args) > 1 else ()
    cwd = os.fsdecode(args[2]) if len(args) > 2 and args[2] else os.getcwd()
    for word in [argv] if isinstance(argv, str | bytes) else list(argv or ())[:400]:
        if isinstance(word, str | bytes | os.PathLike) and len(word := os.fsdecode(word)) < 1024:
            path = os.path.normpath(os.path.join(cwd, word))
            rel = _below(path) if os.path.exists(path) else None
            if rel:
                _tell(read_tokens.under(rel) if os.path.isdir(path) else rel, sys._getframe(2))


def _pulled(module) -> set[str]:
    """The root's files MODULE is made of: its own, and each module's whose names it holds."""
    out: set[str] = set()
    todo, seen = [module], set()
    while todo:
        mod = todo.pop()
        rel = _below(os.path.abspath(getattr(mod, "__file__", None) or os.sep))
        if not rel or mod.__name__ in seen:
            continue
        seen.add(mod.__name__)
        out.add(rel)
        for value in list(vars(mod).values()):
            it = value if isinstance(value, types.ModuleType) else sys.modules.get(
                getattr(value, "__module__", None) or "")  # fmt: skip
            if it is not None:
                todo.append(it)
    return out


def _import_module(real):
    """importlib.import_module, which tells a module an earlier test loaded, and so opens no file,
    as read again (an import STATEMENT's module is the selector's: it reads each one it finds)."""

    @functools.wraps(real)
    def import_module(name, package=None):
        loaded = name in sys.modules
        module = real(name, package)
        current = _state["current"]
        if loaded and current is not None:
            current.update(_pulled(module))  # type: ignore[union-attr]
        return module

    return import_module


def start(root: str) -> None:
    """Watch this process's tests (once a process: an audit hook cannot be removed)."""
    _state["on"] = True
    _state["roots"] = tuple(dict.fromkeys((os.path.abspath(root), os.path.realpath(root))))
    if not _state["hooked"]:
        sys.addaudithook(_note)
        importlib.import_module = _import_module(importlib.import_module)
        _state["hooked"] = True


def _hits() -> dict:
    return {fn: fn.cache_info().hits for fn in _memo}


def refused(nodes: set[str], reads: dict[str, list[str]],
            moved: read_tokens.Moved) -> dict[str, tuple[str, str]]:  # fmt: skip
    """{node: (its file, the changed path it read)} for each of NODES (carriable by the selector)
    that read a word the diff MOVED, or whose file has no reads kept at all: the record names every
    audited file, an empty list included, so a file missing from it was never watched."""
    out: dict[str, tuple[str, str]] = {}
    for node in nodes:
        words = read_sets.words(node, reads)
        hit = "" if words is None else moved.hit(words)
        if words is None or hit:
            out[node] = (read_sets.file_of(node), hit)
    return out


def collected() -> dict[str, list[str]]:
    """{test file or node: what it read} for every file the controller heard from (lib/read_sets.py
    `heard`)."""
    return read_sets.heard(_said.items())


def own(passed) -> dict[str, object]:
    """The `reads` a land record keeps of this process's own PASSED."""
    return read_sets.stamped(passed, parts(passed, {}))


def parts(ran, plan: dict) -> list[read_sets.Part]:
    """What this run's record is joined from (lib/read_sets.py): the nodes it RAN, with what they
    were seen reading, and each record PLAN skipped nodes by, with what that record kept."""
    return [(ran, collected()), *plan.get("reads", ())]


def of_record(path: str, nodes: set[str]) -> read_sets.Part:
    """NODES, skipped by the land record at PATH, and the reads it kept; none for no record."""
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rec = None
    return nodes, read_sets.of(rec)


def keep(plan: dict, rec: dict, nodes: set[str]) -> None:
    """REC's reads of NODES, which a carry skips: a part of PLAN's own, so no other run's reads of
    the same file stand in for them (GOH.READUNION)."""
    plan.setdefault("reads", []).append((nodes, read_sets.of(rec)))


def undeclared(lines: list[str], rec: dict, nodes: set[str], changed: list[str],
               sha: str, root: Path = REPO_ROOT) -> set[str]:  # fmt: skip
    """The NODES the selector would carry from REC whose test file read a changed path no rule of
    the selector declares, or was never watched (`refused`): they run, and a line in LINES says
    why. A record of an older read set (lib/read_sets.py `of`) kept none to believe: all refused."""
    out = refused(nodes, read_sets.of(rec), read_tokens.moved(root, sha, changed))
    if out:
        hit = next(((f, p) for f, p in sorted(out.values()) if p), None)
        why = (f"{hit[0]} read {hit[1]}, which the diff changes and no rule of the selector "
               "declares" if hit else "their files' reads were never watched, or an older goh "
               "kept them")  # fmt: skip
        lines.append(f"land verdicts: {len(out)} not carried from {sha[:8]}: {why}")
    return set(out)


def pytest_configure(config) -> None:
    worker = getattr(config, "workerinput", None)
    if _shard_run.on(config) or (worker is not None and worker.get(WORKER_KEY)):
        start(str(config.rootpath))


def on_controller(config) -> None:
    """The controller's run watches its own tests too (serial); tests/_land_verdicts.py: when."""
    start(str(config.rootpath))


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node) -> None:
    if _state["on"]:
        node.workerinput[WORKER_KEY] = "1"


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item) -> None:
    if _state["on"]:
        _state["current"], _state["hits"] = set(), _hits()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    if _state["on"] and call.when == "teardown" and _state["current"] is not None:
        got, _state["current"] = _state["current"], None
        was = _state["hits"]
        for fn, hits in _hits().items():  # a cache an earlier test filled answered this one
            if hits != was.get(fn, 0):  # type: ignore[union-attr]
                got |= _memo[fn]  # type: ignore[operator]
        info = getattr(item, "_fixtureinfo", None)
        for fixture in (d for ds in getattr(info, "name2fixturedefs", {}).values() for d in ds):
            got |= _wide.get(id(fixture), set())  # type: ignore[operator]
        item.user_properties.append((READS_PROP, json.dumps(sorted(got))))  # type: ignore[arg-type]
    yield


def pytest_runtest_logreport(report) -> None:
    if not _state["on"] or report.when != "teardown":
        return
    said = [json.loads(value) for name, value in report.user_properties if name == READS_PROP]
    _said.setdefault(report.nodeid, []).extend(said)


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(fixturedef, request):
    """A wider fixture's reads are each user's (`pytest_runtest_makereport`) and its file's."""
    if not _state["on"] or fixturedef.scope == "function":
        return (yield)
    wide: list = _state["wide"]  # type: ignore[assignment]
    wide.append(_wide.setdefault(id(fixturedef), set()))
    try:
        return (yield)
    finally:
        wide.pop()
