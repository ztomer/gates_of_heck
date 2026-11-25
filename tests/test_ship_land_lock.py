"""goh ship takes the land queue (GOH.CLASS.CHECKOUT, after GOH.LANDSERIAL).

A ship pushes, releases (goh's kit lands a release commit) and installs from the main checkout,
the tree a lane's tools/land.sh fast-forwards. land.sh held `goh-land.lock` and the ship held
nothing, so a land could move main between the ship's push and its release. The ship worker now
holds the same lock (lib/land_lock.py): a ship started while a land holds it waits, naming the
holder, and pushes nothing until it is free; a land the ship's own release step runs is the ship's
land and does not wait on it (else goh's own release would deadlock its ship).
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import test_ship
from conftest import REPO_ROOT, git
from test_ship import SHIP, _env, _result
from timing_bounds import patience

shipped = test_ship.shipped  # the fixture, by assignment
LAND = REPO_ROOT / "tools" / "land.sh"


def _popen(repo: Path, tmp_path: Path, **env: str) -> subprocess.Popen[str]:
    argv = [sys.executable, str(SHIP), "--repo", str(repo), "--out", str(tmp_path / "ship.json"),
            "--wait"]  # fmt: skip
    return subprocess.Popen(argv, env=_env(tmp_path, **env), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            stdin=subprocess.DEVNULL)  # fmt: skip


def _remote_main(repo: Path) -> str:
    return git(repo, "ls-remote", "origin", "refs/heads/main").split()[0]


@pytest.mark.load_sensitive
def test_a_ship_waits_while_a_land_holds_the_lock(shipped: Path, tmp_path: Path) -> None:
    head = git(shipped, "rev-parse", "HEAD").strip()
    lock = shipped / ".git" / "goh-land.lock"
    with lock.open("a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        held.write(f"lane-x @ 0123abcd (pid {os.getpid()}, since 00:00:00)\n")
        held.flush()
        p = _popen(shipped, tmp_path)
        assert p.stdout is not None
        overdue = threading.Timer(patience(120), p.kill)  # neither waiting nor done: ends the read
        overdue.start()
        said: list[str] = []  # up to the wait it says, or its end: an unqueued ship runs to it
        for line in p.stdout:
            said.append(line)
            if "waiting for the land lock" in line:
                break
        pushed_while_held = _remote_main(shipped) == head
        steps_while_held = list(_result(tmp_path).get("steps", []))
        fcntl.flock(held, fcntl.LOCK_UN)
    out = "".join(said) + p.stdout.read()
    p.wait(timeout=patience(120))
    overdue.cancel()
    assert not pushed_while_held, f"the ship pushed while a land held the lock:\n{out}"
    assert steps_while_held == [], steps_while_held
    assert p.returncode == 0, out
    assert "waiting for the land lock: held by lane-x @ 0123abcd" in out, out
    assert _remote_main(shipped) == head
    assert _result(tmp_path)["status"] == "ok"


@pytest.mark.load_sensitive
def test_a_land_the_ships_release_runs_does_not_wait_on_the_ship(
    shipped: Path, tmp_path: Path
) -> None:
    """goh's own kit (tools/release_goh.sh) lands its release commit through land.sh: the ship holds
    the lock, so that land must read it as its caller's, not queue behind it forever."""
    wt = tmp_path / "wt-rel"
    git(shipped, "worktree", "add", "-q", "-b", "rel", str(wt))
    (wt / "rel.txt").write_text("rel\n")
    git(wt, "add", "rel.txt")
    git(wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-verify", "-m",
        "release: v1.1.0")  # fmt: skip
    release = (f'(cd {wt} && GOH_LAND_GATE=true bash {LAND}) && git tag "v$GOH_SHIP_VERSION" && '
               'git push -q origin main "v$GOH_SHIP_VERSION"')  # fmt: skip
    p = _popen(shipped, tmp_path, GOH_SHIP_RELEASE=release, GOH_SHIP_TIMEOUT="40")
    out, _ = p.communicate(timeout=patience(120))
    res = _result(tmp_path)
    assert p.returncode == 0, f"{out}\n{res.get('tail')}"
    assert git(shipped, "rev-parse", "main").strip() == git(wt, "rev-parse", "HEAD").strip()
    log = Path(f"{tmp_path / 'ship'}.release.log").read_text()
    assert "land lock held for this land by its caller: goh ship" in log, log
