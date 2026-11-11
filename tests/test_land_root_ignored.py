"""The land root (`build/`) is ignored, so an open land worktree never dirties the main checkout.

tools/land.sh runs from a linked worktree, and the house puts those under `<checkout>/build/<name>`
(`git worktree add build/x`, the shape lib/queue_hold.py names). `build/` was not in .gitignore, so
while a land was open the main checkout showed `?? build/` and every estate dirty-check read goh as
uncommitted (GOH.BUILDIGNORE, 2026-11-11). The repo's own .gitignore is the subject: it is copied
into a fixture checkout, a worktree is opened under build/ exactly as a land does, and the main
checkout must stay clean.
"""

from __future__ import annotations

from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, write

# Directories goh's own tools create inside a checkout (not the user's build output): the land root.
TOOL_OWNED = ("build/",)


def _checkout_with_repo_ignore(repo: Path) -> Path:
    write(repo, ".gitignore", (REPO_ROOT / ".gitignore").read_text())
    commit_all(repo)
    return repo


def test_an_open_land_worktree_leaves_the_main_checkout_clean(repo: Path) -> None:
    main = _checkout_with_repo_ignore(repo)
    git(main, "worktree", "add", "-q", "-b", "feature", str(main / "build" / "land-x"))
    write(main / "build" / "land-x", "f.txt", "feature\n")
    assert git(main, "status", "--porcelain") == ""


def test_every_tool_owned_directory_is_ignored(repo: Path) -> None:
    main = _checkout_with_repo_ignore(repo)
    for rel in TOOL_OWNED:
        (main / rel / "x").mkdir(parents=True)
        (main / rel / "x" / "f").write_text("x\n")
    assert git(main, "status", "--porcelain") == ""
