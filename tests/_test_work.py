"""THE WORK EACH TEST DID (GOH.TESTWORK): counted in every process that runs tests, and judged.

Owner, 2026-11-25: "Counts per test: processes started, git repos made, cargo builds, whole-tree
walks, CPU seconds." The dimensions, what they mean and the ceilings are lib/test_work.py's; this
plugin measures them, from a test's setup to its teardown report:

  * CPU: getrusage of this process and of the children it waited for (tests/_drift_guard.py's);
  * an audit hook (sys.addaudithook): each `subprocess.Popen`, `os.system`, `os.fork`/`forkpty`
    and `os.posix_spawn[p]` is a process (a posix_spawn of the argv a Popen just announced is that
    Popen's, counted once); its argv says whether it is `git init`/`clone` or a compiling cargo;
    a listing (`os.scandir`, `os.listdir`, not an import's own) of goh's tree -- this checkout, or
    a copy (TREE_MARKS) -- followed by one at depth 2 or more below it is a whole-tree walk
    (os.walk, rglob, copytree, rmtree); a 3-file fixture repository's is no walk of the tree;
  * a session or package fixture's work is the run's (`<shared setup>`), not the test that first
    asked for it, and so is the session's teardown in a process's last test; a module fixture's
    stays with its file.

A test's counts ride its teardown report to the controller (a user property, the way
tests/_read_audit.py's reads do). The controller of a run the runner named (tests/_schedule.py
took its goh.db) records them (lib/test_work.py `record`) and judges every test it ran by its
ceiling, and a whole run each entry for staleness; a fault turns a green session red, naming the
test, the dimension and the exception form. What it cannot see: a child's own children (a gate
script's git), work done by a thread or child that outlives the test, and a process-wide cache a
test fills for later tests (counted to the test that filled it).

GATES_OF_HECK_TEST_WORK (popped when this loads, so a session a test starts never inherits it)
turns the judging on in a run nobody named: `judge` as the runner's, `new` as a NEW test's (its
own node's entry or `new`, never its file's: tests/test_test_work_new.py). With it,
GATES_OF_HECK_TEST_WORK_CEILING (JSON) replaces the ceilings and GATES_OF_HECK_TEST_WORK_OUT names
a file the run's rows are written to. Pinned by tests/test_test_work.py.
"""

from __future__ import annotations

import json
import os
import re
import resource
import shlex
import sys
import threading
import time
from pathlib import Path

import pytest

_LIB = str(Path(__file__).resolve().parent.parent / "lib")
if _LIB not in sys.path:
    sys.path.append(_LIB)
import test_work  # noqa: E402  (lib/)

MODE = os.environ.pop("GATES_OF_HECK_TEST_WORK", "")
CEILING = os.environ.pop("GATES_OF_HECK_TEST_WORK_CEILING", "")
OUT = os.environ.pop("GATES_OF_HECK_TEST_WORK_OUT", "")
PROP = "goh_test_work"  # a teardown report's user property: the test's counts
LISTS = frozenset(("os.scandir", "os.listdir"))
SPAWNS = frozenset(("subprocess.Popen", "os.posix_spawn", "os.posix_spawnp", "os.system",
                    "os.fork", "os.forkpty"))  # fmt: skip
CARGO_COMPILES = frozenset(("build", "b", "test", "t", "run", "r", "check", "c", "clippy",
                            "rustc", "bench", "doc", "install", "llvm-cov", "nextest", "fuzz",
                            "miri"))  # fmt: skip
_GIT_VALUED = frozenset(("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"))
_SHELL_WORDS = re.compile(r"[;&|()`\n]+")

_C = dict.fromkeys(test_work.COUNTS, 0)  # this process's counts so far
_tl = threading.local()  # the argv the thread's last Popen announced
_CHECKOUT = os.path.realpath(Path(__file__).resolve().parent.parent)
TREE_MARKS = ("gates/structural.sh", "tests/conftest.py", "lib/goh_db.py")  # a goh tree, or a copy
_roots: set[str] = set()  # goh tree roots this test listed, not yet walked below
_is_root: dict[str, bool] = {}
_mark: dict[str, object] = {}  # the current test's start: counts, CPU, what wide fixtures took
_wide = [0]  # depth of session/package fixture setups under way
_shared: dict[str, float] = dict.fromkeys(test_work.DIMS, 0)
_rows: dict[str, dict] = {}  # the controller's: node -> counts
_cold: set[str] = set()
_ran: set[str] = set()


def _cpu() -> float:
    own, kids = (
        resource.getrusage(resource.RUSAGE_SELF),
        resource.getrusage(resource.RUSAGE_CHILDREN),
    )
    return own.ru_utime + own.ru_stime + kids.ru_utime + kids.ru_stime


def _now() -> dict[str, float]:
    return {"cpu_s": _cpu(), **_C}


def _words(argv) -> list[str]:
    if isinstance(argv, str | bytes | os.PathLike):
        argv = [argv]
    words = [os.fsdecode(w) for w in list(argv or ())[:200] if isinstance(w, str | bytes)]
    if (
        len(words) >= 3
        and os.path.basename(words[0]) in ("sh", "bash", "zsh", "dash")
        and (words[1] == "-c" or (words[1].endswith("c") and words[1].startswith("-")))
    ):  # a shell's command line: each of its commands
        out = []
        for part in _SHELL_WORDS.split(words[2]):
            try:
                out += [*shlex.split(part), ";"]
            except ValueError:
                out += [*part.split(), ";"]
        return out
    return words


def _sub(words: list[str], i: int, valued: frozenset[str]) -> str:
    """The subcommand after the program at I: the first word that is no option (nor its value)."""
    j = i + 1
    while j < len(words):
        w = words[j]
        if w in valued:
            j += 2
        elif w.startswith(("-", "+")):
            j += 1
        else:
            return w
    return ""


def classify(argv) -> tuple[int, int]:
    """(git repositories, cargo builds) one spawn's ARGV makes."""
    words, git, cargo = _words(argv), 0, 0
    for i, w in enumerate(words):
        name = os.path.basename(w)
        if name == "git" and _sub(words, i, _GIT_VALUED) in ("init", "clone"):
            git += 1
        elif name == "cargo" and _sub(words, i, frozenset()) in CARGO_COMPILES:
            cargo += 1
    return git, cargo


def _spawned(argv) -> None:
    _C["procs"] += 1
    git, cargo = classify(argv)
    _C["git_repos"] += git
    _C["cargo_builds"] += cargo


def _root(path: str) -> bool:
    got = _is_root.get(path)
    if got is None:
        got = _is_root[path] = path == _CHECKOUT or all(
            os.path.lexists(os.path.join(path, m)) for m in TREE_MARKS
        )
    return got


def _listed(arg) -> None:
    if arg is None:
        arg = "."
    if isinstance(arg, int):
        return
    path = os.fsdecode(os.fspath(arg))
    path = os.path.normpath(path if os.path.isabs(path) else os.path.join(os.getcwd(), path))
    for r in [r for r in _roots if path.startswith(r + os.sep)]:
        if path[len(r) + 1 :].count(os.sep) >= 1:  # two levels below the root: a walk
            _C["tree_walks"] += 1
            _roots.discard(r)
    if _root(path):
        _roots.add(path)


def _audit(event: str, args: tuple) -> None:
    try:
        if event in LISTS:
            if not sys._getframe(1).f_code.co_filename.startswith("<frozen importlib"):
                _listed(args[0] if args else None)
        elif event in SPAWNS:
            if event == "subprocess.Popen":
                argv = list(args[1]) if isinstance(args[1], list | tuple) else args[1]
                _tl.argv = argv
                _spawned(argv)
            elif event.startswith("os.posix_spawn"):
                argv = list(args[1] or ())
                if getattr(_tl, "argv", None) == argv:
                    _tl.argv = None  # that Popen's own
                else:
                    _spawned(argv)
            elif event == "os.system":
                _spawned(["sh", "-c", os.fsdecode(args[0])])
            else:
                _C["procs"] += 1
    except Exception:  # noqa: BLE001 -- the instrument never fails a test
        return


sys.addaudithook(_audit)


def _delta(since: dict[str, float]) -> dict[str, float]:
    now = _now()
    return {d: now[d] - since[d] for d in test_work.DIMS}


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item) -> None:
    _roots.clear()
    _is_root.clear()
    _mark.update(start=_now(), excused=dict.fromkeys(test_work.DIMS, 0.0))


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    if fixturedef.scope not in ("session", "package") or _wide[0]:
        yield
        return
    _wide[0] += 1
    since = _now()
    try:
        yield
    finally:
        _wide[0] -= 1
        _excuse(_delta(since))


def _excuse(took: dict[str, float]) -> None:
    excused = _mark.get("excused")
    for d, n in took.items():
        _shared[d] += n
        if isinstance(excused, dict):
            excused[d] += n


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    since = _now()
    yield
    if nextitem is None:  # the session's fixtures are torn down in this process's last test
        _excuse(_delta(since))
        _mark["last"] = True


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    if call.when == "teardown" and "start" in _mark:
        took = _delta(_mark.pop("start"))  # type: ignore[arg-type]
        excused = _mark.pop("excused", {})
        row = {d: max(0.0, took[d] - excused.get(d, 0.0)) for d in test_work.DIMS}  # type: ignore[union-attr]
        row = {d: round(v, 3) if d == "cpu_s" else int(v) for d, v in row.items()}
        if _mark.pop("last", False):  # this process's last test carries its shared setup
            row[test_work.SHARED] = {d: round(v, 3) for d, v in _shared.items()}
        item.user_properties.append((PROP, json.dumps(row)))
    yield


def pytest_runtest_logreport(report) -> None:
    if getattr(report, "goh_pole_cold", False):  # tests/_drift_guard.py: a kept store missed
        _cold.add(report.nodeid)
    if report.when == "call":
        _ran.add(report.nodeid)
    if report.when != "teardown":
        return
    for name, value in report.user_properties:
        if name == PROP:
            row = json.loads(value)
            shared = row.pop(test_work.SHARED, None)
            if shared:
                into = _rows.setdefault(test_work.SHARED, dict.fromkeys(test_work.DIMS, 0))
                for d, n in shared.items():
                    into[d] += n
            _rows[report.nodeid] = row


def _table() -> Path | None:
    schedule = sys.modules.get("_schedule")
    table = getattr(schedule, "_table", None)
    return table[0] if table else None


def _whole(config) -> bool:
    import _whole_run

    churn = sys.modules.get("_fs_churn")
    deselected = getattr(churn, "_DESELECTED", [0])[0] if churn else 0
    return _whole_run.whole(config) and not deselected


def _load() -> float | None:
    try:
        return round(os.getloadavg()[0], 2)
    except OSError:
        return None


_LOAD0 = [_load()]


def pytest_sessionfinish(session) -> None:
    if hasattr(session.config, "workerinput"):
        return
    db = _table()
    if db is None and not MODE:
        return
    rows = {
        n: {**r, "cold": n in _cold} for n, r in _rows.items() if n in _ran or n == test_work.SHARED
    }
    whole = _whole(session.config)
    conf = test_work.config(CEILING or None)
    run = f"{os.getpid()}:{time.time():.3f}"
    past = test_work.history(conf, db) if db is not None and whole else None  # before this run's
    kept = db is not None and test_work.record(
        run, rows, whole, _LOAD0[0], _load(), db, cores=os.cpu_count()
    )
    if OUT:
        load = {"load1": _LOAD0[0], "load_end": _load(), "cores": os.cpu_count()}
        Path(OUT).write_text(
            json.dumps({"whole": whole, **load, "rows": rows}) + "\n", encoding="utf-8"
        )
    found = test_work.faults(rows, conf, new_only=MODE == "new")
    if whole and MODE != "new":
        found += test_work.stale(rows, conf, past)
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    say = tr.write_line if tr else print
    say(f"→ test work: {len(rows) - (test_work.SHARED in rows)} tests counted"
        f"{' and recorded' if kept else ''} (lib/test_work.py)")  # fmt: skip
    for f in found:
        say(f"✗ test work (GOH.TESTWORK): {f}")
    if found and session.exitstatus == 0:
        session.exitstatus = 1
