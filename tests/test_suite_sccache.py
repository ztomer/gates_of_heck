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
    baked = suite_tmp.baked_tmpdir(text)
    assert baked, f"{found} names no TMPDIR"
    # Under local_ci the step's shim (lib/step_tmp.sh) is found first and names the user's TMPDIR,
    # not this suite's real_tmp() (the step slot): the invariant is "outside every disposable
    # root", never "equal to the TMPDIR this suite started in" (GOH.SHIMNESTED).
    assert not suite_tmp.disposable(baked, suite), (
        f"the shim hands the server {baked}, a temp root a run removes"
    )


@pytest.mark.parametrize(
    ("baked", "verdict"),
    [
        ("/var/folders/ab/T/", False),
        ("/var/folders/ab/T/goh.noindex/step.00", True),
        ("/var/folders/ab/T/goh.noindex/step.02/goh.noindex/goh-pytest.slot00", True),
        ("/var/folders/ab/T/goh.noindex/goh-suite-tmp.x1y2", True),
    ],
)
def test_a_shim_naming_a_removable_temp_root_is_told_apart(tmp_path, baked, verdict):
    """The live test's instrument, calibrated both ways: a step slot or a suite root is caught,
    the user's own temp dir is not (GOH.SHIMNESTED)."""
    shim = suite_tmp.write_sccache_shim(tmp_path / "bin", "/bin/true", baked, "/Users/u")
    got = suite_tmp.baked_tmpdir(shim.read_text())
    assert got == baked
    assert suite_tmp.disposable(got, str(tmp_path / "suite")) is verdict
