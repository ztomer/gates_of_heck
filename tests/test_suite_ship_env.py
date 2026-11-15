"""A caller's GOH_* never reach a test (tests/suite_home.py, `_drop_caller_goh`).

`goh ship` exports GOH_SHIP_SUMMARY (and its siblings) to the release step, whose push gate runs
this suite; release.sh titles its cut with it, so v0.97.0's release gate cut four fixture kits'
CHANGELOGs under the real release's summary and went red. The class is wider than GOH_SHIP_*: the
same ship's export leaked GOH_LIVE_ROOT into v0.119.0's release suite (GOH.LIVEROOTLEAK), so the
entry drops EVERY GOH_* but the gate's declared inputs (suite_home.CALLER_INPUTS).
"""

from __future__ import annotations

import os
import subprocess
import sys

import suite_home
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


STRAYS = {
    "GOH_LIVE_ROOT": "/a/ship/s/main/checkout",  # the v0.119.0 leak
    "GOH_DIR": "/a/ship/s/export",
    "GOH_RESOLVED_FOR": "/elsewhere|live:",
    "GOH_RESOLVED_BIN": "/elsewhere/bin/goh",
    "GOH_CHANGE_BASE": "abc123",
    "GOH_SHIP_SUMMARY": "a real release's title",
    "GOH_SOMETHING_NOBODY_NAMED_YET": "1",
}


def _entered(tmp_path, **extra: str) -> dict[str, str]:
    """The GOH_* a fresh suite entry leaves, under `extra` from its caller."""
    env = hermetic_env(**extra)
    env.pop("GATES_OF_HECK_SUITE_REAL_HOME", None)  # enter afresh, as a ship's suite does
    probe = (
        "import json, os, sys; sys.path.insert(0, sys.argv[1]); import suite_home; "
        "print(json.dumps({k: v for k, v in os.environ.items() if k.startswith('GOH_')}))"
    )
    got = subprocess.run(
        [sys.executable, "-c", probe, str(REPO_ROOT / "tests")],
        env=env, cwd=tmp_path, capture_output=True, text=True, check=True,
        stdin=subprocess.DEVNULL,
    )  # fmt: skip
    return __import__("json").loads(got.stdout)


def test_a_suite_under_stray_goh_variables_enters_as_one_without_them(tmp_path):
    assert _entered(tmp_path, **STRAYS) == _entered(tmp_path)


def test_the_entry_keeps_only_the_gates_inputs_and_sets_its_own_root(tmp_path):
    inputs = {name: f"/x/{name}" for name in suite_home.CALLER_INPUTS}
    got = _entered(tmp_path, **STRAYS, **inputs)
    assert {k: got.get(k) for k in inputs} == inputs, "a declared input was dropped"
    assert "GOH_LIVE_ROOT" not in got
    assert got["GOH_DIR"] == str(REPO_ROOT), "the suite's GOH_DIR is its own checkout"
