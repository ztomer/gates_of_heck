"""The session's binary from a plain helper (imported by conftest, which re-exports both names).

`native_goh_path()` lets a kit's function with no fixture of its own reach the `native_goh`
fixture through the running test's request. Both routes to the binary MARK the test
(tests/_land_verdicts.py): in the coverage step's instrumented run its run of the binary is also
the crate's coverage, so a land's reused verdict never stands in for it (GOH.LANDPREDUP).
"""

from __future__ import annotations

from pathlib import Path

import _land_verdicts
import pytest

_REQUEST: dict[str, pytest.FixtureRequest] = {}


@pytest.fixture(autouse=True)
def _remember_request(request: pytest.FixtureRequest):
    """Lets a plain helper (a kit's function) reach the session's binary: `native_goh_path()`."""
    _REQUEST["now"] = request
    if "native_goh" in request.fixturenames:  # asked for the binary: its run is coverage too
        _land_verdicts.mark_binary(request.node)
    yield
    _REQUEST.pop("now", None)


def native_goh_path() -> Path:
    """The session's native `goh`, from a helper with no fixture of its own; built on first use."""
    _land_verdicts.mark_binary(_REQUEST["now"].node)
    return _REQUEST["now"].getfixturevalue("native_goh")
