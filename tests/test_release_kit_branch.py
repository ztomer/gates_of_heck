"""The release kit pushes the release commit to the repo's DEFAULT branch, never a stray one (GOH.RELBRANCH).

2026-11: goh's release.sh was run from a tools/land.sh worktree. It pushed `$BRANCH` -- the CURRENT
branch, `goh-loadlock` -- to origin: a stray branch on the remote, main left behind, and main had to
be pushed by hand. The branch the release goes to is origin's default branch, whatever branch the
release is cut from. From another branch the push is `HEAD:refs/heads/<default>`, and only when it
fast-forwards origin's default branch; otherwise the release refuses before anything slow, naming
the way forward. An explicit --branch still names the branch to push.
"""

from __future__ import annotations

from test_release_kit import TAG, commit_all, kit, run_release, sh  # noqa: F401  (kit: a fixture)


def _heads(kit: dict) -> list[str]:
    return sorted(
        sh(kit["origin"], "for-each-ref", "--format=%(refname:short)", "refs/heads").split()
    )


def _on_feature_branch(kit: dict, name: str = "wt-feature") -> None:
    """HEAD on a branch other than main, one commit ahead of it, as a land.sh worktree is."""
    sh(kit["proj"], "checkout", "-q", "-b", name)
    (kit["proj"] / "work.txt").write_text("work\n", encoding="utf-8")
    commit_all(kit["proj"], "work on the feature branch")


def test_a_release_from_a_feature_branch_pushes_to_the_default_branch_and_no_stray(kit):
    _on_feature_branch(kit)
    r = run_release(kit, "--skip-release")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    head = sh(kit["proj"], "rev-parse", "HEAD").strip()
    assert sh(kit["origin"], "rev-parse", "refs/heads/main").strip() == head, out
    assert sh(kit["origin"], "rev-parse", f"refs/tags/{TAG}^{{commit}}").strip() == head, out
    assert _heads(kit) == ["main"], f"a stray branch reached origin: {_heads(kit)}"


def test_the_push_names_the_default_branch_as_its_target(kit):
    _on_feature_branch(kit)
    r = run_release(kit, "--skip-release")
    assert r.returncode == 0, r.stdout + r.stderr
    pushes = [c for c in kit["git_log"].read_text().splitlines() if c.startswith("git push")]
    assert pushes == [f"git push origin HEAD:refs/heads/main {TAG}"], pushes


def test_a_branch_that_diverged_from_the_default_branch_is_refused_before_anything_is_pushed(kit):
    sh(kit["proj"], "checkout", "-q", "-b", "wt-diverged")
    (kit["proj"] / "mine.txt").write_text("mine\n", encoding="utf-8")
    commit_all(kit["proj"], "mine")
    # origin's main moves on without it
    other = kit["proj"].parent / "other"
    sh(kit["proj"].parent, "clone", "-q", str(kit["origin"]), str(other))
    (other / "theirs.txt").write_text("theirs\n", encoding="utf-8")
    commit_all(other, "theirs")
    sh(other, "push", "-q", "origin", "HEAD:main")
    r = run_release(kit, "--skip-release")
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "main" in out and "rebase" in out and "nothing pushed" in out, out
    assert _heads(kit) == ["main"] and TAG not in sh(kit["origin"], "tag", "-l")


def test_the_dry_run_plans_the_default_branch_too(kit):
    _on_feature_branch(kit)
    sh(kit["proj"], "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    r = run_release(kit, "--dry-run")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert f"git push origin HEAD:refs/heads/main {TAG}" in out, out
    assert "wt-feature" not in out.replace("on wt-feature", ""), out


def test_the_default_branch_is_read_from_origin_not_assumed_to_be_main(kit):
    sh(kit["origin"], "branch", "-m", "main", "trunk")
    sh(kit["origin"], "symbolic-ref", "HEAD", "refs/heads/trunk")
    sh(kit["proj"], "branch", "-m", "main", "local-name")
    r = run_release(kit, "--skip-release")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert _heads(kit) == ["trunk"], _heads(kit)


def test_an_explicit_branch_still_names_the_branch_pushed(kit):
    _on_feature_branch(kit)
    r = run_release(kit, "--skip-release", "--branch", "wt-feature")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _heads(kit) == ["main", "wt-feature"], _heads(kit)
