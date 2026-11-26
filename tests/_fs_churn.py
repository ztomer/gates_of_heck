"""THE SUITE'S FILE EVENTS (GOH.FSCHURN2): counted every whole run, held under a ceiling.

GOH.FSCHURN (2026-11-18) made the suite's temp roots reuse their names and closed; two days later
fseventsd was back at 100-145% CPU and 11.9 GB while the suite ran, ~800 fresh directories a
second, and nothing had measured it: the regression was felt, not seen. fseventsd's work is one
record per file event, so the record count is the cost. This plugin opens an FSEvents stream
(lib/fs_events.py, no sudo) on the run's OWN roots -- its TMPDIR, which is the runner's
PYTEST_DEBUG_TEMPROOT (tests/suite_tmp.py), and its suite home (tests/suite_home.py) -- in the
controller, for the whole session, and at its end

  * writes the count, the tests run and the top creators to $GATES_OF_HECK_FS_EVENTS (a JSON file
    tools/pytest.sh names for a whole run and records in the suite's timing-ledger row);
  * turns a green session RED when a file's worst test broke its file's ceilings, one test's own
    tmp_path took more than CEILING["per_test"] events, or -- a WHOLE run only -- the
    run more than CEILING["per_test_run"] per test it ran,
    the run's count including the entries it leaves under its roots, which the runner's removal
    of them turns into events after the stream stops (`left_behind`).

WHOLE RUNS ONLY (GOH.FSCEILINGRED). The mean was meant to hold a land's partial suite and a full
one alike; it does not. A land's gate and the rust gate's external pytest are argument-less runs
that DESELECT what the pre-step passed (tests/_land_verdicts.py), so the tests left are the
change's own -- the heaviest, repo-building ones -- and their mean is no reading of a ceiling
re-set from a whole run (GOH.FSCHURN3): 1187 and 1468 of 5812 ran at 204.6 and 200.3 events each
on 2026-11-21 and two lands went red on 185 while whole runs sat at 120-155. A run is whole when
its arguments select the whole suite (tests/_whole_run.py) and nothing was deselected after
collection (counted in every process through pytest's own `pytest_deselected`, each xdist worker
telling the controller, which collects nothing). A partial run's count is still written, with
`whole` and `deselected`, and the per-test ceiling, which holds for any selection, still
judges it, as each FILE's own does (tests/_fs_ceilings.py, GOH.FSEVQUIET).

A LAND'S PRE-STEP IS A SELECTION TOO (GOH.FSEVQUIET). It names its tests, so it is counted and
written, but its tests are the diff's, and a Rust change's are the binary's: 3,751 tests chosen
because they run goh read 261,425 events (69.7 each) over a ceiling of 60 (2026-11-25). The
instrumented binary added none of them (no profile under the roots; the 996 tests of 99 files it
shares with a plain run read 45,389 events under it and 45,757 plain); the selection did. In an
idle run of 5,158 tests its 313 files read 113.2 events a test under their own tmp_paths and the
other files 27.4, and 37-48% of a run's events (shared templates and pools, what it leaves) have
no file, so no expected mean of a selection can be built from its files' readings. The means, of
events and of the bytes each test left written (`bytes_per_test_run`, without the byte-named
files, which hold their own: `_unnamed`), are judged on whole runs only; a selection's tests and
files are judged by their own ceilings, as above. A whole run's bytes are judged in any window.

UNMEASURED WINDOWS ARE NO VERDICT (GOH.FSDROPVERDICT). A window whose stream dropped events, or
did not deliver through the run's end (lib/fs_events.py `Stream.drain`), undercounts: 148 of 400
planted dirs at load 25-45 read as a verdict and refused lands (2026-11-23). Such a run writes
`unmeasured` (why) and no count, and its events are judged by no ceiling; the line says so.

THE BYTES (GOH.FSEVQUIET): the same run reads what each test leaves written (tests/_fs_bytes.py) and
the devices' write counters, and prints the 20 largest tests.

The ceilings live in tests/fs_events_ceiling.json; GATES_OF_HECK_FS_EVENTS_CEILING (JSON) replaces
them for a test of this plugin. Off where FSEvents is not (Linux): the file says `unavailable`.
Not GOH_-prefixed: a hermetic child that drops GOH_* still carries neither; a nested session never
sees the variable, because this plugin pops it as it reads it. Pinned by tests/test_fs_churn.py.
"""

from __future__ import annotations

import json
import os
import pwd
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

_LIB = str(Path(__file__).resolve().parent.parent / "lib")
if _LIB not in sys.path:
    sys.path.append(_LIB)
import _fs_bytes  # noqa: E402  (tests/, beside this file)
import _fs_ceilings  # noqa: E402
import _whole_run  # noqa: E402
import fs_events  # noqa: E402
import pytest  # noqa: E402

OUT = "GATES_OF_HECK_FS_EVENTS"
CEILING_ENV = "GATES_OF_HECK_FS_EVENTS_CEILING"
CEILING_FILE = Path(__file__).resolve().parent / "fs_events_ceiling.json"

# UNMEASURED RUNS (GOH.FSDROPVERDICT, ruling 2026-11-23): a test whose EVERY window the instrument
# marked unmeasured is skipped with why, never passed and never a land-blocking red; each such skip
# is a line in this ledger (the owner's real ~/.cache/goh, not a suite HOME), and every run's
# summary prints a warning while it holds lines from the last day, so it is never silent.
LEDGER_ENV = "GATES_OF_HECK_FS_UNMEASURED_LEDGER"
RECENT_S = 24 * 3600


def ledger() -> Path:
    raw = os.environ.get(LEDGER_ENV)
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    return Path(raw) if raw else home / ".cache" / "goh" / "fs_events_unmeasured.log"


def unmeasured_skip(why: list[str]) -> None:
    """Skip the calling test: every one of its `why` windows was unmeasured. Ledgered first."""
    n = len(why)
    reason = f"fs_events unmeasured: the instrument dropped events in {n} windows of {n}"
    reason += f" (fseventsd: {why[0] if why else 'no window'})"
    test = os.environ.get("PYTEST_CURRENT_TEST", "?").split(" ")[0]
    path = ledger()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"time": time.time(), "test": test, "reason": reason})
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    pytest.skip(reason)


def recent_unmeasured(now: float | None = None) -> int:
    """The ledger's unmeasured skips from the last RECENT_S seconds."""
    now = time.time() if now is None else now
    try:
        lines = ledger().read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    n = 0
    for raw in lines:
        try:
            n += now - float(json.loads(raw)["time"]) <= RECENT_S
        except (ValueError, KeyError, TypeError):
            continue
    return n


def pytest_terminal_summary(terminalreporter) -> None:
    if n := recent_unmeasured():
        terminalreporter.write_line(
            f"⚠ file events: {n} fs_events {'test' if n == 1 else 'tests'} skipped"
            f" unmeasured in the last 24 h"
            f" ({ledger()}): fseventsd is dropping events; unmeasured three days running is a"
            f" fault to report (GOH.FSDROPVERDICT)"
        )


_STATE: dict = {}
DESELECTED_KEY = "gates_of_heck_fs_deselected"  # an xdist worker -> its controller
_DESELECTED: list[int] = [0]  # this process's deselected count, whatever plugin deselected
# The report's per-test lists are this long: enough to rank the long tail by file (GOH.FSCHURN3).
TOP = 200


def _ceiling() -> dict:
    raw = os.environ.get(CEILING_ENV)
    return json.loads(raw) if raw else json.loads(CEILING_FILE.read_text())


def _unnamed(rep: dict, tests_run: int, ceiling: dict) -> tuple[int, int]:
    """(bytes, tests) without the files `named` in the ceiling, which each hold a byte ceiling of
    their own: how many of them a selection draws moved its mean 44,000 -> 76,138, not its tests."""
    own = [
        f for name, f in rep.get("files", {}).items()
        if "bytes" in ceiling.get("named", {}).get(name, {})
    ]  # fmt: skip
    return (
        rep.get("bytes", 0) - sum(f["all_bytes"] for f in own),
        tests_run - sum(f["tests"] for f in own),
    )


def verdict(rep: dict, tests_run: int, ceiling: dict, whole: bool = True) -> list[str]:
    """Each way `rep` (a Tally report) breaks `ceiling`; empty when it holds. The means are judged
    only on a `whole` run: the ceiling describes the whole suite, never a selection of it, however
    large. An unmeasured window has no events to judge; its bytes are read from the files."""
    faults = []
    counted = not rep.get("unmeasured")
    worst = max(rep["per_test"].items(), key=lambda kv: kv[1], default=None) if counted else None
    if worst and worst[1] > ceiling["per_test"]:
        faults.append(
            f"{worst[0]} made {worst[1]} file events,"
            f" over the per-test ceiling {ceiling['per_test']}"
        )
    if not tests_run or not whole:
        return faults
    total = rep["events"] + rep["teardown_entries"] if counted else 0
    if total / tests_run > ceiling["per_test_run"]:
        faults.append(
            f"{total} file events (with what the run left to remove) for {tests_run} tests"
            f" ({total / tests_run:.1f} each), over the ceiling {ceiling['per_test_run']} each"
        )
    size, tests = _unnamed(rep, tests_run, ceiling)
    each, most = size / max(tests, 1), ceiling.get("bytes_per_test_run", sys.maxsize)
    if each > most:
        faults.append(
            f"{size} bytes left written by a run of {tests} ({each:.0f} a test),"
            f" over the ceiling {most} each"
        )
    return faults


def left_behind(roots: list[str]) -> Counter[str]:
    """The entries still under `roots` when the session ends, by who made them: each one is a file
    event when the runner removes the root, after this stream has stopped. Counted, so a cut that
    only moves the removal from a test's teardown to the run's (a template kept for the whole run)
    is not free."""
    return Counter(
        fs_events.creator(os.path.join(at, name), roots)
        for root in roots
        for at, dirs, files in os.walk(root)
        for name in dirs + files
    )


def pytest_configure(config) -> None:  # the bytes beside the events (GOH.FSEVQUIET)
    if not config.pluginmanager.is_registered(_fs_bytes):
        config.pluginmanager.register(_fs_bytes, "_fs_bytes")


def pytest_sessionstart(session) -> None:
    out = os.environ.pop(OUT, "")
    if not out or hasattr(session.config, "workerinput"):  # an xdist worker: the controller counts
        return
    _STATE["out"] = out
    roots = [os.path.realpath(r) for r in (tempfile.gettempdir(), os.environ.get("HOME", ""))]
    roots = [r for r in roots if r and os.path.isdir(r)]
    tally = fs_events.Tally(roots)
    try:
        stream = fs_events.Stream(roots, tally)
        stream.__enter__()
    except OSError as e:
        Path(out).write_text(json.dumps({"unavailable": str(e)}) + "\n")
        return
    _STATE.update(tally=tally, stream=stream, t0=fs_events.time.monotonic(), ran=0, nodes=set())
    _STATE["device"] = _fs_bytes.device_bytes()
    _fs_bytes.start()


def _files(rep: dict, tally, ceiling: dict, ran: set[str], whole: bool) -> list[str]:
    """Each file's reading into the report, and the faults of tests/_fs_ceilings.py."""
    events = _fs_ceilings.by_test(tally.per_dir, _fs_bytes.DIRS)
    rep["files"] = _fs_ceilings.readings(events, rep.pop("all_bytes"), ran)
    if "new" not in ceiling:  # a test of this plugin, with no file ceilings of its own
        return []
    before = set(ceiling["files_today"]) if "files_today" in ceiling else _fs_ceilings.today()
    measured = not rep.get("unmeasured")
    whole = whole and not _fs_bytes.LOST  # a crashed worker's tests read as none: no name is stale
    return _fs_ceilings.faults(rep["files"], ceiling, before, whole, measured)


def pytest_deselected(items) -> None:
    if not items:
        return
    _DESELECTED[0] += len(items)
    config = items[0].config
    if hasattr(config, "workerinput"):  # an xdist worker: its controller collects nothing
        config.workeroutput[DESELECTED_KEY] = _DESELECTED[0]


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error) -> None:
    """xdist, in the controller: each worker collects the whole set, so the most any deselected."""
    n = int(getattr(node, "workeroutput", {}).get(DESELECTED_KEY, 0))
    _DESELECTED[0] = max(_DESELECTED[0], n)


def pytest_runtest_logreport(report) -> None:
    if "tally" in _STATE and report.when == "call":
        _STATE["ran"] += 1
        _STATE["nodes"].add(report.nodeid)


def pytest_sessionfinish(session, exitstatus) -> None:
    tally = _STATE.pop("tally", None)
    if tally is None:
        return
    stream = _STATE.pop("stream")
    stream.drain()  # delivered through the run's end, or the window is unmeasured
    stream.__exit__(None, None, None)
    rep = tally.report(fs_events.time.monotonic() - _STATE.pop("t0"), TOP)
    rep |= _fs_bytes.finish(TOP)
    before, after = _STATE.pop("device"), _fs_bytes.device_bytes()
    rep["device_bytes"] = None if before is None or after is None else after - before
    ran = _STATE.pop("ran")
    rep["tests_run"] = ran
    left = left_behind(tally.roots)
    rep["teardown_entries"], rep["teardown_by"] = left.total(), dict(left.most_common(TOP))
    rep["deselected"] = _DESELECTED[0]
    rep["whole"] = whole = _whole_run.whole(session.config) and not rep["deselected"]
    ceiling = _ceiling()
    unmeasured = rep.get("unmeasured", "")
    # rep["files"] first: verdict reads it
    files = _files(rep, tally, ceiling, _STATE.pop("nodes"), whole)
    faults = verdict(rep, ran, ceiling, whole) + files
    rep["ceiling"], rep["faults"] = ceiling, faults
    Path(_STATE.pop("out")).write_text(json.dumps(rep, indent=1) + "\n")
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    if unmeasured:
        (tr.write_line if tr else print)(
            f"⚠ file events: unmeasured, no verdict: {unmeasured} (tests/_fs_churn.py)"
        )
    elif not whole:
        (tr.write_line if tr else print)(
            f"→ file events: a partial run ({ran} ran, {rep['deselected']} deselected): the per-run"
            f" ceiling describes whole runs and is not judged (tests/_fs_churn.py)"
        )
    for line in _fs_bytes.summary(rep):
        (tr.write_line if tr else print)(line)
    for f in faults:
        line = f"✗ file events (GOH.FSCHURN2, tests/fs_events_ceiling.json): {f}"
        (tr.write_line if tr else print)(line)
    if faults and session.exitstatus == 0:
        session.exitstatus = 1
