"""The session's `goh` fixture (tests/conftest.py): one build per session, under xdist too."""

from __future__ import annotations

from pathlib import Path


def test_goh_is_built_once_per_session(goh: Path, goh_build_count: int) -> None:
    """The class behind 23 errors in one pre-push run (2026-09-22): a session fixture under xdist
    runs once PER WORKER, and each worker's `cargo build` re-linked the binary another worker was
    copying. One build per session is the invariant; more than one is the race coming back."""
    assert goh.exists()
    assert goh_build_count == 1, f"goh was built {goh_build_count} times in one session"


def test_the_repo_fixture_starts_no_background_git(repo: Path) -> None:
    """Every commit detaches `git maintenance run --auto`, which takes and drops
    .git/objects/maintenance.lock: `repo` copies the session template while that runs, and on a
    loaded box the copy raced it (shutil.Error, a rotating ERROR in every push gate, 2026-10-08).
    The copies inherit the template's config, so none of their commits starts one either."""
    from conftest import git

    assert git(repo, "config", "--get", "maintenance.auto").strip() == "false"
