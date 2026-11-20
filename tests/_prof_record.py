"""Each test's own profile in the coverage step's instrumented run (GOH.COVCARRY, term 1).

WHY. Under coverage a test that ran the native binary is never carried from another tree: its run
is coverage the floor counts (tests/_land_verdicts.py). Which tests those are was read from a MARK
-- the `native_goh` fixture or `native_goh_path()` -- and a test that reaches the binary another way
(a gate script resolving GOH_TEST_BIN, a helper) left no mark: its verdict was carried and its
counts never reached the report, so the floor was judged on a profile missing its runs. Of the
2026-11-17 full record's 6135 passes, 1812 were marked, and 1985 unmarked ones sit in the 303 test
files lib/binary_runners.py reads as reaching the binary (154 of them with no marked node).

THE MEASUREMENT. In an instrumented run (`_land_verdicts.instrumented()`), each test runs with its
own LLVM_PROFILE_FILE, `<profile dir>/t/<worker pid>/<node hash>/goh-%4m.profraw` (os.environ: the
suite's children inherit it, and conftest's Popen hook re-reads it for a scrubbed `env=`). A test
that left a raw profile RAN THE BINARY, marked or not: it is marked (`mark_binary`) before its call
report is made, so the land record's `ran_bin` is what happened, and the node joins the record's
`measured` set (lib/reuse.py record_suite), the only nodes coverage may carry. Its raw files are
merged into ONE sparse indexed profile, `<profile dir>/t-<node hash>-<pid>.profraw` (D-0011: 23 ms
and 19 KB a test), the name the report's own merge globs: llvm-profdata reads an indexed profile
in its input list as it reads a raw one (tests/test_cov_carry.py proves the report counts it). A
merge that fails moves the raw files there instead, never drops them. Whatever a test's stray child
writes after its teardown is merged once more at the worker's session end.

A PLAIN RUN MEASURES TOO (GOH.PLAINBINMARK). A plain run that records (the land's pre-step, the
keys and a store named) gives each test its own directory the same way, holding `.goh-binmark`:
a plain `goh` started with LLVM_PROFILE_FILE there leaves `goh-<pid>.binmark` beside it
(crates/goh/src/main.rs `leave_run_mark`), so the mark reaches every child a profile would,
conftest's Popen hook included. A test that left a mark is marked as one that left a profile is,
and every node a CALIBRATED worker ran is `measured` (`calibrate`: the binary conftest's
`native_goh` resolved must leave the mark, else that worker measures nothing and its nodes rerun
under coverage). A mark is written at start and a profile at exit, so the two disagree only toward
re-running; a claim the profile refutes is red wherever it runs instrumented (tests/_cov_claims.py),
the `test` audit's sampled nodes among them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import _land_verdicts as lv
import _prof_reuse  # tests/: the ship's reuse of a profile by its exact key (GOH.COVPROFILEREUSE)
import pytest
from timing_bounds import patience

PROFILE = "LLVM_PROFILE_FILE"
STORE_KEY = "goh_land_store"  # the controller's store, handed to each xdist worker
PROF_MARK = "goh_prof"  # a user property: the test LEFT a profile (a mark alone may run nothing)
MARK_ASK = ".goh-binmark"  # a per-test directory's request: a plain goh leaves a mark there
MARK_KEY = "goh_land_marking"  # the controller's decision to mark, handed to each xdist worker
MEASURED = "goh_measured"  # a user property: a calibrated plain worker measured this node
_state: dict[str, object] = {}
_profiled: set[str] = set()  # the controller's: the nodes whose call left a raw profile
_measured: set[str] = set()  # the controller's: the nodes a calibrated plain worker ran


def _mode() -> str:
    """ "profile" in an instrumented run; "mark" in a plain run that records into a store, the
    controller's decision (an xdist worker's keys left with `_land_verdicts._take`); else ""."""
    if "mode" not in _state:
        mode = ""
        if lv.instrumented():
            mode = "profile"
        elif not (os.environ.get("GOH_COVERAGE_BIN_DIR") or os.environ.get(PROFILE)):
            mode = "mark" if lv._store() and (lv._on(lv.RECORD) or lv._on(lv.REUSE)) else ""
        _state["mode"] = mode
    return str(_state["mode"])


def marking() -> bool:
    """A plain run whose tests leave marks: conftest's Popen hook keeps LLVM_PROFILE_FILE then."""
    return _mode() == "mark"


def calibrate(binary: Path) -> Path:
    """Once per worker of a marking run: `binary` (the one the suite resolved) must leave the mark,
    else this worker measures nothing -- an older goh, or a mark that broke, claims no test ran
    none. conftest's `native_goh` calls it; a worker that never resolved one measures nothing."""
    if not marking() or "calibrated" in _state:
        return binary
    d, was = Path(tempfile.mkdtemp(prefix="goh-binmark-cal-")), os.environ.get(PROFILE)
    try:  # the worker's own environment, as a test's run of it has; only the profile moved
        (d / MARK_ASK).touch()
        os.environ[PROFILE] = str(d / "goh-%4m.profraw")
        subprocess.run([str(binary), "--version"], capture_output=True, timeout=patience(60),
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
        _state["calibrated"] = any(d.glob("*.binmark"))
    except (OSError, subprocess.SubprocessError):
        _state["calibrated"] = False
    finally:
        if was is None:
            os.environ.pop(PROFILE, None)
        else:
            os.environ[PROFILE] = was
        shutil.rmtree(d, ignore_errors=True)
    return binary


def measured(passed: dict[str, bool]) -> dict[str, bool]:
    """What a marking run saw each passed node do ({node: it ran the binary}), for the nodes a
    calibrated worker ran; {} in any other run (an instrumented one reads its own profiles)."""
    return {n: b for n, b in passed.items() if n in _measured} if marking() else {}


def _profdata() -> str | None:
    """rustc's own llvm-profdata (the llvm-tools component the instrumented build needs)."""
    if "profdata" not in _state:
        found = None
        rustc = shutil.which("rustc")
        if rustc:
            r = subprocess.run([rustc, "--print", "target-libdir"], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, check=False)  # fmt: skip
            cand = Path(r.stdout.strip()).parent / "bin" / "llvm-profdata"
            found = str(cand) if r.returncode == 0 and cand.is_file() else None
        _state["profdata"] = found
    return _state["profdata"]  # type: ignore[return-value]


def _base() -> Path | None:
    """This worker's per-test root, under the run's own profile directory; None outside an
    instrumented run."""
    if "base" not in _state:
        base = None
        if _mode() == "profile":
            base = Path(os.environ[PROFILE]).parent / "t" / str(os.getpid())
            shutil.rmtree(base, ignore_errors=True)  # a reused pid's dir is never this run's
            base.mkdir(parents=True, exist_ok=True)
            _state["outer"] = os.environ[PROFILE]
        elif _mode() == "mark":  # nothing outside the run's own directory reads these
            base = Path(tempfile.mkdtemp(prefix="goh-binmark-"))
            _state["outer"] = None
        _state["base"] = base
    return _state["base"]  # type: ignore[return-value]


def node_hash(nodeid: str) -> str:
    return _ps().node_hash(nodeid)


def _ps():
    return lv._lib("prof_store")  # lib/prof_store.py: each test's profile kept (GOH.COVCARRY3)


def key(tree: str, tools: str) -> str | None:
    """The profile store's key for `tree` and THIS run's instrumented binary; None when nothing is
    stored or carried (not an instrumented run, no store, the kill switch, no binary)."""
    if not (lv.instrumented() and lv._store() and _ps().enabled()):
        return None
    with lv._in_store() as (reuse, _):
        if "repo" not in _state:
            _state["repo"] = reuse.repo_id(lv.REPO_ROOT)
        binary = _ps().bin_id(os.environ["GOH_COVERAGE_BIN_DIR"])
        return _ps().key(str(_state["repo"]), tree, tools, binary) if binary else None


def _keep(nodeid: str, merged: Path) -> None:
    """A test's merged profile, kept under this tree's key for the next one."""
    me = lv._here()
    k = key(*me) if me else None
    if k:
        with lv._in_store():
            _ps().put(k, nodeid, merged)


def held(tree: str, tools: str, nodes) -> set[str]:
    """The nodes of `nodes` whose profile `tree`'s run of THIS binary left in the store."""
    k = key(tree, tools)
    if not k:
        return set()
    with lv._in_store():
        return _ps().held(k, nodes)


def kept(ran: set[str]) -> tuple[str, bool, int]:
    """(what the store kept of this run's profiles, whether that is a FAULT). An enabled carry
    whose run drove the binary (`ran`: the nodes that passed and LEFT a profile, `_profiled`) and
    left the store empty under its own key is a reuse path that can never carry (GOH.PROFSTOREEMPTY:
    every xdist worker lost the store's name, so 1691 binary tests re-ran on every land while the
    audit, which samples only the per-crate claim, read uses 8, agreed 8). The third: how many.
    Never the MARKED nodes: a test asking for the binary is marked whether or not it runs it, has
    no profile to keep, and a same-tree re-land re-runs only those (GOH.CIWALL3: 127 of 1833, read
    as an EMPTY store on every re-land of one tree)."""
    me = lv._here()
    k = key(*me) if me and ran else None
    if not k:
        return "", False, 0
    with lv._in_store():
        got = len(_ps().held(k, ran))
    said = f"profile store: {got} of {len(ran)} binary-running tests' profiles kept under tree "
    said += me[0][:12]
    if got:
        return said, False, got
    said += ": the enabled carry left its store EMPTY (tests/_prof_record.py `_keep`)"
    return said, True, 0


def matched(got: int) -> tuple[str, bool]:
    """This run's ledger row (lib/prof_store.py), and whether the store has stopped matching: the
    last MISSES lands owed a match of their sources' stored profiles matched none of them
    (GOH.PROFKEYMISS: a store that fills under keys no land matches carries nothing, unseen)."""
    me = lv._here()
    k = key(*me) if me else None
    if not k:
        return "", False
    pairs = (lv._state.get("plan") or {}).get("profowed") or []  # (source tree, matched)
    sources = sorted({src for src, _ in pairs})
    with lv._in_store():
        ps, rows = _ps(), _ps().ledger()
        row = {"tree": me[0], "key": k, "bin": ps.bin_id(os.environ["GOH_COVERAGE_BIN_DIR"]),
               "kept": got, "owed": ps.owed(rows, sources), "sources": sources,
               "matched": sum(m for _, m in pairs)}  # fmt: skip
        due = ps.misses(rows := ps.note(row))
    if not row["owed"]:
        return "", False
    said = f"profile store: {row['matched']} profiles of source tree {sources[0][:12]} matched"
    if not due:
        return said, False
    srcs = {s for r in due for s in r.get("sources", ())}
    stored = {str(r.get("bin"))[:12] for r in rows if r.get("tree") in srcs and r.get("kept")}
    stored = sorted(stored)
    built = sorted({str(r.get("bin"))[:12] for r in due})
    return (f"profile store: the last {len(due)} instrumented lands that could carry matched none "
            f"of the profiles their sources stored: built {', '.join(built)}, stored under "
            f"{', '.join(stored)} from inputs no build-input change separates -- is the "
            "instrumented build not reproducible (tests/test_coverage_bin_identity.py), or does "
            f"the key read an input it should not? {_ps().KILL}=off stops it"), True  # fmt: skip


def check(session, ran: set[str]) -> None:
    """A reuse path that is on must have filled its store, and must match it: an instrumented run
    whose binary tests passed and kept none of their profiles is red, never a silent full re-run
    next land; so is the MISSES-th owed land in a row that matched none (`matched`)."""
    said, fault, got = kept(ran)
    more, drift = matched(got)
    term = session.config.pluginmanager.get_plugin("terminalreporter")
    for line in (said, more):
        if line:
            (term.write_line if term else print)(f"land verdicts: {line}")
    if (fault or drift) and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_configure(config) -> None:
    """An xdist worker takes the store from its controller: the name left the environment as the
    controller loaded (`_land_verdicts._take`), before any worker started, so no worker -- where
    every test runs -- kept a profile (GOH.PROFSTOREEMPTY: no profstore/ after a full land)."""
    got = getattr(config, "workerinput", {}).get(STORE_KEY)
    if got and not lv._store():
        lv._KEYS[lv.STORE] = got
    if hasattr(config, "workerinput"):
        _state["mode"] = "profile" if lv.instrumented() else config.workerinput.get(MARK_KEY, "")


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node) -> None:
    """xdist, in the controller: hand each worker the store it keeps profiles in."""
    if lv._store():
        node.workerinput[STORE_KEY] = lv._store()
    node.workerinput[MARK_KEY] = "mark" if marking() else ""


def take(tree: str, tools: str, nodes) -> None:
    """Each node's stored profile (from `tree`'s run) where this run's report reads it, and kept
    under this run's own tree, so the next land carries it on."""
    src, me = key(tree, tools), lv._here()
    dest = key(*me) if me else None
    out = Path(os.environ[PROFILE]).parent
    with lv._in_store():
        for n in nodes if src else ():
            got = _ps().get(src, n)
            if got is not None:
                _ps().place(got, out / f"t-carried-{node_hash(n)}.profraw")
                if dest and dest != src:
                    _ps().put(dest, n, got)


def merge(raws: list[Path], dest: Path) -> bool:
    """`raws` merged into one sparse indexed profile at `dest`, the raws removed; on any failure
    the raws are moved beside `dest` (the report reads them as they are) and False."""
    tool = _profdata()
    tmp = dest.with_name(f".{dest.name}.tmp")
    ok = False
    if tool:
        r = subprocess.run([tool, "merge", "--sparse", "-o", str(tmp), *map(str, raws)],
                           capture_output=True, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
        ok = r.returncode == 0
    if ok:
        os.replace(tmp, dest)
        for f in raws:
            f.unlink(missing_ok=True)
        return True
    tmp.unlink(missing_ok=True)
    for i, f in enumerate(raws):
        shutil.move(str(f), str(dest.with_name(f"{dest.stem}-raw{i}.profraw")))
    return False


def _dir_of(item) -> Path | None:
    base = _base()
    return None if base is None else base / node_hash(item.nodeid)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    d = _dir_of(item)
    if d is None:
        yield
        return
    d.mkdir(parents=True, exist_ok=True)
    if _state["outer"] is None:
        (d / MARK_ASK).touch()
    os.environ[PROFILE] = str(d / "goh-%4m.profraw")
    try:
        yield
    finally:
        if _state["outer"] is None:  # a marking run: the marks were read after the call
            os.environ.pop(PROFILE, None)
            shutil.rmtree(d, ignore_errors=True)
        else:
            _merged(item, d)


def _merged(item, d: Path) -> None:
    """The test's raw profiles merged where the report reads them, and kept for the next tree."""
    os.environ[PROFILE] = str(_state["outer"])
    raws = sorted(d.glob("*.profraw"))
    me = f"t-{node_hash(item.nodeid)}-{os.getpid()}.profraw"
    if raws and merge(raws, dest := Path(str(_state["outer"])).with_name(me)):
        _keep(item.nodeid, dest)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """After the call: a raw profile in the test's dir means it ran the binary (setup or call), in
    a marking run a plain goh's mark; a calibrated marking worker's node is measured either way."""
    yield
    d = _dir_of(item)
    if d is None:
        return
    if any(d.glob("*.binmark" if marking() else "*.profraw")):
        lv.mark_binary(item)
        if (PROF_MARK, True) not in item.user_properties:
            item.user_properties.append((PROF_MARK, True))
    if marking() and _state.get("calibrated") is True:
        item.user_properties.append((MEASURED, True))


def pytest_runtest_logreport(report) -> None:
    """The controller's account of which nodes left a profile, and which a calibrated plain worker
    measured (xdist replays each report there)."""
    if (PROF_MARK, True) in report.user_properties:
        _profiled.add(report.nodeid)
    if (MEASURED, True) in report.user_properties:
        _measured.add(report.nodeid)


def pytest_sessionfinish(session) -> None:
    """What a test's stray child wrote after its teardown, merged so the report still reads it;
    the controller checks the run filled the profile store (`check`), then bounds it."""
    if not hasattr(session.config, "workerinput") and key("", ""):
        if lv._on(lv.RECORD) or lv._on(lv.REUSE):  # a run that records: it fills the store
            ran = {n for n in _profiled if n in lv._called and n not in lv._failed}
            check(session, ran)
            term = session.config.pluginmanager.get_plugin("terminalreporter")
            for line in _prof_reuse.finish(ran):  # the ship's: kept by key, counted, checked
                (term.write_line if term else print)(f"land verdicts: {line}")
        with lv._in_store():
            _ps().prune()
    base = _state.get("base")
    if not isinstance(base, Path):
        return
    late = sorted(base.glob("*/*.profraw"))
    if late and _state.get("outer"):
        merge(late, Path(str(_state["outer"])).parent / f"t-late-{os.getpid()}.profraw")
    shutil.rmtree(base, ignore_errors=True)
