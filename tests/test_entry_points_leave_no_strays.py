"""goh's land queue entry points leave nothing in a work tree (GOH.CLASS.CHECKOUT).

The class: goh's own locks, stages and temp files written INTO the checkouts it serves
(`bin/.build.lock`, `bin/goh.<pid>.staging`, a ship result beside the repo) read as "N uncommitted"
on a claim card and failed ships. tools/land.sh (lock and landed record in the common git dir) and
goh ship (result and logs under ~/.local/state or --out, its verify export under a temp root) are
run here end to end in a scratch repository with a bare origin and a lane worktree, and every
work tree must hold no file git does not track, ignored or not.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import test_ship
from conftest import REPO_ROOT, git, hermetic_env
from test_ship import SHIP, _env

shipped = test_ship.shipped  # the fixture, by assignment
LAND = REPO_ROOT / "tools" / "land.sh"


def _strays(tree: Path) -> list[str]:
    out = git(tree, "status", "--porcelain", "--untracked-files=all", "--ignored")
    return [ln for ln in out.splitlines() if ln.strip()]


def test_land_then_ship_leave_no_file_in_any_work_tree(shipped: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt-lane"
    git(shipped, "worktree", "add", "-q", "-b", "lane", str(wt))
    (wt / "lane.txt").write_text("lane\n")
    git(wt, "add", "lane.txt")
    git(wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-verify", "-m",
        "feat: lane")  # fmt: skip
    land = subprocess.run(["bash", str(LAND)], cwd=wt, capture_output=True, text=True, timeout=120,
                          env=hermetic_env(drop_git=True, GOH_LAND_GATE="true"),
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert land.returncode == 0, land.stdout + land.stderr
    ship = subprocess.run([sys.executable, str(SHIP), "--repo", str(shipped), "--wait"],
                          env=_env(tmp_path, HOME=str(tmp_path / "home")), capture_output=True,
                          text=True, timeout=120, stdin=subprocess.DEVNULL,
                          check=False)  # fmt: skip
    assert ship.returncode == 0, ship.stdout + ship.stderr
    assert {"main": _strays(shipped), "lane": _strays(wt)} == {"main": [], "lane": []}
