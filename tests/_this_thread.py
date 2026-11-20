"""A fake for ONE thread: the time module's clock patched for the test that asked, never for the
rest of the process (GOH.LANDQWINFLAKE).

`monkeypatch.setattr(time, "sleep", fake)` (or `mod.time`, the same module object) replaces the
clock for EVERY thread of the xdist worker: tests/_drift_guard.py's `drift-load-sampler` (alive in
every worker, `time.sleep(2.0)` in a loop), a thread an earlier test left running, or a library's
own then drives the test's fake clock, fires its scheduled events on the wrong thread, or waits on
a frozen `monotonic`. That is how `[['T.ONE'], ['T.TWO']]` came out
of tests/test_landq_window.py at load 116-138 and never alone.

The first choice is no patch at all: inject the clock (lib/landq_window.py `hold`, tools/landq.py
`drain_held`). Where the sleeper is code the test cannot reach (CPython's own subprocess wait),
`this_thread(fake, real)` is the value to patch with: FAKE on the patching thread, REAL on every
other. tests/test_no_global_clock_patch.py refuses any other value.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


def this_thread(fake: Callable[..., Any], real: Callable[..., Any]) -> Callable[..., Any]:
    """FAKE when called from the thread that made this, REAL from any other."""
    mine = threading.get_ident()

    def call(*args: Any, **kwargs: Any) -> Any:
        return (fake if threading.get_ident() == mine else real)(*args, **kwargs)

    return call
