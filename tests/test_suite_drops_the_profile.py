"""The suite never writes into the profile of the gate that runs it (GOH.LANDGAPS, 2026-11-09).

A land profile (tools/gate_profile.sh, GOH_TIMINGS set) of goh's own push gate listed a step
labelled "the step" at rc=124 -- a timeout -- while the land passed. It was not the gate's: it was
tests/test_bounded_run.py's deliberate timeout, recorded through the inherited GOH_TIMINGS by
`bounded_run.run` in the suite's own process. A profile that lists a test's fixture as a timed-out
step reads as a gate that passed through a timeout. conftest drops every profile variable at
import, before any test runs (as it drops git's).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import step_timings

PROFILE = ("GOH_TIMINGS", "GOH_TIMINGS_PARENT", "GOH_TIMINGS_KIND", "GOH_TIMINGS_CWD")


def test_importing_the_suite_drops_every_profile_variable(tmp_path: Path) -> None:
    profile = tmp_path / "timings.jsonl"
    env = dict(os.environ, **{name: str(profile) for name in PROFILE})
    probe = (
        "import os, sys; sys.path[:0] = ['tests', 'lib']; import conftest, step_timings; "
        "step_timings.record('the step', 1.0, 124, 'tree'); "
        f"print([n for n in {PROFILE!r} if n in os.environ])"
    )
    ran = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "[]", ran.stdout
    assert not profile.exists() or profile.read_text() == "", profile.read_text()


def test_the_running_suite_has_no_profile() -> None:
    assert [n for n in PROFILE if n in os.environ] == []
    assert step_timings.ENV in PROFILE
