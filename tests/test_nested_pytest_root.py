"""A pytest a test starts numbers its dirs in a root of its own, not this session's (GOH.FLAKY2).

pytest numbers every session's dirs `pytest-N` under `<root>/pytest-of-<user>` and, at each
session's exit, deletes the old ones and unlinks a dead `pytest-current`. suite_tmp.py handed every
pytest a test starts the SUITE's root (PYTEST_DEBUG_TEMPROOT, inherited), so the nested sessions of
tests on different xdist workers rotated one directory at once: two unlinked the same dead
`pytest-current` and the second died `FileNotFoundError` at its exit (main, 2026-11-19 16:30,
test_temp_claimable.py::test_the_session_start_runs_the_sweep; 1 in 40 sessions here at load 55).
Each worker now hands its children a root under its own basetemp (suite_tmp.nested_root): one
worker runs one test at a time, so the sessions in it never overlap.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience


def _numbered(path: Path) -> Path:
    """`<root>/pytest-of-<user>`: the directory a session numbers `path`'s pytest-N in."""
    return next(p for p in path.parents if p.name.startswith("pytest-of-"))


def test_a_pytest_a_test_starts_numbers_its_dirs_outside_this_sessions_root(
    tmp_path: Path, tmp_path_factory
) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")  # the nested session's rootdir: not the repo
    seen = tmp_path / "seen"
    (tmp_path / "test_child.py").write_text(
        "import os\n"
        "def test_child(tmp_path):\n"
        "    open(os.environ['GOH_FLAKY2_SEEN'], 'w').write(str(tmp_path))\n"
    )
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
         "-p", "no:xdist", "test_child.py"],
        cwd=tmp_path, capture_output=True, text=True, timeout=patience(120), check=False,
        env=hermetic_env(GOH_FLAKY2_SEEN=str(seen)), stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    child = Path(seen.read_text())
    ours = _numbered(tmp_path_factory.getbasetemp() / "x")
    assert _numbered(child) != ours, child  # its rotation can delete nothing of this run
    assert tmp_path_factory.getbasetemp() in child.parents, child  # and it goes with this worker's


def test_two_pytests_a_test_starts_in_turn_never_share_a_basetemp(tmp_path: Path) -> None:
    """GOH.NESTEDBASETEMP: a green pytest under `tmp_path_retention_policy=failed` (every suite run,
    gates/_pytest_root.sh) REMOVES its `pytest-N`, so the next pytest a test of this worker starts
    took the same `pytest-N` and every tmp_path of the first again -- while goh.db, in the suite's
    home, outlived both. tests/test_suite_shard.py's `1/1` session learned the planted map judge as
    a guard of its queue `.../pytest-0/test_a_red_cheap_judge_is_refu0/landq`; the `1/2` session on
    the same worker had that queue's path, its guard check refused first, and the enqueue exited 1,
    not 2 (land 01795514937725081000, 2026-11-24). Each pytest a test starts has its own root."""
    plug = tmp_path / "plug"
    plug.mkdir()
    (plug / "goh_seen_basetemp.py").write_text(
        "import os\n"
        "def pytest_runtest_setup(item):\n"
        "    with open(os.environ['GATES_OF_HECK_SEEN_BASETEMP'], 'a') as f:\n"
        "        f.write(str(item.config._tmp_path_factory.getbasetemp()) + '\\n')\n"
    )
    seen = tmp_path / "seen"
    env = hermetic_env(GATES_OF_HECK_SEEN_BASETEMP=str(seen), PYTHONPATH=str(plug),
                       PYTEST_ADDOPTS="-o tmp_path_retention_policy=failed")  # fmt: skip
    node = "tests/test_nested_pytest_root.py::test_numbered_is_the_pytest_of_directory"
    for _ in range(2):
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n0",
             "-p", "goh_seen_basetemp", node],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=patience(300), check=False,
            env=env, stdin=subprocess.DEVNULL,
        )  # fmt: skip
        assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    first, second = seen.read_text().split()
    assert first != second, first  # the second session's tmp paths are none of the first's
    root = Path(os.environ["PYTEST_DEBUG_TEMPROOT"]).resolve()
    assert root in Path(first).resolve().parents, (first, root)  # still under this worker's root


def test_numbered_is_the_pytest_of_directory(tmp_path: Path) -> None:
    assert _numbered(tmp_path / "x").name.startswith("pytest-of-")
