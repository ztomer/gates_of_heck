"""A ship's run inputs never reach a test (tests/suite_home.py, `_drop_ship_inputs`).

`goh ship` exports GOH_SHIP_SUMMARY (and its siblings) to the release step, whose push gate runs
this suite; release.sh titles its cut with it, so v0.97.0's release gate cut four fixture kits'
CHANGELOGs under the real release's summary and went red.
"""

from __future__ import annotations

import os
import subprocess
import sys

from conftest import REPO_ROOT, hermetic_env


def test_the_suite_holds_no_ship_variable():
    assert not [k for k in os.environ if k.startswith("GOH_SHIP_")]


def test_a_suite_started_by_a_ship_drops_its_inputs(tmp_path):
    """The entry itself, under a ship's environment: the suite's first import clears them."""
    env = hermetic_env(GOH_SHIP_SUMMARY="a real release's title", GOH_SHIP_ITEM="X.Y")
    env.pop("GATES_OF_HECK_SUITE_REAL_HOME", None)  # enter afresh, as a ship's suite does
    probe = (
        "import os, sys; sys.path.insert(0, sys.argv[1]); import suite_home; "
        "print(sorted(k for k in os.environ if k.startswith('GOH_SHIP_')))"
    )
    got = subprocess.run(
        [sys.executable, "-c", probe, str(REPO_ROOT / "tests")],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    assert got.stdout.strip() == "[]", got.stdout
