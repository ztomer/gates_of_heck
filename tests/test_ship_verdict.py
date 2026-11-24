"""`goh ship verdict --sha SHA`: did the gate pass on SHA's tree, under my ship? (GOH.VERDICTAPI)

A release kit started by `goh ship` re-ran the full gate over the commit ship's push step had just
gated (koffee_big: three ~19 min gates per ship). Its skip read goh's push LOG for the text
"pre-push: gating <ref> @ <sha>" and "gates: all green", so a reworded line silently turned the
skip off. The verdict is RECORDED: the push gate records each commit it gated green in the one
verdict store (lib/reuse.py, GOH.ONEVERDICT), keyed by the commit's tree and the gate identity,
on every push; the query answers only for a running ship whose worker is an ancestor of the
caller. The per-ship file it read before held only what ship's OWN push gated, so a commit the
land queue had pushed first was never green to the ship (koffee_big ship
20261115T121828Z-952ef800: "nothing to gate", then a full gate in the release kit, 3137.5 s).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import _goh_db_fixture as gdb
import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from test_push_gate import PUSH_GATE, _git, _repo
from test_reuse import REUSE, recorded_hows

SHIP = REPO_ROOT / "tools" / "ship.py"


def _push(repo: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    sha = _git(repo, "rev-parse", "HEAD")
    env = hermetic_env(
        GATE_REPORT=str(tmp_path / "report.txt"),
        GOH_DIR=str(REPO_ROOT),
        GOH_PUSH_LOGS=str(tmp_path / "push-logs"),
        GOH_PUSH_WORKTREES=str(tmp_path / "exports"),
        GOH_REUSE_DIR=str(tmp_path / "reuse"),
    )
    return subprocess.run(
        ["bash", str(PUSH_GATE), "origin"],
        cwd=repo,
        env=env,
        text=True,
        input=f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n",
        capture_output=True,
        timeout=120,
        check=False,
    )


def test_a_green_push_gate_records_the_sha_it_gated(tmp_path):
    repo = _repo(tmp_path)
    r = _push(repo, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    sha = _git(repo, "rev-parse", "HEAD")
    assert recorded_hows(tmp_path / "reuse", sha) == ["full"], r.stdout + r.stderr


def test_a_red_push_gate_records_nothing(tmp_path):
    repo = _repo(tmp_path)
    (repo / "fail.flag").write_text("")
    _git(repo, "add", "fail.flag")
    _git(repo, "commit", "-q", "-m", "red")
    r = _push(repo, tmp_path)
    assert r.returncode != 0
    assert recorded_hows(tmp_path / "reuse", _git(repo, "rev-parse", "HEAD")) == []


def _record(tmp_path: Path, repo: Path, *, pid: int, status: str = "running") -> Path:
    path = tmp_path / "ships" / repo.name / "20261114T000000Z-x.json"
    return gdb.ship(path, {"repo": str(repo), "status": status, "pid": pid})


def _gated(tmp_path: Path, repo: Path, sha: str) -> None:
    """The store's record of a green full gate on sha (what the push gate writes)."""
    r = subprocess.run([sys.executable, str(REUSE), "record-gate", "--repo", str(repo), sha],
                       env=hermetic_env(drop_git=True, GOH_REUSE_DIR=str(tmp_path / "reuse")),
                       capture_output=True, text=True, check=False,
                       stdin=subprocess.DEVNULL)  # fmt: skip
    assert r.returncode == 0, r.stderr


def _verdict(tmp_path: Path, repo: Path, sha: str) -> subprocess.CompletedProcess:
    env = hermetic_env(drop_git=True, GOH_SHIP_DIR=str(tmp_path / "ships"),
                       GOH_REUSE_DIR=str(tmp_path / "reuse"))  # fmt: skip
    return subprocess.run(
        [sys.executable, str(SHIP), "verdict", "--sha", sha, "--repo", str(repo)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


def test_the_query_answers_for_a_running_ship_above_the_caller(tmp_path):
    repo = _init(tmp_path)
    sha = _git(repo, "rev-parse", "HEAD")
    _gated(tmp_path, repo, sha)
    path = _record(tmp_path, repo, pid=os.getpid())  # this test is the "worker"
    r = _verdict(tmp_path, repo, sha)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == str(path)


def test_a_commit_gated_before_the_ship_is_green_to_it(tmp_path):
    """The koffee_big case: the land queue pushed the handoff, its push gate recorded it, and the
    ship's own push then had nothing to gate. The verdict is the store's, not the ship's."""
    repo = _repo(tmp_path)
    assert _push(repo, tmp_path).returncode == 0  # no ship: the land queue's push
    _record(tmp_path, repo, pid=os.getpid())  # the ship starts after
    r = _verdict(tmp_path, repo, _git(repo, "rev-parse", "HEAD"))
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.load_sensitive
def test_the_query_refuses_what_it_cannot_prove(tmp_path):
    repo = _init(tmp_path)
    sha = _git(repo, "rev-parse", "HEAD")
    _gated(tmp_path, repo, sha)
    _record(tmp_path, repo, pid=os.getpid())
    (repo / "a.txt").write_text("two\n")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "two")
    other = _verdict(tmp_path, repo, _git(repo, "rev-parse", "HEAD"))
    assert other.returncode == 1 and "no gate verdict" in other.stderr, other.stderr  # never gated
    _record(tmp_path, repo, pid=os.getpid(), status="ok")
    assert _verdict(tmp_path, repo, sha).returncode == 1  # a finished ship is history
    dead = subprocess.Popen(["true"], stdin=subprocess.DEVNULL)
    dead.wait()
    _record(tmp_path, repo, pid=dead.pid)
    assert _verdict(tmp_path, repo, sha).returncode == 1  # no live worker above the caller


def _init(tmp_path: Path) -> Path:
    repo = tmp_path / "shipped"
    fast_init(repo, "main")
    (repo / "a.txt").write_text("one\n")
    _git(repo, "add", "a.txt")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "one")
    return repo
