"""The ship/land deadlock of 2026-11-15, reproduced with short bounds (GOH.LOCKORDER).

goh v0.122.0's ship held the land lock and its release step's loaded suite waited 1800 s for the
load slot, which a land held: `load.py 0 -- bash tools/land.sh`, a gate sharing the slot while it
queued on the land lock. Ship: land, then load. Land: load, then land. Here the SHIP is the real
lib/land_lock.py hold (what tools/ship.py's worker takes) followed by the real tools/load.py with a
spinner and a 5 s --wait-max (the loaded suite's call, bounded); the LAND is the real land.sh.

Fixed by ONE order (lib/lock_order.tsv: land 20 before load 30): a land started inside a load
holder is refused at once, naming both locks, so the ship's experiment gets the slot; and land.sh
run bare takes the land lock FIRST and the slot for its gate after, so a queued land holds nothing.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git, hermetic_env
from timing_bounds import GiveUp, patience

LAND = REPO_ROOT / "tools" / "land.sh"
LOAD = REPO_ROOT / "tools" / "load.py"
# The ship's half: the land lock held through lib/land_lock.py, then the loaded suite's slot claim.
SHIP = f"""
import subprocess, sys, time
from pathlib import Path
sys.path.insert(0, {str(REPO_ROOT / "lib")!r})
import land_lock
lock = land_lock.path(Path(sys.argv[1]))
with land_lock.held(lock, "goh ship test", print):
    print("SHIP HOLDS LAND", flush=True)
    Path(sys.argv[2]).write_text("held")
    while not Path(sys.argv[3]).exists():
        time.sleep(0.05)
    rc = subprocess.run([sys.executable, {str(LOAD)!r}, "--background-ok", "test", "--wait",
                         "--wait-max", "5s", "--max", "60s", "1", "--", "true"],
                        stdin=subprocess.DEVNULL).returncode
    print(f"SHIP LOAD RC {{rc}}", flush=True)
sys.exit(rc)
"""


def _branch(repo: Path, tmp_path: Path) -> Path:
    wt = tmp_path / "wt-lane"
    git(repo, "worktree", "add", "-q", "-b", "lane", str(wt))
    (wt / "lane.txt").write_text("lane\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "--no-verify", "-m", "lane")
    return wt


def _env(tmp_path: Path) -> dict[str, str]:
    load_dir = tmp_path / "load"
    load_dir.mkdir(exist_ok=True)
    return hermetic_env(drop_git=True, GOH_LAND_GATE="true", GOH_LOAD_DIR=str(load_dir))


def _ship(repo: Path, tmp_path: Path) -> subprocess.Popen[str]:
    held, go = tmp_path / "ship.held", tmp_path / "ship.go"
    ship = subprocess.Popen([sys.executable, "-c", SHIP, str(repo), str(held), str(go)],
                            env=_env(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, stdin=subprocess.DEVNULL)  # fmt: skip
    give_up = GiveUp(30)
    while not held.exists():
        assert ship.poll() is None and not give_up.passed(), ship.communicate()[0]
        time.sleep(0.05)
    return ship


def _until_queued(land: subprocess.Popen[str], tmp_path: Path, needle: str) -> None:
    """Wait until the land's log shows NEEDLE (it is queued on the land lock) or it exits."""
    log = tmp_path / "land.log"
    give_up = GiveUp(30)
    while needle not in log.read_text() and land.poll() is None:
        assert not give_up.passed(), log.read_text()
        time.sleep(0.05)


@pytest.mark.load_sensitive
def test_a_land_inside_a_load_slot_cannot_deadlock_a_ship(repo: Path, tmp_path: Path) -> None:
    """Today's invocation, `load.py 0 -- bash tools/land.sh`, while a ship holds the land lock:
    the ship's loaded suite must get the slot (it timed out at 1800 s on 2026-11-15)."""
    wt = _branch(repo, tmp_path)
    ship = _ship(repo, tmp_path)
    with (tmp_path / "land.log").open("w") as log:
        land = subprocess.Popen([sys.executable, str(LOAD), "--background-ok", "land gate",
                                 "--max", "60s", "0", "--", "bash", str(LAND)],
                                cwd=wt, env=_env(tmp_path), stdout=log, stderr=subprocess.STDOUT,
                                text=True, stdin=subprocess.DEVNULL)  # fmt: skip
        try:
            _until_queued(land, tmp_path, "land lock")
            (tmp_path / "ship.go").write_text("go")
            out, _ = ship.communicate(timeout=patience(60))
        finally:
            land.kill()
            land.wait()
    assert ship.returncode == 0, out
    said = (tmp_path / "land.log").read_text()
    assert "refusing to wait on 'land'" in said and "'load'" in said, said


@pytest.mark.load_sensitive
def test_a_bare_land_queued_behind_a_ship_holds_nothing_and_lands_after(
    repo: Path, tmp_path: Path
) -> None:
    """The invocation now: `bash tools/land.sh`. It queues on the land lock holding nothing, the
    ship's experiment takes the slot, and once the ship is done the land gates in its own slot."""
    wt = _branch(repo, tmp_path)
    ship = _ship(repo, tmp_path)
    with (tmp_path / "land.log").open("w") as log:
        land = subprocess.Popen(["bash", str(LAND)], cwd=wt, env=_env(tmp_path), stdout=log,
                                stderr=subprocess.STDOUT, text=True,
                                stdin=subprocess.DEVNULL)  # fmt: skip
        _until_queued(land, tmp_path, "waiting for the land lock")
        (tmp_path / "ship.go").write_text("go")
        out, _ = ship.communicate(timeout=patience(60))
        assert ship.returncode == 0, out
        assert land.wait(timeout=patience(60)) == 0, (tmp_path / "land.log").read_text()
    said = (tmp_path / "land.log").read_text()
    assert "landed" in said and "load:" in said, said  # the gate ran under the load slot
