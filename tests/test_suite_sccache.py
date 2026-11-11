"""The sccache server the suite may start is the user's, never the suite's (GOH.SCCACHETMP).

2026-11-11: a cargo inside the suite found no sccache server and started one, which inherited the
suite's `goh-suite-tmp.*` TMPDIR and outlived it; once the root was removed, every cargo build on
the machine failed "Failed to create temp dir" until someone restarted the server. So
tests/suite_tmp.py puts a `sccache` first on the suite's PATH that runs the real one under the
user's TMPDIR and HOME.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import pytest
import suite_tmp
from conftest import hermetic_env


def test_the_shim_runs_the_real_sccache_under_the_users_tmpdir_and_home(tmp_path):
    real = tmp_path / "real-sccache"
    real.write_text('#!/bin/sh\nprintf "%s|%s|%s" "$TMPDIR" "$HOME" "$*"\n')
    real.chmod(0o755)
    shim = suite_tmp.write_sccache_shim(tmp_path / "bin", str(real), "/the user's/T", "/Users/u")
    got = subprocess.run(
        [str(shim), "--start-server", "a b"],
        env={"PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path), "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    assert got.stdout == "/the user's/T|/Users/u|--start-server a b"


@pytest.mark.skipif(
    not any(
        os.path.isfile(os.path.join(d, "sccache")) for d in os.environ["PATH"].split(os.pathsep)
    ),
    reason="no sccache on this machine",
)
def test_a_suite_child_that_starts_sccache_starts_it_outside_the_suite_tmp():
    """The live suite: the `sccache` a cargo child resolves (cargo resolves the bare wrapper name
    through PATH, as hermetic children keep it) is the shim, and it names the USER's temp dir."""
    found = shutil.which("sccache", path=hermetic_env()["PATH"])
    assert found, "sccache vanished from the suite's PATH"
    with open(found, encoding="utf-8", errors="replace") as handle:
        text = handle.read(4096)
    assert "goh-suite-sccache-shim" in text, (
        f"{found} is the real sccache: a server it starts keeps the suite's TMPDIR"
    )
    suite = os.environ[suite_tmp.MARKER]
    assert tempfile.gettempdir().startswith(suite)
    assert suite not in text, "the shim hands the server the suite's own temp root"
    assert suite_tmp.real_tmp() in text
