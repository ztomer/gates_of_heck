"""The goh suite's live share (GOH.SHAREGROW; the rule is lib/worker_share.py's header).

tools/pytest.sh starts xdist at its ceiling and names a token pool (GOH_SHARE_POOL); in each worker
this plugin takes one token around collection and around each test's whole protocol, so the run is
as wide as the share is NOW: wider the moment another gate leaves, narrower when one is admitted.
No pool (a serial run, the controller, a run with GOH_SUITE_CORES pinned): every hook is a pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_LIB = str(Path(__file__).resolve().parent.parent / "lib")
if _LIB not in sys.path:
    sys.path.append(_LIB)
import worker_share  # noqa: E402

# Read at the first hook, not at import: xdist names the worker (PYTEST_XDIST_WORKER) after the
# conftest that imports this is loaded.
TOKENS: worker_share.Tokens | None = None
_READ = False


def _tokens() -> worker_share.Tokens | None:
    global TOKENS, _READ
    if not _READ:
        TOKENS, _READ = worker_share.tokens_from_env(), True
    return TOKENS


@pytest.hookimpl(hookwrapper=True)
def pytest_collection(session):
    tokens = _tokens()
    if tokens is None:
        yield
        return
    tokens.take()
    try:
        yield
    finally:
        tokens.give()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    tokens = _tokens()
    if tokens is None:
        yield
        return
    tokens.take()
    try:
        yield
    finally:
        tokens.give()
