"""The suite's file events are counted and held under a ceiling (GOH.FSCHURN2, tests/_fs_churn.py).

GOH.FSCHURN's cut did not hold and nothing saw it go: the suite was back at ~800 fresh directories
a second two days later. These pin the instrument (lib/fs_events.py reads an FSEvents stream and
names who made each event) and the ceiling: a planted churny test in a session the plugin watches
turns it red, naming the test; a quiet one stays green and its count is written.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import fs_events
from _fs_churn import verdict

darwin = pytest.mark.skipif(sys.platform != "darwin", reason="FSEvents is macOS only")

CHURNY = (
    "import os\n"
    "def test_churny(tmp_path):\n"
    "    for i in range(400):\n"
    "        d = tmp_path / f'd{i}'\n"
    "        d.mkdir()\n"
    "        (d / 'f').write_text('x')\n"
)
QUIET = "def test_quiet(tmp_path):\n    (tmp_path / 'f').write_text('x')\n"


def _session(tmp_path: Path, body: str, ceiling: dict) -> tuple[subprocess.CompletedProcess, dict]:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_planted.py").write_text(body)
    tmp, home = tmp_path / "t", tmp_path / "h"
    tmp.mkdir()
    home.mkdir()
    out = tmp_path / "events.json"
    env = hermetic_env(
        TMPDIR=str(tmp), HOME=str(home), PYTEST_DEBUG_TEMPROOT=str(tmp),
        GATES_OF_HECK_FS_EVENTS=str(out), GATES_OF_HECK_FS_EVENTS_CEILING=json.dumps(ceiling),
        PYTHONPATH=str(REPO_ROOT / "tests"),
    )  # fmt: skip
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
         "-p", "_fs_churn", "test_planted.py"],
        cwd=tmp_path, capture_output=True, text=True, timeout=patience(120), check=False,
        env=env, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    return r, json.loads(out.read_text())


@darwin
def test_a_planted_churny_test_breaks_the_per_test_ceiling(tmp_path: Path) -> None:
    r, rep = _session(tmp_path, CHURNY, {"per_test": 300, "per_test_run": 10_000})
    assert r.returncode == 1, r.stdout[-1500:] + r.stderr[-1500:]
    assert "test_churny made" in r.stdout and "per-test ceiling 300" in r.stdout, r.stdout[-1500:]
    assert rep["per_test"]["test_churny"] >= 400, rep  # one record per dir at least
    assert rep["dirs_created"] >= 400 and rep["faults"], rep


@darwin
def test_a_planted_churny_test_breaks_the_per_run_ceiling(tmp_path: Path) -> None:
    r, _ = _session(tmp_path, CHURNY, {"per_test": 100_000, "per_test_run": 200})
    assert r.returncode == 1, r.stdout[-1500:] + r.stderr[-1500:]
    assert "over the ceiling 200 each" in r.stdout, r.stdout[-1500:]


@darwin
def test_a_quiet_session_is_green_and_its_count_is_written(tmp_path: Path) -> None:
    r, rep = _session(tmp_path, QUIET, {"per_test": 300, "per_test_run": 200})
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    assert rep["tests_run"] == 1 and rep["faults"] == [] and rep["dropped"] == 0, rep
    assert 1 <= rep["events"] < 50, rep  # the stream saw the test's own write, and little else


def test_the_variable_is_popped_so_a_nested_session_records_nothing() -> None:
    assert os.environ.get("GATES_OF_HECK_FS_EVENTS") is None  # this session's controller took it


def test_creator_names_the_test_the_prefix_or_the_reused_dir() -> None:
    root = "/T/goh-pytest.slot03"
    roots = [root, "/H/home.slot07"]
    assert fs_events.creator(f"{root}/pytest-of-u/pytest-0/popen-gw2/test_foo_bar0/a/b", roots) == (
        "goh-pytest.slotN/pytest-N/test_foo_bar"
    )
    assert fs_events.creator(f"{root}/goh-display-seam.yz6xpt8t/case01/UI", roots) == (
        "goh-pytest.slotN/goh-display-seam.*"
    )
    assert fs_events.creator(f"{root}/goh-test-git.gw4.0/.git/HEAD", roots) == (
        "goh-pytest.slotN/goh-test-git.<worker>"
    )
    assert fs_events.creator("/H/home.slot07/.gitconfig", roots) == "home.slotN/.gitconfig"
    assert fs_events.creator("/elsewhere/x", roots) == "(outside the roots)"


def test_the_verdict_names_each_broken_ceiling() -> None:
    rep = {"events": 9000, "per_test": {"test_a": 50, "test_b": 7000}}
    assert verdict(rep, 10, {"per_test": 8000, "per_test_run": 1000}) == []
    faults = verdict(rep, 10, {"per_test": 5000, "per_test_run": 500})
    assert len(faults) == 2 and "test_b made 7000" in faults[0] and "900.0 each" in faults[1]


def test_the_recorded_ceiling_is_a_ratchet_with_both_bounds() -> None:
    ceiling = json.loads((REPO_ROOT / "tests" / "fs_events_ceiling.json").read_text())
    assert {"per_test", "per_test_run"} <= ceiling.keys(), ceiling
    assert all(isinstance(ceiling[k], int) and ceiling[k] > 0 for k in ("per_test", "per_test_run"))
