"""hold_repo.sh / release_hold.sh, and the archive wrappers over them, without GitHub.

`archive_repo.sh` archives the repo under the OWNER's account (never an upstream someone else owns),
only when nothing exists only in the clone, then holds it read-only with unarchive_repo.sh as the
lift. `unarchive_repo.sh` lifts it only once GitHub says it is live again. GitHub is a fake `gh`
(GOH_GH) keeping each repo's archived flag in a state directory; the owner's login is GOH_GH_USER.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git, hermetic_env

SCRIPTS = REPO_ROOT / "scripts"
FAKE_GH = """#!/usr/bin/env bash
printf 'gh %s\\n' "$*" >> "$GH_STATE/log"
slug() { printf '%s' "$1" | tr / _; }
case "$1 $2" in
  "repo view") f="$GH_STATE/$(slug "$3")"; [ -f "$f" ] && cat "$f" || echo false ;;
  "repo archive") echo true > "$GH_STATE/$(slug "$3")" ;;
  "repo unarchive") echo false > "$GH_STATE/$(slug "$3")" ;;
  *) exit 1 ;;
esac
"""


@pytest.fixture
def gh(tmp_path: Path) -> dict[str, str]:
    state = tmp_path / "gh"
    state.mkdir()
    fake = tmp_path / "fake-gh"
    fake.write_text(FAKE_GH)
    fake.chmod(0o755)
    return {"GOH_GH": str(fake), "GOH_GH_USER": "ztomer", "GH_STATE": str(state)}


@pytest.fixture
def clone(repo: Path, tmp_path: Path):
    """A clone whose `origin` is someone else's and whose `estate` is the owner's, all pushed."""
    git(repo, "remote", "add", "origin", "https://github.com/Galzai/apes.git")
    git(repo, "remote", "add", "estate", "git@github.com:ztomer/apes.git")
    git(repo, "update-ref", "refs/remotes/estate/main", "HEAD")
    yield repo
    for root, dirs, _files in os.walk(repo):  # a read-only fixture must still be removable
        os.chmod(root, os.stat(root).st_mode | stat.S_IWUSR)


def _sh(
    script: str, *args: str, env: dict[str, str], stdin: str = ""
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPTS / script), *args], env=hermetic_env(**env), input=stdin,
        capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip


def _held(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "goh.hold"], capture_output=True, text=True,
        stdin=subprocess.DEVNULL, check=False,
    ).stdout.strip()  # fmt: skip


def _writable(p: Path) -> bool:
    return bool(p.stat().st_mode & stat.S_IWUSR)


def test_hold_then_release_round_trips_with_and_without_read_only(repo: Path) -> None:
    r = _sh("hold_repo.sh", str(repo), "--reason", "parked", env={})
    assert r.returncode == 0, r.stderr
    assert "parked -- lift: " in _held(repo) and "release_hold.sh" in _held(repo)
    assert _writable(repo / "README.md")
    again = _sh("hold_repo.sh", str(repo), "--reason", "x", env={})
    assert again.returncode != 0 and "already on hold" in again.stderr
    assert _sh("release_hold.sh", str(repo), env={}).returncode == 0
    assert not _held(repo)
    assert _sh("hold_repo.sh", str(repo), "--reason", "ro", "--read-only", env={}).returncode == 0
    assert not _writable(repo / "README.md")
    r = _sh("release_hold.sh", str(repo), env={})
    assert r.returncode == 0, r.stderr
    assert _writable(repo / "README.md") and not _held(repo)


def test_archive_refuses_a_dirty_tree_and_unpushed_commits_naming_them(
    clone: Path, gh: dict[str, str]
) -> None:
    (clone / "loose.txt").write_text("x\n")
    r = _sh("archive_repo.sh", str(clone), env=gh)
    assert r.returncode != 0 and "loose.txt" in r.stderr, r.stderr
    (clone / "loose.txt").unlink()
    (clone / "README.md").write_text("changed\n")
    git(clone, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "only here")
    r = _sh("archive_repo.sh", str(clone), env=gh)
    assert r.returncode != 0 and "only here" in r.stderr, r.stderr
    log = Path(gh["GH_STATE"]) / "log"
    assert not _held(clone) and "archive" not in (log.read_text("utf-8") if log.exists() else "")


def test_archive_refuses_a_repo_with_no_remote_under_the_owner(
    repo: Path, gh: dict[str, str]
) -> None:
    git(repo, "remote", "add", "origin", "https://github.com/Galzai/apes.git")
    r = _sh("archive_repo.sh", str(repo), env=gh)
    assert r.returncode != 0 and "Galzai/apes" in r.stderr and "another account" in r.stderr
    assert not (Path(gh["GH_STATE"]) / "Galzai_apes").exists()


def test_archive_archives_the_owners_remote_holds_read_only_and_unarchive_lifts(
    clone: Path, gh: dict[str, str]
) -> None:
    r = _sh("archive_repo.sh", str(clone), env=gh)
    assert r.returncode == 0, r.stderr
    state = Path(gh["GH_STATE"])
    assert (state / "ztomer_apes").read_text().strip() == "true"
    assert not (state / "Galzai_apes").exists(), "another account's repo was touched"
    assert "ztomer/apes" in _held(clone) and "unarchive_repo.sh" in _held(clone)
    assert not _writable(clone / "README.md")
    # release_hold.sh alone will not lift an archive: its lift is unarchive_repo.sh.
    r = _sh("release_hold.sh", str(clone), env=gh)
    assert r.returncode != 0 and "unarchive_repo.sh" in r.stderr
    # Still archived on GitHub, not interactive: refused, naming the command.
    r = _sh("unarchive_repo.sh", str(clone), env=gh)
    assert r.returncode != 0 and "gh repo unarchive ztomer/apes" in r.stderr
    assert _held(clone) and not _writable(clone / "README.md")
    # Interactive, declined: still held.
    r = _sh("unarchive_repo.sh", str(clone), env={**gh, "GOH_HOLD_INTERACTIVE": "1"}, stdin="n\n")
    assert r.returncode != 0 and _held(clone)
    # The owner unarchives on GitHub: the hold lifts.
    (state / "ztomer_apes").write_text("false\n")
    r = _sh("unarchive_repo.sh", str(clone), env=gh)
    assert r.returncode == 0, r.stderr
    assert not _held(clone) and _writable(clone / "README.md")


def test_unarchive_lifts_the_legacy_archived_key(clone: Path, gh: dict[str, str]) -> None:
    git(clone, "config", "--local", "goh.archived", "2026-10-26: archived by hand")
    clone.joinpath("README.md").chmod(0o444)
    r = _sh("unarchive_repo.sh", str(clone), env=gh)
    assert r.returncode == 0, r.stderr
    out = subprocess.run(
        ["git", "-C", str(clone), "config", "--get", "goh.archived"], capture_output=True,
        text=True, stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    assert out.returncode == 1 and _writable(clone / "README.md")


def _lying_gh(tmp_path: Path, gh: dict[str, str]) -> dict[str, str]:
    """A gh whose archive/unarchive exit 0 and change nothing (G15: success claimed over no state)."""
    lie = tmp_path / "lying-gh"
    lie.write_text(FAKE_GH.replace('echo true > "$GH_STATE/$(slug "$3")"', ":")
                   .replace('echo false > "$GH_STATE/$(slug "$3")"', ":"))  # fmt: skip
    lie.chmod(0o755)
    return {**gh, "GOH_GH": str(lie)}


def test_archive_that_changed_nothing_on_github_is_not_reported_archived(
    clone: Path, gh: dict[str, str], tmp_path: Path
) -> None:
    r = _sh("archive_repo.sh", str(clone), env=_lying_gh(tmp_path, gh))
    assert r.returncode != 0 and "archived ztomer/apes" not in r.stdout, r.stdout + r.stderr
    assert "still not archived" in r.stderr and not _held(clone)


def test_unarchive_that_changed_nothing_on_github_is_not_reported_unarchived(
    clone: Path, gh: dict[str, str], tmp_path: Path
) -> None:
    (Path(gh["GH_STATE"]) / "ztomer_apes").write_text("true\n")
    git(clone, "config", "--local", "goh.hold", "2026-10-26: archived -- lift: x")
    env = {**_lying_gh(tmp_path, gh), "GOH_HOLD_INTERACTIVE": "1"}
    r = _sh("unarchive_repo.sh", str(clone), env=env, stdin="y\n")
    assert r.returncode != 0 and "unarchived ztomer/apes on GitHub" not in r.stdout, r.stdout
    assert "still archived" in r.stderr
