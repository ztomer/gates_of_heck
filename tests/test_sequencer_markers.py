"""Sequencer commits get the conflict-marker check (hooks/post-rewrite, hooks/pre-applypatch).

2026-10-08: a CHANGELOG.md conflict resolved during `git rebase --continue` kept a diff3 base
section (`||||||| parent of ...`), and only the full push gate caught it. Measured on git 2.56: a
rebase pick -- conflicted and resolved, or clean -- runs NO pre-commit and NO commit-msg; only
post-commit and, once the rebase ends, post-rewrite. `git am` and `git rebase --apply` run
pre-applypatch per patch, which the stock hooks did not install. Cherry-pick, revert and merge
`--continue` DO run pre-commit, so the staged gate already sees their resolutions.

So: post-rewrite scans what each rewritten commit changed (`goh markers --commits`) and fails
loudly (git ignores its exit; the rebase is already done), and pre-applypatch scans the index the
patch made and REFUSES it. Every test drives real git through the stock hooks.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env, native_goh_path, run_goh

# A diff3 resolution that kept the base section: the 2026-10-08 CHANGELOG.md, in miniature.
KEPT_BASE = "# changes\n||||||| parent of 1234567 (feature)\nbase entry\nmain entry\nside entry\n"
CLEAN = "# changes\nmain entry\nside entry\n"


def _env(**extra: str) -> dict[str, str]:
    return hermetic_env(
        drop_git=True,
        GOH_DIR=str(REPO_ROOT),
        GOH_BIN=str(native_goh_path()),
        GIT_EDITOR="true",
        GIT_SEQUENCE_EDITOR="true",
        **extra,
    )


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=_env())
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed:\n{r.stdout}{r.stderr}")
    return r


def _commit(repo: Path, rel: str, body: str, msg: str) -> str:
    (repo / rel).write_text(body)
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "--no-verify", "-m", msg)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    """A repo wired the way install.sh wires one (every stock hook FILE in .githooks/), with a
    diff3 conflict waiting: `side` and `main` both edit CHANGELOG.md from one base."""
    repo = tmp_path / "consumer"
    fast_init(repo, "main")
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("merge.conflictStyle", "diff3")):
        _git(repo, "config", k, v)
    # Tracked, as a consumer's are: a linked worktree has only what the branch carries.
    hooks = repo / ".githooks"
    hooks.mkdir()
    for stock in (REPO_ROOT / "hooks").iterdir():
        if stock.is_file():
            shutil.copy2(stock, hooks / stock.name)
    _git(repo, "add", ".githooks")
    _commit(repo, "CHANGELOG.md", "# changes\nbase entry\n", "base")
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "CHANGELOG.md", "# changes\nside entry\n", "feature")
    _commit(repo, "notes.txt", "later\n", "later")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "CHANGELOG.md", "# changes\nmain entry\n", "main")
    _git(repo, "config", "core.hooksPath", ".githooks")
    return repo


def _rebase_resolving_with(repo: Path, body: str, *backend: str) -> subprocess.CompletedProcess:
    _git(repo, "checkout", "-q", "side")
    stopped = _git(repo, "rebase", *backend, "main", check=False)
    assert stopped.returncode != 0, "the fixture's conflict did not happen"
    (repo / "CHANGELOG.md").write_text(body)
    _git(repo, "add", "CHANGELOG.md")
    return _git(repo, "rebase", "--continue", check=False)


# ---- the rebase pick that skipped every commit hook --------------------------------------------


def test_rebase_continue_with_a_kept_base_section_is_reported(consumer):
    r = _rebase_resolving_with(consumer, KEPT_BASE)
    out = r.stdout + r.stderr
    assert "no_conflict_markers" in out and "CHANGELOG.md:2" in out, out
    assert "||||||| parent of" in out, out
    # The report names the commit to fix, as git spells a blob in it.
    bad = _git(consumer, "rev-parse", "HEAD~1").stdout.strip()
    assert f"{bad[:10]}:CHANGELOG.md" in out, out


def test_rebase_continue_with_a_clean_resolution_is_quiet_about_markers(consumer):
    r = _rebase_resolving_with(consumer, CLEAN)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "✗" not in out, out
    # Absence is never compliance: the hook says it looked, and over what.
    assert "no markers in 2 commits" in out, out


def test_a_rebase_in_a_linked_worktree_is_reported(consumer, tmp_path):
    """From a linked worktree git hands the hook an absolute GIT_DIR (contract #12): the hook
    drops it and still finds the repository whose commits it judges."""
    wt = tmp_path / "wt"
    _git(consumer, "worktree", "add", "-q", "--detach", str(wt), "main")
    r = _rebase_resolving_with(wt, KEPT_BASE)
    assert "CHANGELOG.md:2" in r.stdout + r.stderr, r.stdout + r.stderr


def test_the_apply_backend_refuses_the_patch(consumer):
    r = _rebase_resolving_with(consumer, KEPT_BASE, "--apply")
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "CHANGELOG.md:2" in out, out
    # Refused, not committed: the resolution is still waiting in the index.
    assert (consumer / ".git" / "rebase-apply").is_dir()
    assert "||||||| parent of" not in _git(consumer, "show", "HEAD:CHANGELOG.md").stdout


# ---- git am: pre-applypatch is the commit gate, and it blocks ----------------------------------


def _am_resolving_with(repo: Path, body: str, *flags: str) -> subprocess.CompletedProcess:
    patches = repo.parent / "patches"
    _git(repo, "format-patch", "-q", "-1", "side~1", "-o", str(patches))
    stopped = _git(repo, "am", "-3", *flags, *map(str, sorted(patches.iterdir())), check=False)
    assert stopped.returncode != 0, "the fixture's conflict did not happen"
    (repo / "CHANGELOG.md").write_text(body)
    _git(repo, "add", "CHANGELOG.md")
    return _git(repo, "am", "--continue", *flags, check=False)


def test_am_continue_with_a_kept_base_section_is_refused(consumer):
    before = _git(consumer, "rev-parse", "HEAD").stdout
    r = _am_resolving_with(consumer, KEPT_BASE)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "CHANGELOG.md:2" in out, out
    assert _git(consumer, "rev-parse", "HEAD").stdout == before, "the marker was committed"


def test_am_continue_with_a_clean_resolution_commits(consumer):
    before = _git(consumer, "rev-parse", "HEAD").stdout
    r = _am_resolving_with(consumer, CLEAN)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _git(consumer, "rev-parse", "HEAD").stdout != before


def test_am_no_verify_is_the_escape_hatch(consumer):
    # git does not keep --no-verify across the stop: the continue that commits has to say it.
    r = _am_resolving_with(consumer, KEPT_BASE, "--no-verify")
    assert r.returncode == 0, r.stdout + r.stderr


def test_amend_is_left_to_pre_commit_and_its_escape_hatch(consumer):
    """An amend ran pre-commit, or skipped it with --no-verify on purpose: post-rewrite's
    `amend` call must not second-guess that, or the escape hatch stops being one."""
    (consumer / "CHANGELOG.md").write_text(KEPT_BASE)
    r = _git(consumer, "commit", "-q", "-a", "--amend", "--no-edit", "--no-verify")
    assert "no_conflict_markers" not in r.stdout + r.stderr


# ---- goh markers --commits: the scan the hook runs ---------------------------------------------


def test_commits_scope_is_what_each_commit_changed(consumer):
    # `side` adds notes.txt; a marker already in a file it did NOT touch is not its finding.
    _git(consumer, "checkout", "-q", "side~1")
    _commit(consumer, "CHANGELOG.md", KEPT_BASE, "bad")
    _commit(consumer, "other.txt", "fine\n", "unrelated")
    head = _git(consumer, "rev-parse", "HEAD").stdout.strip()
    r = run_goh(consumer, "markers", "--commits", head)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no markers in 1 commit" in r.stdout
    r = run_goh(consumer, "markers", "--commits", "HEAD~1", head)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "CHANGELOG.md:2" in r.stderr and "other.txt" not in r.stderr


def test_commits_scope_reads_the_commit_not_the_worktree(consumer):
    _commit(consumer, "CHANGELOG.md", KEPT_BASE, "bad")
    (consumer / "CHANGELOG.md").write_text(CLEAN)  # fixed in the tree, not in the commit
    assert run_goh(consumer, "markers", "--commits", "HEAD").returncode == 1


def test_commits_scope_covers_a_root_commit(tmp_path):
    repo = tmp_path / "fresh"
    fast_init(repo, "main")
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        _git(repo, "config", k, v)
    _commit(repo, "a.txt", ">>>>>>> theirs\n", "root")
    r = run_goh(repo, "markers", "--commits", "HEAD")
    assert r.returncode == 1 and "a.txt:1" in r.stderr, r.stdout + r.stderr


def test_commits_scope_judges_a_merge_against_its_first_parent(consumer):
    _git(consumer, "merge", "-q", "side", check=False)
    (consumer / "CHANGELOG.md").write_text(KEPT_BASE)
    _git(consumer, "add", "CHANGELOG.md")
    _git(consumer, "commit", "-q", "--no-verify", "--no-edit")
    r = run_goh(consumer, "markers", "--commits", "HEAD")
    assert r.returncode == 1 and "CHANGELOG.md:2" in r.stderr, r.stdout + r.stderr


def test_an_unknown_commit_cannot_be_judged(consumer):
    r = run_goh(consumer, "markers", "--commits", "no-such-rev")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "no-such-rev" in r.stderr
