"""An xdist worker's collection error is the run's (GOH.REMOTEREDSILENT, tests/test_xdist_usage.py).

Under xdist the WORKERS collect. A node id one of them cannot find (`not found: x.py::t[a`) ends
its collection with pytest's usage error, which it writes to its own stderr, gone; the controller
reads `0 items` and exits 5, at -qq with not one word: four land pre-steps went red on 2026-11-23
naming nothing, and a carried run maps 5 to green (tests/_land_verdicts.py), so the same id in a
run with carried nodes passed while running none of its tests. Here each worker hands its usage
error back in its output, and the controller prints it and ends the run with pytest's usage-error
exit, 4, whatever else mapped it.
"""

from __future__ import annotations

import pytest

KEY = "goh_usage_error"  # worker -> controller, in the worker's output
_said: list[str] = []


@pytest.hookimpl(wrapper=True)
def pytest_collection(session):
    try:
        return (yield)
    except pytest.UsageError as exc:
        out = getattr(session.config, "workeroutput", None)
        if out is not None:  # a worker: the controller is told; it is said there
            out[KEY] = "\n".join(str(a) for a in exc.args)
        raise


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error) -> None:
    said = (getattr(node, "workeroutput", None) or {}).get(KEY)
    if said and said not in _said:
        _said.append(said)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session) -> None:
    if not _said:
        return
    term = session.config.pluginmanager.get_plugin("terminalreporter")
    for said in _said:
        first, *rest = said.splitlines() or [""]
        for line in [f"✗ an xdist worker's collection failed: {first}", *rest]:
            (term.write_line if term else print)(line)
    session.exitstatus = pytest.ExitCode.USAGE_ERROR
