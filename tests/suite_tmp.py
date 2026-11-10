"""The suite's children write their temp files in the SUITE's temp root (imported by suite_home,
which conftest.py imports first, before anything reads TMPDIR).

THE CLASS (CI.PUBLIC2). A gate keeps what a red run wrote, on purpose: local_ci.sh keeps its
`goh-local-ci.*` log directory when a step fails, because that log is the only record of why. The
suite runs local_ci.sh with failing steps by design -- dozens of tests -- so every run left those
directories in the USER's temp dir, beside the owner's real failed runs, where only prune_kept's
bound (the newest 20, then the 12-hour backstop) ever removed them. A test that remembered to set
TMPDIR was clean; the rest leaked. So TMPDIR points at a directory the suite owns for the whole
session, and a new test that runs a gate is contained without anyone remembering it:

  * under tools/pytest.sh it IS `$PYTEST_DEBUG_TEMPROOT`, the root the runner removes after a green
    suite and keeps after a red one -- the kept logs stay with the failure they explain;
  * under a bare `pytest`, a `goh-suite-tmp.*` directory of its own, which becomes the
    PYTEST_DEBUG_TEMPROOT too, removed at exit.

Either way every tmp_path is UNDER TMPDIR, as it is outside the suite: install.sh tells a scratch
repo by exactly that (tests/test_estate_registry.py).

The user's own temp dir is kept by name (REAL_TMP_MARKER), for the one thing that must still see it:
the session-start sweep of what killed runs left there (tests/_tree_guard.py).
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

MARKER = "GATES_OF_HECK_SUITE_TMP"  # not GOH_-prefixed: no env scrub drops it
REAL_TMP_MARKER = "GATES_OF_HECK_SUITE_REAL_TMP"


def real_tmp() -> str:
    return os.environ.get(REAL_TMP_MARKER) or tempfile.gettempdir()


def _enter() -> None:
    # An xdist worker inherits the controller's; a suite a test starts with a TMPDIR of its own
    # (test_temp_claimable.py's sweep) enters afresh, with that TMPDIR as its real one.
    if os.environ.get(MARKER) and os.environ.get(MARKER) == os.environ.get("TMPDIR"):
        return
    real = tempfile.gettempdir()
    root = os.environ.get("PYTEST_DEBUG_TEMPROOT")
    if root and os.path.isdir(root):
        tmp = Path(root)
    else:
        tmp = Path(tempfile.mkdtemp(prefix="goh-suite-tmp.", dir=real))
        atexit.register(shutil.rmtree, tmp, True)
        os.environ["PYTEST_DEBUG_TEMPROOT"] = str(tmp)
    os.environ[REAL_TMP_MARKER] = real
    os.environ[MARKER] = str(tmp)
    os.environ["TMPDIR"] = str(tmp)
    tempfile.tempdir = None  # gettempdir() caches; the next call reads TMPDIR again


_enter()
