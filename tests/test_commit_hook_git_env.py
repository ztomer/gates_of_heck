"""A commit hook's repository variables never reach what the commit gate spawns (contract #12).

media_server, 2026-10-08. git hands pre-commit GIT_DIR and GIT_INDEX_FILE -- from a LINKED
worktree both absolute -- and the stock hook passed them to `tools/gate.sh --staged` and
everything under it. A consumer's test suite that built a scratch repo (init, `add`,
`commit`, `stash`) acted on the REAL repository: `core.bare=true` in the shared config
(the main checkout stopped working), the scratch tree committed onto the worktree's branch, and
three entries pushed onto the shared stash. And every commit from a worktree printed "the gates
... are NOT committed", because `git status` in the gates export answered with the consumer's
index against the export's files.

Each test commits for real -- `git commit` in a linked worktree, so git itself sets the
variables -- through the stock hook. The `commit -a` tests are the other half: unbinding must not
cost the staged scope the index being committed (`gates/_git_env.sh`, GOH_HOOK_INDEX_FILE).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from _fast_git import fast_init
from conftest import EMOJI_SMILE, REPO_ROOT, hermetic_env, native_goh_path
from test_gate_environment import _clean_checkout_of_todays_gates

# The commit hook as it shipped before the variables were dropped: consumers run it until they
# re-install, so the gates-side half of the fix has to hold under it too.
PREVIOUS_STOCK_HOOK = "86a9ee0"

# A consumer's "test suite": a scratch repo built the way test_bump.py built it. Every step is
# `|| true` so each corruption is reached even when an earlier one broke the real repo.
SUITE_GATE = """#!/usr/bin/env bash
set -uo pipefail
t="$(mktemp -d "$SUITE_TMP/suite.XXXXXX")"
git init -q "$t" || true
echo temp > "$t/temp.txt"
git -C "$t" add temp.txt || true
git -C "$t" -c user.email=t@t -c user.name=t commit -q -m "scratch tree" || true
echo more > "$t/temp.txt"
git -C "$t" stash push -q -m "scratch stash" || true
exit 0
"""

STRUCTURAL_GATE = """#!/usr/bin/env bash
set -euo pipefail
exec bash "$GOH_DIR/gates/structural.sh" --staged
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _consumer(
    tmp_path: Path, gate: str, hook: bytes, files: dict | None = None, worktree_config=True
):
    """(main, wt): a consumer repo with the stock hook and `gate` as tools/gate.sh, plus a
    linked worktree `wt` on branch `wt` -- the shape whose hook variables are absolute."""
    main = tmp_path / "main"
    (main / ".githooks").mkdir(parents=True)
    (main / ".githooks" / "pre-commit").write_bytes(hook)
    (main / ".githooks" / "pre-commit").chmod(0o755)
    (main / "tools").mkdir()
    (main / "tools" / "gate.sh").write_text(gate)
    (main / "tools" / "gate.sh").chmod(0o755)
    (main / "README.md").write_text("consumer\n")
    for rel, body in (files or {}).items():
        (main / rel).write_text(body)
    fast_init(main, "main")
    _git(main, "config", "user.email", "t@t")
    _git(main, "config", "user.name", "t")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "--no-verify", "-m", "one")
    _git(main, "config", "core.hooksPath", ".githooks")
    _git(main, "worktree", "add", "-q", str(tmp_path / "wt"))
    if worktree_config:
        _git(main, "config", "extensions.worktreeConfig", "true")
    return main, tmp_path / "wt"


def _commit(wt: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(wt), "commit", "-q", "-m", "ours", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _bare(main: Path) -> str:
    return subprocess.run(
        ["git", "config", "--file", str(main / ".git" / "config"), "core.bare"],
        capture_output=True,
        text=True,
    ).stdout.strip()


def _export_env(tmp_path: Path, gates: Path) -> dict:
    """How a CONSUMER runs the gates: from an export of the checkout's HEAD (C4), not the live
    tree -- the `~/.cache/goh/head/<sha>` the false warning came from. The suite's own markers
    go, or the gate refuses to run anything but the working tree."""
    env = hermetic_env(
        GOH_DIR=str(gates),
        GOH_BIN=str(native_goh_path()),
        GOH_HEAD_CACHE=str(tmp_path / "head-cache"),
    )
    for name in ("GOH_LIVE", "GATES_OF_HECK_SUITE", "PYTEST_CURRENT_TEST"):
        env.pop(name, None)
    return env


@pytest.mark.parametrize("worktree_config", [True, False])
def test_a_consumer_suite_run_by_the_commit_gate_never_touches_the_real_repo(
    tmp_path, worktree_config
):
    """With extensions.worktreeConfig on, the scratch repo's init flips core.bare and the rest of
    the scratch suite then fails on the broken repo; off, the init is harmless and the scratch
    commit and stash land on the real repository. Both shapes, so each corruption is reached."""
    main, wt = _consumer(
        tmp_path,
        SUITE_GATE,
        (REPO_ROOT / "hooks" / "pre-commit").read_bytes(),
        worktree_config=worktree_config,
    )
    (tmp_path / "suite").mkdir()
    (wt / "ours.txt").write_text("ours\n")
    _git(wt, "add", "ours.txt")
    env = hermetic_env(GOH_DIR=str(REPO_ROOT), SUITE_TMP=str(tmp_path / "suite"))

    got = _commit(wt, env)

    text = got.stdout + got.stderr
    assert got.returncode == 0, text
    assert _bare(main) in ("", "false"), f"core.bare flipped in the shared config: {text}"
    assert _git(main, "log", "--format=%s", "wt").splitlines() == ["ours", "one"], text
    assert _git(main, "show", "--name-only", "--format=", "wt") == "ours.txt", text
    assert _git(main, "stash", "list") == "", "the scratch repo's stash landed on the real one"
    assert _git(main, "log", "--format=%s", "main").splitlines() == ["one"]


@pytest.mark.parametrize("hook", ["current", "previous"])
def test_a_worktree_commit_does_not_report_uncommitted_gates(tmp_path, hook):
    """No "NOT committed" from a clean gates checkout. Under the PREVIOUS stock hook the
    variables still arrive (that hook is a copy, current only after a re-install), so the gates'
    own git on their checkout has to drop them itself. The consumer's Cargo.toml is bait for the
    sibling read: `git show HEAD:Cargo.toml` in the export with the hook's GIT_DIR read THIS one,
    and the version check called the binary behind."""
    gates = _clean_checkout_of_todays_gates(tmp_path)
    body = (
        (REPO_ROOT / "hooks" / "pre-commit").read_bytes()
        if hook == "current"
        else _git(REPO_ROOT, "show", f"{PREVIOUS_STOCK_HOOK}:hooks/pre-commit").encode() + b"\n"
    )
    main, wt = _consumer(
        tmp_path,
        STRUCTURAL_GATE,
        body,
        {"Cargo.toml": '[package]\nname = "consumer"\nversion = "0.0.1"\n'},
    )
    (wt / "ours.txt").write_text("ours\n")
    _git(wt, "add", "ours.txt")

    got = _commit(wt, _export_env(tmp_path, gates))

    text = got.stdout + got.stderr
    assert got.returncode == 0, text
    assert "NOT committed" not in text, text
    assert "BEHIND" not in text, text
    assert _git(main, "log", "--format=%s", "wt").splitlines() == ["ours", "one"], text


@pytest.mark.parametrize("how", ["all", "paths"])
def test_the_staged_scope_still_judges_the_index_being_committed(tmp_path, how):
    """`commit -a` and `commit <paths>` commit a TEMPORARY index (index.lock, next-index-*), named
    only by GIT_INDEX_FILE. Unbinding it without carrying it would judge the repository's own
    index -- here, without the emoji -- and pass a commit that records one."""
    gates = _clean_checkout_of_todays_gates(tmp_path)
    main, wt = _consumer(
        tmp_path,
        STRUCTURAL_GATE,
        (REPO_ROOT / "hooks" / "pre-commit").read_bytes(),
        {"notes.md": "clean\n"},
    )
    (wt / "notes.md").write_text(f"dirty {EMOJI_SMILE}\n")  # modified, NOT staged
    extra = ("-a",) if how == "all" else ("notes.md",)

    got = _commit(wt, _export_env(tmp_path, gates), *extra)

    text = got.stdout + got.stderr
    assert got.returncode != 0, f"the commit's index went unjudged:\n{text}"
    assert "notes.md" in text, text
    assert _git(main, "log", "--format=%s", "wt").splitlines() == ["one"], text


def test_a_plain_commit_does_not_judge_an_unstaged_edit(tmp_path):
    """The converse: with nothing but the repository's own index, an unstaged violation is not
    the commit's -- the hook carried no index, and the staged scope must not invent one."""
    gates = _clean_checkout_of_todays_gates(tmp_path)
    main, wt = _consumer(
        tmp_path,
        STRUCTURAL_GATE,
        (REPO_ROOT / "hooks" / "pre-commit").read_bytes(),
        {"notes.md": "clean\n"},
    )
    (wt / "notes.md").write_text(f"dirty {EMOJI_SMILE}\n")
    (wt / "ours.txt").write_text("ours\n")
    _git(wt, "add", "ours.txt")

    got = _commit(wt, _export_env(tmp_path, gates))

    assert got.returncode == 0, got.stdout + got.stderr
    assert _git(main, "log", "--format=%s", "wt").splitlines() == ["ours", "one"]


def test_the_carried_index_never_binds_another_repository(tmp_path):
    """GOH_HOOK_INDEX_FILE outlives the hook's own git: a consumer test that runs a gate on a
    FIXTURE inherits it. It binds only the repository it was taken from."""
    fixture = tmp_path / "fixture"
    fast_init(fixture)
    probe = (
        f'. "{REPO_ROOT}/gates/_git_env.sh"; goh_bind_hook_index; '
        'printf "%s" "${GIT_INDEX_FILE:-unbound}"'
    )
    env = hermetic_env(
        GOH_HOOK_INDEX_FILE=str(tmp_path / "elsewhere" / "index.lock"),
        GOH_HOOK_GIT_DIR=str(tmp_path / "elsewhere" / ".git"),
    )
    got = subprocess.run(
        ["bash", "-c", probe], cwd=fixture, env=env, capture_output=True, text=True
    )
    assert got.returncode == 0, got.stderr
    assert got.stdout == "unbound", got.stdout


def test_a_repository_reachable_only_through_git_dir_is_refused_by_name(tmp_path):
    """A bare repository with a separate work tree (`git --git-dir=dot.git --work-tree=home`):
    git cannot find it from the hook's cwd. Kept, the variables reach every child; dropped, the
    gate has no repository. The hook refuses and says which, instead of doing either silently."""
    dot, home, hooks = tmp_path / "dot.git", tmp_path / "home", tmp_path / "hooks"
    home.mkdir()
    hooks.mkdir()
    shutil.copy2(REPO_ROOT / "hooks" / "pre-commit", hooks / "pre-commit")
    _git(tmp_path, "init", "-q", "--bare", str(dot))
    bound = ["git", f"--git-dir={dot}", f"--work-tree={home}", "-c", f"core.hooksPath={hooks}"]
    (home / "f.txt").write_text("f\n")
    subprocess.run([*bound, "add", "f.txt"], check=True, capture_output=True)
    got = subprocess.run(
        [*bound, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "x"],
        cwd=home,
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert got.returncode != 0, got.stdout + got.stderr  # git reports any hook failure as 1
    assert "only through" in got.stderr and "--no-verify" in got.stderr, got.stderr
