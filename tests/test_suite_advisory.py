"""tests/suite_advisory.py: a run's audits read one copy of cargo's database, made once."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import suite_advisory
from conftest import REPO_ROOT, commit_all, write
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT))
from lib import advisory_db


@pytest.fixture
def parent(tmp_path: Path) -> Path:
    (tmp_path / "suite").mkdir()
    yield tmp_path / "suite"
    for copy in (tmp_path / "suite").glob("advisory-db.*"):
        if copy.is_dir():
            suite_advisory._remove(copy)


def test_every_run_links_the_one_copy_and_an_audit_seeds_none(
    repo: Path, parent: Path, tmp_path: Path, monkeypatch
) -> None:
    one = suite_advisory.link(tmp_path / "one", parent, repo)
    made = one.stat().st_ino
    two = suite_advisory.link(tmp_path / "two", parent, repo)
    assert one == two and two.stat().st_ino == made, "a second copy of one commit"
    assert (two / "README.md").read_text() == "# fixture\n"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "two" / ".cache"))
    monkeypatch.delenv("GOH_ADVISORY_DB", raising=False)
    monkeypatch.setattr(advisory_db, "_seed", lambda db: pytest.fail(f"seeded {db}"))
    with advisory_db.held(fetch=False) as db:
        assert (db / "README.md").is_file() and db.resolve() == one.resolve()


def test_a_suite_in_a_suites_home_takes_that_homes_copy(
    repo: Path, parent: Path, tmp_path: Path
) -> None:
    outer = suite_advisory.link(tmp_path / "home", parent, repo)
    nested = tmp_path / "home" / ".cache" / "goh" / "suite"
    nested.mkdir()
    copy = suite_advisory.link(tmp_path / "inner", nested, repo)
    assert copy.samefile(outer) and not list(nested.iterdir()), "a copy of the copy"
    assert (tmp_path / "inner" / ".cache" / "goh" / "advisory-db" / "README.md").is_file()


def test_a_refresh_of_it_is_refused(repo: Path, parent: Path, tmp_path: Path) -> None:
    copy = suite_advisory.link(tmp_path / "home", parent, repo)
    r = subprocess.run(["git", "-C", str(copy), "fetch", "-q", str(repo)], capture_output=True,
                       text=True, check=False, stdin=subprocess.DEVNULL,
                       timeout=patience(60))  # fmt: skip
    assert r.returncode != 0 and "ermission denied" in r.stderr, r.stderr


def test_a_database_that_moved_is_another_copy_and_the_left_one_goes(
    repo: Path, parent: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(suite_advisory, "_make", lambda _shared, copy: copy.mkdir())  # made above
    one = suite_advisory.link(tmp_path / "one", parent, repo)
    commit_all(write(repo, "RUSTSEC-2.md", "two\n").parent, "two")
    two = suite_advisory.link(tmp_path / "two", parent, repo)
    assert two != one and two.is_dir()
    assert one.is_dir(), "a run may read it still"
    old = one.stat().st_mtime - suite_advisory.OLD_S - 60
    os.utime(one, (old, old))
    suite_advisory.link(tmp_path / "three", parent, repo)
    assert not one.exists() and two.is_dir()


def test_no_database_leaves_the_home_as_it_was(parent: Path, tmp_path: Path) -> None:
    assert suite_advisory.link(tmp_path / "home", parent, tmp_path / "none") is None
    assert not os.path.lexists(tmp_path / "home" / ".cache" / "goh" / "advisory-db")


def test_the_suites_own_home_has_it() -> None:
    home = Path(os.environ["HOME"]) / ".cache" / "goh" / "advisory-db"
    shared = Path(os.environ["GATES_OF_HECK_SUITE_REAL_HOME"]) / ".cargo" / "advisory-db"
    if not (shared / ".git" / "HEAD").is_file():
        pytest.skip("this machine's cargo has no advisory database")
    assert home.is_symlink() and (home / "crates").is_dir()
