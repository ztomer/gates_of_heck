"""proven.sh / _proven.sh — a gate step already proven on this exact tree is not re-run.

monitor, 2026-09-23: one commit ran the full suite under llvm-cov in pre-commit, then again in
pre-push over the same tree, then in CI. The cache skips the second run -- and these tests pin
the conditions under which it may: a clean tree (working tree == index), the same step string,
the same gates checkout, a green result, a tree that did not move while the step ran, and a
record younger than the TTL. Every one of those has a test that watches the skip NOT happen.

Each test runs a private COPY of the gates (a throwaway git repo), never this checkout: the key
includes the gates' own diff, and this checkout is being edited while the suite runs.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from conftest import REPO_ROOT

GIT_VARS = ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY",
            "GIT_COMMON_DIR", "GIT_PREFIX")


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if k not in GIT_VARS}
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                           *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


@pytest.fixture
def goh(tmp_path: Path) -> Path:
    """A committed copy of the gates this suite tests."""
    d = tmp_path / "goh"
    (d / "gates").mkdir(parents=True)
    for name in ("_proven.sh", "_hash.sh", "proven.sh", "local_ci.sh", "push_gate.sh"):
        shutil.copy2(REPO_ROOT / "gates" / name, d / "gates" / name)
    shutil.copytree(REPO_ROOT / "tui", d / "tui", ignore=shutil.ignore_patterns("__pycache__"))
    _git(d, "init", "-q", "-b", "main")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "gates")
    return d


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "src.txt").write_text("one\n")
    (r / ".gitignore").write_text("build/\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "one")
    return r


@pytest.fixture
def env(tmp_path: Path, goh: Path) -> dict:
    e = {k: v for k, v in os.environ.items()
         if k not in GIT_VARS and not k.startswith("GOH_PROVEN")}
    e["GOH_DIR"] = str(goh)
    e["COUNT"] = str(tmp_path / "count")   # each real run of a step appends a line here
    e["NO_COLOR"] = "1"
    return e


STEP = 'echo ran >> "$COUNT"'


def proven(goh: Path, repo: Path, env: dict, step: str = STEP, *opts: str):
    return subprocess.run(["bash", str(goh / "gates" / "proven.sh"), *opts, "--", step],
                          cwd=repo, env=env, capture_output=True, text=True)


def runs(env: dict) -> int:
    p = Path(env["COUNT"])
    return len(p.read_text().splitlines()) if p.exists() else 0


def records(repo: Path) -> list[Path]:
    d = repo / ".git" / "goh-proven"
    return sorted(p for p in d.iterdir() if not p.name.startswith(".")) if d.exists() else []


# ── the cache does its job ──


def test_records_on_a_clean_tree_and_skips_the_second_run(goh, repo, env):
    first = proven(goh, repo, env, STEP, "--label", "pre-commit")
    assert first.returncode == 0, first.stdout + first.stderr
    assert runs(env) == 1 and len(records(repo)) == 1
    second = proven(goh, repo, env)
    assert second.returncode == 0, second.stdout + second.stderr
    assert runs(env) == 1, "a proven step ran again"
    assert "proven on this tree" in second.stdout and "by pre-commit" in second.stdout


def test_a_staged_only_change_still_has_a_key(goh, repo, env):
    # The pre-commit case: the working tree equals the index, which differs from HEAD.
    (repo / "src.txt").write_text("two\n")
    _git(repo, "add", "src.txt")
    proven(goh, repo, env)
    proven(goh, repo, env)
    assert runs(env) == 1
    rec = records(repo)[0].read_text()
    assert f"tree={_git(repo, 'write-tree')}" in rec


def test_the_record_names_the_committed_tree(goh, repo, env):
    proven(goh, repo, env)
    rec = records(repo)[0].read_text()
    assert f"tree={_git(repo, 'rev-parse', 'HEAD^{tree}')}" in rec
    assert f"step={STEP}" in rec


# ── ... and only then ──


def test_a_different_step_string_is_not_skipped(goh, repo, env):
    proven(goh, repo, env, STEP)
    proven(goh, repo, env, STEP + " # other")
    assert runs(env) == 2


@pytest.mark.parametrize("dirt", ["unstaged", "untracked"])
def test_a_dirty_tree_neither_records_nor_skips(goh, repo, env, dirt):
    if dirt == "unstaged":
        (repo / "src.txt").write_text("edited, not staged\n")
    else:
        (repo / "new.txt").write_text("untracked, not ignored\n")
    for _ in range(2):
        r = proven(goh, repo, env)
        assert r.returncode == 0
    assert runs(env) == 2 and records(repo) == []


def test_an_ignored_file_is_not_dirt(goh, repo, env):
    (repo / "build").mkdir()
    (repo / "build" / "out.o").write_text("artifact\n")
    proven(goh, repo, env)
    proven(goh, repo, env)
    assert runs(env) == 1


def test_a_failing_step_is_not_recorded(goh, repo, env):
    step = 'echo ran >> "$COUNT"; exit 3'
    assert proven(goh, repo, env, step).returncode == 3
    assert proven(goh, repo, env, step).returncode == 3
    assert runs(env) == 2 and records(repo) == []


def test_a_tree_that_moved_during_the_step_is_not_recorded(goh, repo, env):
    # Staged, so the tree is still CLEAN afterwards -- only the recomputed key can see the move.
    step = 'echo ran >> "$COUNT"; echo moved > src.txt; git add src.txt'
    r = proven(goh, repo, env, step)
    assert r.returncode == 0
    assert "moved while the step ran" in r.stdout
    assert records(repo) == []


def test_a_record_expires_after_the_ttl(goh, repo, env):
    proven(goh, repo, env)
    later = dict(env, GOH_PROVEN_NOW=str(int(time.time()) + 86400 + 5))
    proven(goh, repo, later)
    assert runs(env) == 2, "a record older than the 24 h default was trusted"
    short = dict(env, GOH_PROVEN_TTL_S="60", GOH_PROVEN_NOW=str(int(time.time()) + 86400 + 70))
    proven(goh, repo, short)
    assert runs(env) == 3, "GOH_PROVEN_TTL_S was not honoured"


def test_expired_records_are_pruned(goh, repo, env):
    proven(goh, repo, env, STEP)
    later = dict(env, GOH_PROVEN_NOW=str(int(time.time()) + 90000))
    proven(goh, repo, later, STEP + " # b")
    assert len(records(repo)) == 1, "the expired record was left behind"


def test_goh_proven_0_disables_the_cache(goh, repo, env):
    off = dict(env, GOH_PROVEN="0")
    proven(goh, repo, off)
    proven(goh, repo, off)
    assert runs(env) == 2 and records(repo) == []


@pytest.mark.parametrize("change", ["edit", "untracked", "commit"])
def test_a_change_to_the_gates_invalidates_what_they_proved(goh, repo, env, change):
    proven(goh, repo, env)
    if change == "edit":
        with open(goh / "gates" / "local_ci.sh", "a") as f:
            f.write("# work in progress\n")
    elif change == "untracked":
        (goh / "gates" / "new_gate.sh").write_text("#!/bin/sh\n")
    else:
        (goh / "NOTE").write_text("x\n")
        _git(goh, "add", "NOTE")
        _git(goh, "commit", "-q", "-m", "gates moved")
    proven(goh, repo, env)
    assert runs(env) == 2


def test_an_env_var_the_repo_names_is_part_of_the_key(goh, repo, env):
    (repo / ".gatesrc").write_text("GOH_PROVEN_ENV='MY_MODE'\n")
    _git(repo, "add", ".gatesrc")
    _git(repo, "commit", "-q", "-m", "rc")
    proven(goh, repo, dict(env, MY_MODE="a"))
    proven(goh, repo, dict(env, MY_MODE="b"))
    proven(goh, repo, dict(env, MY_MODE="b"))
    assert runs(env) == 2


def test_a_bad_ttl_is_a_usage_error_naming_the_value(goh, repo, env):
    r = proven(goh, repo, dict(env, GOH_PROVEN_TTL_S="1d"))
    assert r.returncode == 2 and "'1d'" in r.stderr
    assert runs(env) == 0


def test_outside_a_git_repo_the_step_just_runs(goh, tmp_path, env):
    plain = tmp_path / "plain"
    plain.mkdir()
    for _ in range(2):
        assert proven(goh, plain, env).returncode == 0
    assert runs(env) == 2


# ── where the records are made and read: the hook, local_ci, the push gate ──

HOOK = """#!/usr/bin/env bash
set -euo pipefail
"$GOH_DIR/gates/proven.sh" --label pre-commit -- 'echo ran >> "$COUNT"'
"""
GATE = """#!/usr/bin/env bash
set -euo pipefail
exec "$GOH_DIR/gates/local_ci.sh" .
"""


@pytest.fixture
def hooked(repo: Path, env: dict) -> Path:
    """The repo with a pre-commit hook that proves STEP, and GOH_CI_STEPS naming the same step."""
    (repo / ".githooks").mkdir()
    (repo / ".githooks" / "pre-commit").write_text(HOOK)
    (repo / ".githooks" / "pre-commit").chmod(0o755)
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text(GATE)
    (repo / "tools" / "gate.sh").chmod(0o755)
    (repo / ".gatesrc").write_text(f"GOH_CI_STEPS='{STEP}:true'\n")
    _git(repo, "config", "core.hooksPath", ".githooks")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "wire", "--no-verify")
    return repo


def commit(repo: Path, env: dict, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", *args],
                   cwd=repo, env=env, check=True, capture_output=True, text=True)


def local_ci(goh: Path, repo: Path, env: dict):
    return subprocess.run(["bash", str(goh / "gates" / "local_ci.sh"), str(repo)],
                          cwd=repo, env=env, capture_output=True, text=True)


def test_local_ci_records_and_then_skips(goh, hooked, env):
    first = local_ci(goh, hooked, env)
    assert first.returncode == 0, first.stdout + first.stderr
    second = local_ci(goh, hooked, env)
    assert second.returncode == 0, second.stdout + second.stderr
    assert runs(env) == 1
    assert "by local_ci" in second.stdout
    assert "2 proven earlier on this tree" in second.stdout


@pytest.mark.parametrize("how", ["staged", "all"])
def test_a_step_the_pre_commit_hook_proved_is_skipped_at_push(goh, hooked, env, how):
    # `git commit` hands the hook GIT_INDEX_FILE: the index being committed. With -a it is a
    # temporary index (index.lock) -- the tree must still be the one that lands.
    (hooked / "src.txt").write_text("two\n")
    if how == "staged":
        _git(hooked, "add", "src.txt")
        commit(hooked, env, "-m", "two")
    else:
        commit(hooked, env, "-a", "-m", "two")
    assert runs(env) == 1, "the hook did not run the step"
    r = local_ci(goh, hooked, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert runs(env) == 1, "the push-time run re-ran a step the hook proved on this tree"
    assert "by pre-commit" in r.stdout


def test_a_partially_staged_commit_records_nothing(goh, hooked, env):
    (hooked / "src.txt").write_text("two\n")
    _git(hooked, "add", "src.txt")
    (hooked / "other.txt").write_text("not part of the commit\n")
    commit(hooked, env, "-m", "two")
    assert records(hooked) == []


def test_the_push_gate_worktree_sees_what_the_checkout_proved(goh, hooked, env, tmp_path):
    (hooked / "src.txt").write_text("two\n")
    commit(hooked, env, "-a", "-m", "two")
    sha = _git(hooked, "rev-parse", "HEAD")
    penv = dict(env, GOH_PUSH_WORKTREES=str(tmp_path / "push"),
                GOH_PUSH_LOGS=str(tmp_path / "push-logs"))
    r = subprocess.run(["bash", str(goh / "gates" / "push_gate.sh")], cwd=hooked, env=penv,
                       input=f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n",
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert runs(env) == 1, "the push gate's clean worktree re-ran a proven step"
    assert "by pre-commit" in r.stdout
