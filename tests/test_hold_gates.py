"""A held repo's git hooks refuse commit, merge, `git am` and push, naming why and the lift.

The hooks every repo installs delegate to the gates at runtime (gates/commit_gate.sh, commit_msg.sh
-- the hook a merge commit runs --, sequencer_gate.sh for pre-applypatch, push_gate.sh), so the hold
(lib/repo_hold.sh) reaches every installed repo with no re-install. The same repo, unheld, commits.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from test_commit_gate_structural import CALLS, _git

REASON = "frozen for the v9 release"
HOOKS = ("pre-commit", "commit-msg", "pre-applypatch", "post-rewrite", "pre-push")


def _consumer(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".githooks").mkdir(parents=True)
    for name in HOOKS:
        hook = repo / ".githooks" / name
        hook.write_bytes((REPO_ROOT / "hooks" / name).read_bytes())
        hook.chmod(0o755)
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text(CALLS)
    (repo / "README.md").write_text("consumer\n")
    fast_init(repo, "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "one")
    _git(repo, "config", "core.hooksPath", ".githooks")
    return repo


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip


def _hold(repo: Path) -> None:
    _git(repo, "config", "--local", "goh.hold", f"2026-10-26: {REASON} -- lift: lift-cmd")


def _refused(r: subprocess.CompletedProcess[str], hook: str) -> None:
    text = r.stdout + r.stderr
    assert r.returncode != 0, text
    assert f"{hook}: " in text, text  # the hook that refused it, named
    assert REASON in text and "lift-cmd" in text and "on hold" in text, text


def test_a_commit_is_refused_in_a_held_repo_and_runs_in_an_unheld_one(tmp_path: Path) -> None:
    repo = _consumer(tmp_path)
    (repo / "a.md").write_text("a\n")
    _git(repo, "add", "a.md")
    _hold(repo)
    head = _git(repo, "rev-parse", "HEAD")
    _refused(_run(repo, "commit", "-q", "-m", "two"), "pre-commit")
    assert _git(repo, "rev-parse", "HEAD") == head
    _git(repo, "config", "--local", "--unset", "goh.hold")
    r = _run(repo, "commit", "-q", "-m", "two")
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_merge_commit_is_refused(tmp_path: Path) -> None:
    repo = _consumer(tmp_path)
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "s.md").write_text("s\n")
    _git(repo, "add", "s.md")
    _git(repo, "commit", "-q", "--no-verify", "-m", "side")
    _git(repo, "checkout", "-q", "main")
    (repo / "m.md").write_text("m\n")
    _git(repo, "add", "m.md")
    _git(repo, "commit", "-q", "--no-verify", "-m", "main")
    _hold(repo)
    _refused(_run(repo, "merge", "--no-edit", "side"), "commit-msg")


def test_a_git_am_patch_is_refused(tmp_path: Path) -> None:
    repo = _consumer(tmp_path)
    (repo / "p.md").write_text("p\n")
    _git(repo, "add", "p.md")
    _git(repo, "commit", "-q", "--no-verify", "-m", "patch me")
    patch = tmp_path / "p.patch"
    patch.write_text(_git(repo, "format-patch", "-1", "--stdout") + "\n")
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    _hold(repo)
    _refused(_run(repo, "am", str(patch)), "pre-applypatch")


def test_a_push_is_refused(tmp_path: Path) -> None:
    repo = _consumer(tmp_path)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, stdin=subprocess.DEVNULL)
    _git(repo, "remote", "add", "origin", str(bare))
    _hold(repo)
    _refused(_run(repo, "push", "-q", "origin", "main"), "pre-push")
    assert not _git(bare, "for-each-ref"), "the push reached the remote"
