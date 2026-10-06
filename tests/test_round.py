"""gates/round.sh: one round -- commit the NAMED paths, push the commit, prove the remote has it.

Ported from ZoneWM (2026-10-06). Each refusal is a way a hand-written chain failed there: a commit
after a red gate, a commit of nothing, a push refused for a disk that read as another error, and
`git commit` taking whatever else was already staged (the hole ZoneWM's own version named).
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env

ROUND = REPO_ROOT / "gates" / "round.sh"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          check=True, env=hermetic_env(drop_git=True)).stdout.strip()  # fmt: skip


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    p = tmp_path / "proj"
    p.mkdir()
    fast_init(p, "main")
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("core.hooksPath", ".githooks")):
        _git(p, "config", k, v)
    (p / "a.txt").write_text("a\n")
    _git(p, "add", "-A")
    _git(p, "commit", "-q", "-m", "base")
    _git(p, "remote", "add", "origin", str(remote))
    _git(p, "push", "-q", "-u", "origin", "main")
    (p / ".githooks").mkdir()
    return p


def _round(p: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(ROUND), *args], cwd=p, capture_output=True, text=True,
                          env=hermetic_env(drop_git=True, GOH_DIR=str(REPO_ROOT)))  # fmt: skip


def _hook(p: Path, name: str, body: str) -> None:
    h = p / ".githooks" / name
    h.write_text(f"#!/bin/sh\n{body}\n")
    h.chmod(0o755)


@pytest.mark.parametrize(
    ("args", "why"),
    [
        ((), "no commit message"),
        (("-m", "x"), "no paths"),
        (("-m", "x", "--all"), "is not a path"),
        (("-m", "x", "nope.txt"), "neither on disk nor tracked"),
        (("-m", "x", ".claude/settings.local.json"), "never committed"),
    ],
)
def test_a_round_that_cannot_be_made_is_refused_before_anything_runs(proj, args, why) -> None:
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.local.json").write_text("{}\n")
    r = _round(proj, *args)
    assert r.returncode == 1 and why in r.stderr, r.stderr
    assert _git(proj, "rev-list", "--count", "HEAD") == "1"


def test_only_the_named_paths_are_committed_and_the_remote_has_it(proj) -> None:
    (proj / "a.txt").write_text("a2\n")
    (proj / "other.txt").write_text("staged, not named\n")
    _git(proj, "add", "other.txt")
    r = _round(proj, "-m", "round: a", "a.txt")
    assert r.returncode == 0, r.stdout + r.stderr
    head = _git(proj, "rev-parse", "HEAD")
    assert _git(proj, "show", "--name-only", "--format=", head) == "a.txt"
    assert "other.txt" in _git(proj, "diff", "--cached", "--name-only"), (
        "a staged path was swept in"
    )
    assert _git(proj, "ls-remote", "origin", "refs/heads/main").split()[0] == head


def test_a_red_commit_hook_stops_the_round_with_nothing_pushed(proj) -> None:
    _hook(proj, "pre-commit", "echo red-gate >&2; exit 1")
    (proj / "a.txt").write_text("a2\n")
    r = _round(proj, "-m", "round: a", "a.txt")
    assert r.returncode == 1 and "commit was refused" in r.stderr, r.stderr
    assert _git(proj, "rev-list", "--count", "HEAD") == "1"


def test_a_red_push_hook_leaves_the_commit_local_and_says_so(proj) -> None:
    _hook(proj, "pre-push", "exit 1")
    before = _git(proj, "ls-remote", "origin", "refs/heads/main").split()[0]
    (proj / "a.txt").write_text("a2\n")
    r = _round(proj, "-m", "round: a", "a.txt")
    assert r.returncode == 1 and "the commit is local" in r.stderr, r.stderr
    assert _git(proj, "ls-remote", "origin", "refs/heads/main").split()[0] == before


def test_a_short_disk_stops_the_round_before_the_commit(proj) -> None:
    (proj / ".gatesrc").write_text("GOH_MIN_FREE_GIB=1e9\n")
    (proj / "a.txt").write_text("a2\n")
    r = _round(proj, "-m", "round: a", "a.txt")
    assert r.returncode == 1 and "GiB free" in r.stderr, r.stderr
    assert _git(proj, "rev-list", "--count", "HEAD") == "1"


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not installed")
def test_a_named_python_file_is_formatted_when_the_repo_opts_in(proj) -> None:
    (proj / ".gatesrc").write_text("GOH_PYTHON_FORMATTED=1\n")
    (proj / "m.py").write_text("x  =  1\n")
    r = _round(proj, "-m", "round: m", "m.py")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _git(proj, "show", "HEAD:m.py") == "x = 1"


@pytest.mark.parametrize("how", ["rm", "mv"])
def test_a_staged_deletion_is_a_path_the_round_commits(proj, how) -> None:
    """`git rm` (or the old side of a `git mv`) leaves a path on neither disk nor the index; only
    HEAD has it. The round must still commit the deletion it names (ZoneWM, 2026-10-06)."""
    if how == "rm":
        _git(proj, "rm", "-q", "a.txt")
        named = ("a.txt",)
    else:
        _git(proj, "mv", "a.txt", "b.txt")
        named = ("a.txt", "b.txt")
    r = _round(proj, "-m", f"round: {how}", *named)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "a.txt" not in _git(proj, "ls-tree", "--name-only", "HEAD").split()
    assert _git(proj, "status", "--porcelain") == ""


def test_a_preflight_runs_before_the_commit_and_a_red_one_stops_it(proj) -> None:
    (proj / ".gatesrc").write_text("GOH_ROUND_PREFLIGHT='echo warm-verify; exit 3'\n")
    (proj / "a.txt").write_text("a2\n")
    r = _round(proj, "-m", "round: a", "a.txt")
    assert r.returncode == 1 and "preflight" in r.stderr, r.stdout + r.stderr
    assert "warm-verify" in r.stdout + r.stderr
    assert _git(proj, "rev-list", "--count", "HEAD") == "1"
    (proj / ".gatesrc").write_text("GOH_ROUND_PREFLIGHT='test -f a.txt'\n")
    r = _round(proj, "-m", "round: a", "a.txt")
    assert r.returncode == 0, r.stdout + r.stderr
