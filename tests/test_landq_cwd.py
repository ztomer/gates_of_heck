"""tools/landq.py's working directory (GOH.LANDERCWD): the lander lives in its own state dir.

The lander kept the directory of whoever started it -- often a lane worktree -- and re-ran itself
there when its code changed. When that worktree was removed (finished lanes are cleaned), the
lander logged `getcwd: cannot access parent directories` (lander.log 2026-11-17, three times) and
every relative path or child inheriting the directory broke. The lander now changes to its state
dir when it starts and before every re-run, and each land names its repo and worktree explicitly.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from test_landq_batch import FAKE_LOOPCTL, GATE, enqueue_all, lane, recorded

sys.path.insert(0, str(REPO_ROOT / "lib"))
from timing_bounds import patience

LANDQ = REPO_ROOT / "tools" / "landq.py"
START = os.getcwd()  # the directory this worker collects (and so runs) from
# Start the lander from a directory, delete the directory, THEN exec it: its cwd is a dead inode.
DOOMED = (
    "import os, sys; d = sys.argv[1]; os.chdir(d); os.rmdir(d); "
    "os.execv(sys.executable, [sys.executable, sys.argv[2], '--drain'])"
)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    return hermetic_env(
        drop_git=True,
        GOH_LANDQ_DIR=str(tmp_path / "landq"),
        GOH_LOOPCTL=str(fake),
        FAKE_LOOPCTL_LOG=str(tmp_path / "loopctl.log"),
        GOH_LAND_TIMEOUT="120",
        GOH_LAND_GATE=GATE,
        GATE_LOG=str(tmp_path / "gate.log"),
    )


def landq_module():
    spec = importlib.util.spec_from_file_location("landq_cwd_probe", LANDQ)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_lander_started_in_a_directory_that_is_then_deleted_keeps_draining(repo, tmp_path, env):
    wts = [
        (lane(repo, tmp_path, "one", {"one.txt": "1\n"}), "T.ONE"),
        (lane(repo, tmp_path, "two", {"two.txt": "2\n"}), "T.TWO"),
    ]
    env = {**env, "GOH_LANDQ_BATCH": "1"}  # each land runs on its own: its child inherits the cwd
    enqueue_all(env, wts)
    doomed = tmp_path / "gone-lane"
    doomed.mkdir()
    argv = [sys.executable, "-c", DOOMED, str(doomed), str(LANDQ)]
    done = subprocess.run(argv, env=env, capture_output=True, text=True, check=False,
                          stdin=subprocess.DEVNULL, timeout=patience(300))  # fmt: skip
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    got = recorded(env)
    assert all(got[item]["state"] == "landed" for _, item in wts), (got, out)
    logs = [out, *(Path(got[item]["log"]).read_text() for _, item in wts)]
    assert not any("getcwd" in text for text in logs), logs


def test_a_lander_re_running_its_fresh_code_does_so_from_its_state_dir(tmp_path, monkeypatch):
    lq = landq_module()
    state, doomed = tmp_path / "state", tmp_path / "gone-lane"
    doomed.mkdir()
    monkeypatch.setenv("GOH_LANDQ_DIR", str(state))
    monkeypatch.chdir(doomed)
    doomed.rmdir()  # the lander's own directory is gone before it ever looks
    monkeypatch.setattr(lq, "code_digest", lambda: "old")
    monkeypatch.setattr(lq, "drain_held", lambda q, digest="": False)
    ran: dict[str, str] = {}

    class Exec(Exception):
        pass

    def execve(path, argv, env=None):
        ran["cwd"] = os.getcwd()
        raise Exec

    monkeypatch.setattr(lq.os, "execve", execve)
    with pytest.raises(Exec):
        lq.drain()
    assert ran["cwd"] == str(state.resolve()), ran


def test_an_in_process_drain_moves_this_process_to_the_state_dir(tmp_path, monkeypatch):
    lq = landq_module()
    monkeypatch.setenv("GOH_LANDQ_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(lq, "drain_held", lambda q, digest="": True)
    assert lq.drain() == 0
    assert os.getcwd() == str((tmp_path / "state").resolve())


def test_the_next_test_finds_the_directory_the_one_before_it_found():
    """conftest's `_a_test_leaves_the_directory_it_found`: the drain above left no mark here."""
    assert os.getcwd() == START
