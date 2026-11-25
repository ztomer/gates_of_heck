"""A test's goh.db leaves no journal files, and the tests that need one are a list that only shrinks
(tests/_db_on_disk.py, lib/db_settings.py `throwaway`)."""

from __future__ import annotations

import ast
import os
from pathlib import Path

import db_core
import db_settings
import goh_db
import pytest
from _db_on_disk import ENV, hold, on_disk
from conftest import REPO_ROOT

TREE_READS = ("tests/",)  # each name of the list is a test of a file there
LISTED = 11  # only ever lowered, with the names that left


def _mode(path: Path) -> tuple[str, list[str]]:
    with db_core.transaction(goh_db.SEAM, at=path) as con:
        con.execute("INSERT INTO kv(name, body, at) VALUES ('k', 'v', 0)")
        mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        return mode, sorted(p.name for p in path.parent.iterdir())


def _page_size(path: Path) -> int:
    return int.from_bytes(path.read_bytes()[16:18], "big")  # SQLite's file header


def test_a_tests_own_database_leaves_no_file_beside_it(tmp_path, monkeypatch) -> None:
    assert Path(tmp_path).is_relative_to(Path(os.environ[ENV]).resolve())
    (tmp_path / "mine").mkdir()
    (tmp_path / "wal").mkdir()
    assert _mode(tmp_path / "mine" / "goh.db") == ("memory", ["goh.db"])
    monkeypatch.delenv(ENV)  # production's own: the reading above can tell the two apart
    assert _mode(tmp_path / "wal" / "goh.db") == ("wal", ["goh.db", "goh.db-shm", "goh.db-wal"])
    pages = [_page_size(tmp_path / d / "goh.db") for d in ("mine", "wal")]
    assert pages == [512, 4096]  # a table with one row is 512 bytes of it, not 4 KB


def test_a_database_outside_the_run_is_never_a_throwaway(tmp_path) -> None:
    env = {ENV: str(tmp_path / "run")}
    assert db_settings.throwaway(tmp_path / "run" / "a" / "goh.db", env)
    assert not db_settings.throwaway(tmp_path / "goh.db", env)
    assert not db_settings.throwaway(tmp_path / "run2" / "goh.db", env)
    assert not db_settings.throwaway(tmp_path / "goh.db", {})


def test_every_listed_test_stands_says_why_and_the_list_only_shrinks() -> None:
    assert len(on_disk()) <= LISTED, "a test of no WAL, lock or crash journals in memory"
    for name, why in on_disk().items():
        rel, test = name.split("::")
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        assert test in {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}, name
        assert why.endswith("is the subject") or "WAL" in why, (name, why)


@pytest.mark.parametrize("env", [None, ""])
def test_no_root_named_is_production(tmp_path, monkeypatch, env) -> None:
    assert not db_settings.throwaway(tmp_path / "goh.db", {} if env is None else {ENV: env})


def test_a_held_wal_database_keeps_its_files_and_every_writer_writes(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(ENV)
    path = tmp_path / "goh.db"
    made = ["goh.db", "goh.db-shm", "goh.db-wal"]

    def there() -> list[str]:
        return sorted(p.name for p in tmp_path.iterdir())

    _mode(path)
    assert there() == ["goh.db"], "an unheld one's are removed at its close: the reading can tell"
    con = hold(path)
    ino = {n: (tmp_path / n).stat().st_ino for n in made}
    with db_core.transaction(goh_db.SEAM, at=path) as w:
        w.execute("UPDATE kv SET body = 'w'")
    assert there() == made and {n: (tmp_path / n).stat().st_ino for n in made} == ino
    assert con.execute("SELECT body FROM kv").fetchone() == ("w",)
    con.close()
    assert there() == ["goh.db"]
