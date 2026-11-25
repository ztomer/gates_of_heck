"""The land gate's remote share, as pytest hooks (GOH.REMOTEGATE; the rules: lib/remote_gate.py).

The controller decides ONCE, as tests/_land_verdicts.py decides its carry: whether this run shares
(`remote_gate.off`), which Linux-safe files go (`choose`, after the carry's own skip set), and
whether it is a shadow. It launches the share and hands every xdist worker the files to deselect
(none in a shadow), so all workers deselect alike. At the session's end, before the verdict store
records it (tryfirst), the worker's verdict joins the Mac's: green, a red naming the tests that
failed on Linux, or a fallback the Mac runs now; a shadow compares both platforms' verdicts. A run
that stopped red on its own (a land's --maxfail=1) cancels the share: its verdict is not needed.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import _kept_store
import _land_verdicts as lv
import _schedule
import _whole_run
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
fast_tests = importlib.import_module("lib.fast_tests")
remote_gate = importlib.import_module("lib.remote_gate")
remote_lane = importlib.import_module("lib.remote_lane")
remote_saved = importlib.import_module("lib.remote_saved")

KEY = "goh_land_remote"  # controller -> worker: the files the worker deselects
_state: dict = {}
_mac: dict[str, bool] = {}  # a shadow's Mac verdicts: node -> passed


def _table() -> Path | None:
    return _schedule._table[0] if _schedule._table else None


def _decide(config) -> dict:
    """{"files": deselected here, "share": the running share or None, "shadow": bool}."""
    if "d" in _state:
        return _state["d"]
    if not lv._controller(config):
        raw = config.workerinput.get(KEY)
        _state["d"] = {"files": json.loads(raw) if raw else [], "share": None, "shadow": False}
        return _state["d"]
    _state["d"] = {"files": [], "share": None, "shadow": False}
    why = remote_gate.off(bool(lv._on(lv.REUSE)), _whole_run.whole(config), lv.instrumented(),
                          bool(_schedule._shard_spec), _table() is not None)  # fmt: skip
    if why:
        return _state["d"]
    with remote_gate.table(_table(), _kept_store._REAL):
        files, mac_s = remote_gate.choose(ROOT, set(lv._plan(config)["skip"]),
                                          fast_tests.load())  # fmt: skip
        if not files:
            return _state["d"]
        shadow = remote_gate.shadowed()
        share = remote_gate.start(ROOT, files, mac_s)
    _state["d"] = {"files": [] if shadow else files, "share": share, "shadow": shadow}
    _say(config, [f"land gate: {len(files)} Linux-safe test files on the remote worker"
                  + (" (a SHADOW: the Mac runs them too)" if shadow else "")])  # fmt: skip
    return _state["d"]


def _say(config, lines: list[str]) -> None:
    term = config.pluginmanager.get_plugin("terminalreporter")
    for line in lines:
        (term.write_line if term else print)(line)


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node) -> None:
    """xdist, in the controller: every worker deselects the same files."""
    node.workerinput[KEY] = json.dumps(_decide(node.config)["files"])


def pytest_sessionstart(session) -> None:
    if lv._controller(session.config):
        _decide(session.config)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items) -> None:
    files = set(_decide(config)["files"])
    if not files:
        return
    keep, drop = [], []
    for item in items:
        (drop if item.nodeid.split("::")[0] in files else keep).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


def pytest_runtest_logreport(report) -> None:
    d = _state.get("d") or {}
    if not d.get("shadow"):
        return
    if report.failed:
        _mac[report.nodeid] = False
    elif report.when == "call" and report.passed:
        _mac.setdefault(report.nodeid, True)


def _run_local(files: list[str]) -> int:
    """The worker gave the files back: the Mac runs them now, a plain child pytest (the land's
    keys left this environment when tests/_land_verdicts.py loaded: it records nothing)."""
    workers = os.environ.get("GOH_GATE_WORKERS") or "4"
    plain = ["env", "-u", _kept_store.KEPT_ENV]  # `table` named the real cache: it merges nothing
    argv = [*plain, sys.executable, "-m", "pytest", "-q", "-n", workers, *files]
    return subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL, check=False).returncode


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session) -> None:
    d = _state.get("d") or {}
    share = d.get("share")
    if share is None or not lv._controller(session.config):
        return
    if session.exitstatus not in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED):
        remote_lane.cancel(share)  # red already: the worker's verdict is not needed
        return
    with remote_gate.table(_table(), _kept_store._REAL):
        green, lines = remote_gate.finish(ROOT, share, d["shadow"], _mac, _run_local)
    _say(session.config, lines)
    # its figure beside the verdict record, for the land's log (tools/land.sh, GOH.REMOTESAVED)
    remote_saved.keep(lines, f"{lv._on(lv.REUSE)}.shares" if lv._on(lv.REUSE) else "")
    if not d["shadow"]:  # passed by ANOTHER verdict: the store's record is never `full`
        plan = lv._plan(session.config)
        plan["other"] = int(plan.get("other", 0)) + len(share.nodes)
        if session.exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED:
            session.exitstatus = pytest.ExitCode.OK  # every node went to the worker
    if not green:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
