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
  * turns a green session RED when one test's own tmp_path took more than CEILING["per_test"]
    events, or the run as a whole more than CEILING["per_test_run"] per test it ran (the mean, so
    a land's partial suite and a full one are held alike) -- the run's count including the entries
    it leaves under its roots, which the runner's removal of them turns into events after the
    stream stops (`left_behind`).

The ceilings live in tests/fs_events_ceiling.json; GATES_OF_HECK_FS_EVENTS_CEILING (JSON) replaces
them for a test of this plugin. Off where FSEvents is not (Linux): the file says `unavailable`.
Not GOH_-prefixed: a hermetic child that drops GOH_* still carries neither; a nested session never
sees the variable, because this plugin pops it as it reads it. Pinned by tests/test_fs_churn.py.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_LIB = str(Path(__file__).resolve().parent.parent / "lib")
if _LIB not in sys.path:
    sys.path.append(_LIB)
import fs_events  # noqa: E402

OUT = "GATES_OF_HECK_FS_EVENTS"
CEILING_ENV = "GATES_OF_HECK_FS_EVENTS_CEILING"
CEILING_FILE = Path(__file__).resolve().parent / "fs_events_ceiling.json"

_STATE: dict = {}
# The report's per-test lists are this long: enough to rank the long tail by file (GOH.FSCHURN3).
TOP = 200


def _ceiling() -> dict:
    raw = os.environ.get(CEILING_ENV)
    return json.loads(raw) if raw else json.loads(CEILING_FILE.read_text())


def verdict(rep: dict, tests_run: int, ceiling: dict) -> list[str]:
    """Each way `rep` (a Tally report) breaks `ceiling`; empty when it holds."""
    faults = []
    worst = max(rep["per_test"].items(), key=lambda kv: kv[1], default=None)
    if worst and worst[1] > ceiling["per_test"]:
        faults.append(
            f"{worst[0]} made {worst[1]} file events,"
            f" over the per-test ceiling {ceiling['per_test']}"
        )
    total = rep["events"] + rep["teardown_entries"]
    if tests_run and total / tests_run > ceiling["per_test_run"]:
        faults.append(
            f"{total} file events (with what the run left to remove) for {tests_run} tests"
            f" ({total / tests_run:.1f} each), over the ceiling {ceiling['per_test_run']} each"
        )
    return faults


def left_behind(roots: list[str]) -> int:
    """The entries still under `roots` when the session ends: each one is a file event when the
    runner removes the root, after this stream has stopped. Counted, so a cut that only moves the
    removal from a test's teardown to the run's (a template kept for the whole run) is not free."""
    return sum(len(dirs) + len(files) for root in roots for _, dirs, files in os.walk(root))


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
    _STATE.update(tally=tally, stream=stream, t0=fs_events.time.monotonic(), ran=0)


def pytest_runtest_logreport(report) -> None:
    if "tally" in _STATE and report.when == "call":
        _STATE["ran"] += 1


def pytest_sessionfinish(session, exitstatus) -> None:
    tally = _STATE.pop("tally", None)
    if tally is None:
        return
    fs_events.time.sleep(0.5)  # the stream's latency: the last records in
    _STATE.pop("stream").__exit__(None, None, None)
    rep = tally.report(fs_events.time.monotonic() - _STATE.pop("t0"), TOP)
    ran = _STATE.pop("ran")
    rep["tests_run"] = ran
    rep["teardown_entries"] = left_behind(tally.roots)
    ceiling = _ceiling()
    faults = verdict(rep, ran, ceiling)
    rep["ceiling"], rep["faults"] = ceiling, faults
    Path(_STATE.pop("out")).write_text(json.dumps(rep, indent=1) + "\n")
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    for f in faults:
        line = f"✗ file events (GOH.FSCHURN2, tests/fs_events_ceiling.json): {f}"
        (tr.write_line if tr else print)(line)
    if faults and session.exitstatus == 0:
        session.exitstatus = 1
