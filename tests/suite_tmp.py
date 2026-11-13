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

A SHARED DAEMON MUST NOT INHERIT A PRIVATE TEMP DIR (GOH.SCCACHETMP). Every cargo the suite runs
goes through sccache (the user's `rustc-wrapper`), and the first compile with no server running
STARTS the machine's one sccache server -- which inherits this TMPDIR and HOME and outlives the
suite. 2026-11-11: a server started inside a `goh-suite-tmp.*` root kept running after the root was
removed, and every cargo build on the machine failed "Failed to create temp dir" until it was
restarted by hand; five agents hit it. So the suite's PATH leads with a `sccache` that runs the real
one under the user's own TMPDIR and HOME (`write_sccache_shim`): whichever test starts the server,
it starts the server a user would have started, with the user's sccache config. The class, stated:
a process the suite may start but does not own -- a server, a daemon, an agent -- gets the user's
environment, never the suite's disposable one. Pinned by tests/test_suite_sccache.py.

THE ROOT NEVER OUTLIVES ITS RUN (GOH.ENVDUMP). `atexit` is skipped by SIGKILL, SIGTERM and SIGHUP,
so a killed bare-pytest run left its `goh-suite-tmp.*` root behind (2026-11-12 17:10, holding a
test's dump of the whole environment). A reaper process waiting on a pipe only this process holds
removes the root when the pipe closes, however the run ends. Pinned by
tests/test_suite_tmp_reaped.py, every arm red before the reaper.
"""

from __future__ import annotations

import atexit
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MARKER = "GATES_OF_HECK_SUITE_TMP"  # not GOH_-prefixed: no env scrub drops it
REAL_TMP_MARKER = "GATES_OF_HECK_SUITE_REAL_TMP"


def real_tmp() -> str:
    return os.environ.get(REAL_TMP_MARKER) or tempfile.gettempdir()


SHIM_MARK = "# goh-suite-sccache-shim"


def write_sccache_shim(bin_dir: Path, real: str, tmpdir: str, home: str) -> Path:
    """`bin_dir/sccache`: the real `real`, run with the user's TMPDIR and HOME baked in -- baked,
    so a child that scrubs its environment to PATH and HOME still reaches them."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "sccache"
    q = shlex.quote
    shim.write_text(
        f"#!/bin/sh\n{SHIM_MARK}\nTMPDIR={q(tmpdir)} HOME={q(home)}\n"
        f'export TMPDIR HOME\nexec {q(real)} "$@"\n'
    )
    shim.chmod(0o755)
    return shim


def _is_shim(path: str | None) -> bool:
    try:
        with open(path or "", encoding="utf-8") as handle:
            return SHIM_MARK in handle.read(200)
    except (OSError, UnicodeDecodeError):
        return False


def _shim_sccache(tmp: Path, real_tmp_dir: str) -> None:
    found = shutil.which("sccache")
    if not found or _is_shim(found):  # none to guard, or an outer suite already did
        return
    home = os.environ.get("GATES_OF_HECK_SUITE_REAL_HOME") or os.path.expanduser("~")
    bin_dir = tmp / "goh-suite-bin"
    write_sccache_shim(bin_dir, os.path.realpath(found), real_tmp_dir, home)
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


# The reaper: blocks on the pipe until EVERY write end is closed, which the kernel does however
# the suite ends (SIGKILL included), then removes the root. `-S`: no site, so no user code runs.
REAPER = (
    "import os, shutil, sys\n"
    "fd = int(sys.argv[1])\n"
    "while os.read(fd, 64):\n"
    "    pass\n"
    "shutil.rmtree(sys.argv[2], ignore_errors=True)\n"
)


def _reap_on_exit(tmp: Path) -> None:
    """Remove `tmp` when this process ends, however it ends (GOH.ENVDUMP): `atexit` alone never
    ran on a SIGKILL, SIGTERM or SIGHUP, and a killed run's root outlived it holding what its
    tests wrote. The write end stays in this process only (os.pipe's fds are not inheritable, so
    no xdist worker or gate child keeps the reaper waiting); the reaper is in a session of its
    own, so a signal to the suite's process group does not take it down with the suite."""
    (tmp / ".goh-owner").write_text(f"{os.getpid()}\n")  # the backstop sweep skips a live root
    read_end, write_end = os.pipe()
    try:
        _REAPER.append(_spawn_reaper(read_end, tmp))
    finally:
        os.close(read_end)
    _REAPER_PIPE.append(write_end)  # held open for the life of the process, never closed by hand


_REAPER_PIPE: list[int] = []
_REAPER: list[subprocess.Popen] = []


def _spawn_reaper(read_end: int, tmp: Path) -> subprocess.Popen:
    """Never waited on, by design: it outlives this process and exits when the pipe closes."""
    return subprocess.Popen(
        [sys.executable, "-S", "-c", REAPER, str(read_end), str(tmp)],
        pass_fds=(read_end,),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


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
        _reap_on_exit(tmp)
        os.environ["PYTEST_DEBUG_TEMPROOT"] = str(tmp)
    os.environ[REAL_TMP_MARKER] = real
    _shim_sccache(tmp, real)
    os.environ[MARKER] = str(tmp)
    os.environ["TMPDIR"] = str(tmp)
    tempfile.tempdir = None  # gettempdir() caches; the next call reads TMPDIR again


_enter()
