"""tools/release_goh.sh: goh's own GOH_SHIP_RELEASE kit (GOH.SHIPKIT, 2026-11-11).

`goh ship` could not release goh: no kit was declared, and v0.95.0's bump (8e90921) was made by
hand. The kit bumps [workspace.package] and Cargo.lock, cuts Unreleased into `## v{version}` in a
build/ worktree, lands it through tools/land.sh (main moves only through land: GOH_LANDED_ONLY),
pushes main, then tags v{version} with the stanza as its message and pushes the tag. What it must
refuse before it touches anything: a dirty tree, and an Unreleased with nothing to release.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, git, hermetic_env

KIT = REPO_ROOT / "tools" / "release_goh.sh"
# The macOS /bin/bash is 3.2; the hosted runner and minimal-PATH hooks start the kit with it, so the
# suite does too: the bash 5 guard must re-exec it (lib/bash5.sh; v0.106.1 was red there).
BASH = "/bin/bash" if Path("/bin/bash").exists() else "bash"


def _kit(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([BASH, str(KIT), *args], cwd=repo, env=hermetic_env(drop_git=True),
                          capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
                          check=False)  # fmt: skip


def _commit(repo: Path, changelog: str) -> None:
    (repo / "CHANGELOG.md").write_text(changelog)
    (repo / "Cargo.toml").write_text('[workspace.package]\nversion = "0.1.0"\n')
    git(repo, "add", "CHANGELOG.md", "Cargo.toml")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "feat: x")


def test_an_empty_unreleased_is_refused_before_anything_moves(repo: Path) -> None:
    _commit(repo, "# CHANGELOG\n\n## Unreleased\n\n## v0.1.0 -- first\n\n* **One.** a\n")
    head = git(repo, "rev-parse", "HEAD")
    r = _kit(repo, "0.2.0")
    assert r.returncode != 0, r.stdout + r.stderr
    assert "Unreleased is empty" in r.stderr, r.stderr
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "worktree", "list").count("\n") == 1, "a refused release made a worktree"


def test_a_dirty_tree_is_refused_before_anything_moves(repo: Path) -> None:
    _commit(
        repo, "# CHANGELOG\n\n## Unreleased\n\n* **Two.** b\n\n## v0.1.0 -- first\n\n* **One.** a\n"
    )
    (repo / "Cargo.toml").write_text('[workspace.package]\nversion = "9.9.9"\n')
    r = _kit(repo, "0.2.0")
    assert r.returncode != 0, r.stdout + r.stderr
    assert "uncommitted" in r.stderr, r.stderr


def test_a_version_that_is_not_semver_is_refused(repo: Path) -> None:
    r = _kit(repo, "v0.2")
    assert r.returncode == 2 and "X.Y.Z" in r.stderr, r.stderr


def test_ship_is_told_the_kit_and_that_it_pushes() -> None:
    rc = (REPO_ROOT / ".gatesrc").read_text()
    assert "GOH_SHIP_RELEASE='tools/release_goh.sh {version}'" in rc
    assert "GOH_SHIP_RELEASE_PUSHES=1" in rc


def test_the_ship_summary_titles_the_preflight_too(repo: Path) -> None:
    """The kit's own `ready` preflight ran without GOH_SHIP_SUMMARY, so an entry that derives no
    title refused a ship that passed --summary, and the refusal was reported as an empty
    Unreleased (v0.106.1, 2026-11-12)."""
    _commit(
        repo, "# CHANGELOG\n\n## Unreleased\n\n* , a tail\n\n## v0.1.0 -- first\n\n* **One.** a\n"
    )
    env = hermetic_env(drop_git=True, GOH_SHIP_SUMMARY="the real title")
    r = subprocess.run([BASH, str(KIT), "0.2.0"], cwd=repo, env=env, capture_output=True,
                       text=True, timeout=60, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert "is not a phrase" not in r.stderr, r.stderr
    assert "holds nothing to release" not in r.stderr, r.stderr


def test_a_title_refusal_is_not_reported_as_an_empty_unreleased(repo: Path) -> None:
    _commit(
        repo, "# CHANGELOG\n\n## Unreleased\n\n* , a tail\n\n## v0.1.0 -- first\n\n* **One.** a\n"
    )
    r = _kit(repo, "0.2.0")
    assert r.returncode != 0 and "is not a phrase" in r.stderr, r.stderr
    assert "holds nothing to release" not in r.stderr, r.stderr
