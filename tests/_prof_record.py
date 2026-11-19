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
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import _land_verdicts as lv
import pytest

PROFILE = "LLVM_PROFILE_FILE"
STORE_KEY = "goh_land_store"  # the controller's store, handed to each xdist worker
_state: dict[str, object] = {}


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
        if lv.instrumented():
            base = Path(os.environ[PROFILE]).parent / "t" / str(os.getpid())
            shutil.rmtree(base, ignore_errors=True)  # a reused pid's dir is never this run's
            base.mkdir(parents=True, exist_ok=True)
            _state["outer"] = os.environ[PROFILE]
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


def kept(ran: set[str]) -> tuple[str, bool]:
    """(what the store kept of this run's profiles, whether that is a FAULT). An enabled carry
    whose run drove the binary (`ran`: the nodes that passed running it) and left the store empty
    under its own key is a reuse path that can never carry (GOH.PROFSTOREEMPTY: every xdist
    worker lost the store's name, so 1691 binary tests re-ran on every land while the audit,
    which samples only the per-crate claim, read uses 8, agreed 8)."""
    me = lv._here()
    k = key(*me) if me and ran else None
    if not k:
        return "", False
    with lv._in_store():
        got = len(_ps().held(k, ran))
    said = f"profile store: {got} of {len(ran)} binary-running tests' profiles kept under tree "
    said += me[0][:12]
    if got:
        return said, False
    return f"{said}: the enabled carry left its store EMPTY (tests/_prof_record.py `_keep`)", True


def check(session, ran: set[str]) -> None:
    """A reuse path that is on must have filled its store: an instrumented run whose binary tests
    passed and kept none of their profiles is red, never a silent full re-run next land."""
    said, fault = kept(ran)
    if said:
        term = session.config.pluginmanager.get_plugin("terminalreporter")
        (term.write_line if term else print)(f"land verdicts: {said}")
    if fault and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_configure(config) -> None:
    """An xdist worker takes the store from its controller: the name left the environment as the
    controller loaded (`_land_verdicts._take`), before any worker started, so no worker -- where
    every test runs -- kept a profile (GOH.PROFSTOREEMPTY: no profstore/ after a full land)."""
    got = getattr(config, "workerinput", {}).get(STORE_KEY)
    if got and not lv._store():
        lv._KEYS[lv.STORE] = got


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node) -> None:
    """xdist, in the controller: hand each worker the store it keeps profiles in."""
    if lv._store():
        node.workerinput[STORE_KEY] = lv._store()


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
    os.environ[PROFILE] = str(d / "goh-%4m.profraw")
    try:
        yield
    finally:
        os.environ[PROFILE] = str(_state["outer"])
        raws = sorted(d.glob("*.profraw"))
        if raws:
            dest = (
                Path(str(_state["outer"])).parent
                / f"t-{node_hash(item.nodeid)}-{os.getpid()}.profraw"
            )
            if merge(raws, dest):
                _keep(item.nodeid, dest)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """After the call: a raw profile in the test's dir means it ran the binary (setup or call)."""
    yield
    d = _dir_of(item)
    if d is not None and any(d.glob("*.profraw")):
        lv.mark_binary(item)


def pytest_sessionfinish(session) -> None:
    """What a test's stray child wrote after its teardown, merged so the report still reads it;
    the controller checks the run filled the profile store (`check`), then bounds it."""
    if not hasattr(session.config, "workerinput") and key("", ""):
        if lv._on(lv.RECORD) or lv._on(lv.REUSE):  # a run that records: it fills the store
            check(session, {n for n in lv._bin if n in lv._called and n not in lv._failed})
        with lv._in_store():
            _ps().prune()
    base = _state.get("base")
    if not isinstance(base, Path):
        return
    late = sorted(base.glob("*/*.profraw"))
    if late:
        merge(late, Path(str(_state["outer"])).parent / f"t-late-{os.getpid()}.profraw")
    shutil.rmtree(base, ignore_errors=True)
