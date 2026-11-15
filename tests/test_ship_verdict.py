"""`goh ship verdict --sha SHA`: did the gate pass on SHA under my ship? (GOH.VERDICTAPI)

A release kit started by `goh ship` re-ran the full gate over the commit ship's push step had just
gated (koffee_big: three ~19 min gates per ship). Its skip read goh's push LOG for the text
"pre-push: gating <ref> @ <sha>" and "gates: all green", so a reworded line silently turned the
skip off. The verdict is now RECORDED: the push gate appends `green <sha>` to the file
GOH_GATE_VERDICTS names, after every gated ref passed and none moved; the ship worker names that
file for its whole run and keeps its path in the result; the query answers only for a running
ship whose worker is an ancestor of the caller.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from test_push_gate import PUSH_GATE, _git, _repo
from test_ship import _result, _ship, make_shipped

SHIP = REPO_ROOT / "tools" / "ship.py"


def _push(repo: Path, tmp_path: Path, verdicts: Path) -> subprocess.CompletedProcess:
    sha = _git(repo, "rev-parse", "HEAD")
    env = hermetic_env(
        GATE_REPORT=str(tmp_path / "report.txt"),
        GOH_DIR=str(REPO_ROOT),
        GOH_PUSH_LOGS=str(tmp_path / "push-logs"),
        GOH_PUSH_WORKTREES=str(tmp_path / "exports"),
        GOH_GATE_VERDICTS=str(verdicts),
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
    verdicts = tmp_path / "verdicts"
    r = _push(repo, tmp_path, verdicts)
    assert r.returncode == 0, r.stdout + r.stderr
    sha = _git(repo, "rev-parse", "HEAD")
    assert verdicts.read_text().splitlines() == [f"green {sha}"]


def test_a_red_push_gate_records_nothing(tmp_path):
    repo = _repo(tmp_path)
    (repo / "fail.flag").write_text("")
    _git(repo, "add", "fail.flag")
    _git(repo, "commit", "-q", "-m", "red")
    verdicts = tmp_path / "verdicts"
    r = _push(repo, tmp_path, verdicts)
    assert r.returncode != 0
    assert not verdicts.exists() or not verdicts.read_text().strip()


def _record(tmp_path: Path, repo: Path, sha: str, *, pid: int, status: str = "running") -> Path:
    verdicts = tmp_path / "rec.verdicts"
    verdicts.write_text(f"green {sha}\n")
    path = tmp_path / "ships" / repo.name / "20261114T000000Z-x.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"repo": str(repo), "status": status, "pid": pid, "verdicts": str(verdicts)}
    path.write_text(json.dumps(rec))
    return path


def _verdict(tmp_path: Path, repo: Path, sha: str) -> subprocess.CompletedProcess:
    env = hermetic_env(drop_git=True, GOH_SHIP_DIR=str(tmp_path / "ships"))
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
    repo = tmp_path / "r"
    repo.mkdir()
    sha = "a" * 40
    path = _record(tmp_path, repo, sha, pid=os.getpid())  # this test is the "worker"
    r = _verdict(tmp_path, repo, sha)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == str(path)


def test_the_query_refuses_what_it_cannot_prove(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    sha = "a" * 40
    _record(tmp_path, repo, sha, pid=os.getpid())
    assert _verdict(tmp_path, repo, "b" * 40).returncode == 1  # another sha: never gated
    _record(tmp_path, repo, sha, pid=os.getpid(), status="ok")
    assert _verdict(tmp_path, repo, sha).returncode == 1  # a finished ship is history
    dead = subprocess.Popen(["true"], stdin=subprocess.DEVNULL)
    dead.wait()
    _record(tmp_path, repo, sha, pid=dead.pid)
    assert _verdict(tmp_path, repo, sha).returncode == 1  # no live worker above the caller


def test_the_ship_worker_names_the_verdict_file_for_its_whole_run(tmp_path):
    shipped = make_shipped(_init(tmp_path), tmp_path)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    # A stand-in push gate: green, recorded where the worker says.
    hook = 'while read -r _ sha _ _; do echo "green $sha" >>"$GOH_GATE_VERDICTS"; done\n'
    (hooks / "pre-push").write_text("#!/bin/sh\n" + hook)
    (hooks / "pre-push").chmod(0o755)
    _git(shipped, "config", "core.hooksPath", str(hooks))
    r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE")
    assert r.returncode == 0, r.stdout + r.stderr
    res = _result(tmp_path)
    assert f"green {res['handoff_sha']}" in Path(res["verdicts"]).read_text(), res


def _init(tmp_path: Path) -> Path:
    repo = tmp_path / "shipped"
    fast_init(repo, "main")
    (repo / "a.txt").write_text("one\n")
    _git(repo, "add", "a.txt")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "one")
    return repo
