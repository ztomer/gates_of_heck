"""A ship always finds or makes its fresh instrumented full run (GOH.SHIPSTARVE).

Owner's term (GOH.COVCARRY): a fresh full instrumented run on main's tip before every ship. Only the
idle lander made it, and it yields to every land, so with lands arriving a ship starved; and the
ship waited for the land lock, then released whatever main had become, which no run had judged
(2026-11-18: 8150234a waited 20 min, took 2a3f0dc9, refused "drove the plain binary"). The ship's
first step now makes the run itself, holding the land lock (lib/ship_full_run.py), and its record
keeps the lock wait apart from its own run, which is what `goh budget` judges (lib/budget.py).
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import test_ship
from conftest import REPO_ROOT, commit_all, git, write
from test_ship import SHIP, _env
from timing_bounds import GiveUp, patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import budget
import gate_identity
import reuse

shipped = test_ship.shipped  # the fixture, by assignment

# The tip's stand-in coverage gate (what idle_suite.INSTRUMENTED runs): it logs the commit it judged
# and records an instrumented full run on its tree, as the real suite's record does.
STAND_IN = """\
git rev-parse HEAD >> "$STAND_IN_LOG"
exec {py} -c '
import subprocess, sys
from pathlib import Path
sys.path.insert(0, "{lib}")
import gate_identity, reuse
tree = subprocess.run(["git", "rev-parse", "HEAD^{{tree}}"], capture_output=True,
                      text=True).stdout.strip()
reuse.record_suite(Path("."), tree, gate_identity.suite_tools(), {{"t.py::a": True}}, set(), True,
                   "stand-in", instrumented=True)
'
"""
# The release kit's own check (tools/release_goh.sh), then the tag.
KIT = (f'{sys.executable} {REPO_ROOT / "lib" / "reuse.py"} full-suite --repo . '
       '--sha "$(git rev-parse HEAD)" && git tag "v$GOH_SHIP_VERSION" && '
       'git push -q origin "v$GOH_SHIP_VERSION"')  # fmt: skip


def _declare(repo: Path) -> None:
    write(repo, ".gatesrc", "GOH_SHIP_FULL_SUITE=on\n")
    write(repo, "gates/coverage_gate.sh", STAND_IN.format(py=sys.executable, lib=REPO_ROOT / "lib"))
    commit_all(repo, "chore: a repo whose ship stands on a full run")


def _env_for(tmp_path: Path) -> dict[str, str]:
    return _env(tmp_path, GOH_SHIP_RELEASE=KIT, GOH_REUSE_DIR=str(tmp_path / "store"),
                STAND_IN_LOG=str(tmp_path / "judged"), GOH_LIVE="1")  # fmt: skip


def _ship(repo: Path, tmp_path: Path) -> subprocess.Popen[str]:
    argv = [sys.executable, str(SHIP), "--repo", str(repo), "--out", str(tmp_path / "ship.json"),
            "--wait"]  # fmt: skip
    return subprocess.Popen(argv, env=_env_for(tmp_path), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            stdin=subprocess.DEVNULL)  # fmt: skip


def _judged(tmp_path: Path) -> list[str]:
    log = tmp_path / "judged"
    return log.read_text().split() if log.exists() else []


def _record_run(repo: Path, store: Path) -> None:
    """The idle lander's instrumented run on REPO's HEAD, as it lands in the store."""
    os.environ["GOH_REUSE_DIR"] = str(store)
    try:
        tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
        reuse.record_suite(repo, tree, gate_identity.suite_tools(), {"t.py::a": True}, set(), True,
                           "idle", instrumented=True)  # fmt: skip
    finally:
        os.environ.pop("GOH_REUSE_DIR", None)


@pytest.mark.load_sensitive
def test_lands_keep_arriving_and_the_ship_still_releases_a_tip_with_its_run(
    shipped: Path, tmp_path: Path
) -> None:
    _declare(shipped)
    _record_run(shipped, tmp_path / "store")  # the idle run on the tip the ship starts on
    lock = shipped / ".git" / "goh-land.lock"
    with lock.open("a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX)  # a land holds the queue
        held.write(f"lane-x @ 0123abcd (pid {os.getpid()}, since 00:00:00)\n")
        held.flush()
        p = _ship(shipped, tmp_path)
        give_up = GiveUp(30)
        while not give_up.passed() and p.poll() is None:
            path = tmp_path / "ship.json"
            if path.exists() and json.loads(path.read_text()).get("pid"):
                break  # the worker is up, waiting on the lock
            time.sleep(0.1)
        for n in range(3):  # lands keep arriving: main moves past the tip that run judged
            write(shipped, f"land{n}.txt", f"{n}\n")
            commit_all(shipped, f"fix: land {n}")
        time.sleep(0.3)
        fcntl.flock(held, fcntl.LOCK_UN)
    out, _ = p.communicate(timeout=patience(120))
    res = json.loads((tmp_path / "ship.json").read_text())
    tip = git(shipped, "rev-parse", "HEAD").strip()
    assert p.returncode == 0, f"{out}\n{res.get('tail')}"
    assert git(shipped, "rev-parse", "v1.1.0^{commit}").strip() == tip  # the tip released
    assert _judged(tmp_path) == [tip], _judged(tmp_path)  # judged by the ship's own run
    assert (res["steps"][0]["name"], res["steps"][0]["status"]) == ("suite", "ok"), res["steps"]
    assert res["land_wait_s"] >= 0.3, res
    assert 0 <= res["own_s"] <= res["land_wait_s"] + res["own_s"], res
    assert res["sha"] == tip


@pytest.mark.load_sensitive
def test_a_tip_that_has_its_run_is_not_run_again(shipped: Path, tmp_path: Path) -> None:
    _declare(shipped)
    _record_run(shipped, tmp_path / "store")
    p = _ship(shipped, tmp_path)
    out, _ = p.communicate(timeout=patience(120))
    res = json.loads((tmp_path / "ship.json").read_text())
    assert p.returncode == 0, f"{out}\n{res.get('tail')}"
    suite = res["steps"][0]
    assert (suite["name"], suite["status"]) == ("suite", "skipped"), res["steps"]
    assert "already has its instrumented full run (by idle)" in suite["why"], suite
    assert _judged(tmp_path) == []


def test_the_budget_judges_the_ships_own_run_not_its_wait_for_the_queue() -> None:
    res = {
        "schema": 1, "name": "gates_of_heck", "sha": "b" * 40, "status": "ok", "version": "0.1.0",
        "result": "/x/gates_of_heck/20261118T054204Z-8150234a.json", "load1": 4.0,
        "started": "2026-11-18T05:42:04+00:00", "finished": "2026-11-18T06:14:12+00:00",
        "land_wait_s": 1200.0, "own_s": 728.0,
        "steps": [{"name": "suite", "seconds": 600.0, "status": "ok"},
                  {"name": "release", "seconds": 120.0, "status": "ok"}],
    }  # fmt: skip
    ship = budget.ship_rows(res)[0]
    assert (ship["wall_s"], ship["land_wait_s"]) == (728.0, 1200.0), ship
    assert ship["top_steps"][0] == ["suite", 600.0]
    old = {k: v for k, v in res.items() if k not in ("land_wait_s", "own_s")}
    assert budget.ship_rows(old)[0]["wall_s"] == 1928.0  # a result from before: started->finished
