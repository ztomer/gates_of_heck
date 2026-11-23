"""`goh land --check` refuses an item with no changelog fragment (GOH.FRAGMENTATLAND, 2026-11-22).

Two lanes landed with no changelog/ fragment, and the omission surfaced only when `goh ship` cut a
release and refused on an empty Unreleased -- a coordinator worktree, commit and extra land later.
In a repository that keeps changelog/ fragments, the land's check now names every item id a commit
subject of the range carries (`GOH.X: ...`) with no `changelog/<ID>.md` at the tip and no
`Changelog: none` trailer, where the lane can still add it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import hermetic_env

from lib import fragment_check


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, env=hermetic_env(drop_git=True),
                          stdin=subprocess.DEVNULL)  # fmt: skip
    return done.stdout.strip()


def _commit(repo: Path, files: dict[str, str], msg: str) -> None:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
        _git(repo, "add", name)
    _git(repo, "commit", "-q", "--no-verify", "-m", msg)


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    for k in [k for k in __import__("os").environ if k.startswith("GIT_")]:
        monkeypatch.delenv(k)
    r = tmp_path / "repo"
    fast_init(r, "main")
    for kv in (("user.email", "t@t"), ("user.name", "t"), ("core.hooksPath", "/dev/null")):
        _git(r, "config", *kv)
    _commit(r, {"a.txt": "a\n", "changelog/OLD.ONE.md": "* old\n"}, "base")
    _git(r, "switch", "-q", "-c", "lane")
    return r


def test_an_item_with_no_fragment_is_refused_by_name(repo) -> None:
    _commit(repo, {"b.txt": "b\n"}, "GOH.NOFRAG: a change")
    missing = fragment_check.missing(repo)
    assert len(missing) == 1 and "GOH.NOFRAG" in missing[0], missing
    assert "changelog/GOH.NOFRAG.md" in missing[0] and "Changelog: none" in missing[0], missing


def test_a_fragment_at_the_tip_passes(repo) -> None:
    _commit(repo, {"b.txt": "b\n"}, "GOH.FRAG: a change")
    _commit(repo, {"changelog/GOH.FRAG.md": "* it\n"}, "GOH.FRAG: its fragment")
    assert fragment_check.missing(repo) == []


def test_a_changelog_none_trailer_passes(repo) -> None:
    _commit(repo, {"b.txt": "b\n"}, "GOH.INTERNAL: test-only\n\nChangelog: none\n")
    assert fragment_check.missing(repo) == []


def test_a_repo_with_no_fragment_dir_is_not_judged(tmp_path, monkeypatch) -> None:
    for k in [k for k in __import__("os").environ if k.startswith("GIT_")]:
        monkeypatch.delenv(k)
    r = tmp_path / "plain"
    fast_init(r, "main")
    for kv in (("user.email", "t@t"), ("user.name", "t"), ("core.hooksPath", "/dev/null")):
        _git(r, "config", *kv)
    _commit(r, {"a.txt": "a\n"}, "base")
    _git(r, "switch", "-q", "-c", "lane")
    _commit(r, {"b.txt": "b\n"}, "SRV.X: no changelog dir here")
    assert fragment_check.missing(r) == []
