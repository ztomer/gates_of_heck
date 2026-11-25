"""A test's goh.db leaves no journal files, and the tests that need one are a list that only shrinks
(tests/_db_on_disk.py, lib/db_settings.py `throwaway`)."""

from __future__ import annotations

import ast
import dataclasses
import os
import sys
from pathlib import Path

import db_core
import db_settings
import goh_db
import pytest
from _db_on_disk import ENV, SEEDS, hold, on_disk
from _fs_bytes import clone_of
from conftest import REPO_ROOT

TREE_READS = ("tests/",)  # each name of the list is a test of a file there
LISTED = 11  # only ever lowered, with the names that left


def _mode(path: Path, seam: db_core.Seam = goh_db.SEAM) -> tuple[str, list[str]]:
    with db_core.transaction(seam, at=path) as con:
        con.execute("INSERT INTO kv(name, body, at) VALUES ('k', 'v', 0)")
        mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        return mode, sorted(p.name for p in path.parent.iterdir())


def _small(tmp_path: Path) -> db_core.Seam:
    """The journal is the subject, not goh.db's tables: one is enough."""
    (schema := tmp_path / "schema").mkdir()
    (schema / "0001_kv.sql").write_text("CREATE TABLE kv(name TEXT, body TEXT, at REAL);\n")
    return dataclasses.replace(goh_db.SEAM, schema=schema)


def _page_size(path: Path) -> int:
    return int.from_bytes(path.read_bytes()[16:18], "big")  # SQLite's file header


def test_a_tests_own_database_leaves_no_file_beside_it(tmp_path, monkeypatch) -> None:
    assert Path(tmp_path).is_relative_to(Path(os.environ[ENV]).resolve())
    (tmp_path / "mine").mkdir()
    (tmp_path / "wal").mkdir()
    seam = _small(tmp_path)
    assert _mode(tmp_path / "mine" / "goh.db", seam) == ("memory", ["goh.db"])
    monkeypatch.delenv(ENV)  # production's own: the reading above can tell the two apart
    assert _mode(tmp_path / "wal" / "goh.db", seam) == (
        "wal",
        ["goh.db", "goh.db-shm", "goh.db-wal"],
    )
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
    seam = _small(tmp_path)
    (tmp_path / "db").mkdir()
    path = tmp_path / "db" / "goh.db"
    made = ["goh.db", "goh.db-shm", "goh.db-wal"]

    def there() -> list[str]:
        return sorted(p.name for p in path.parent.iterdir())

    _mode(path, seam)
    assert there() == ["goh.db"], "an unheld one's are removed at its close: the reading can tell"
    con = hold(path)
    ino = {n: (path.parent / n).stat().st_ino for n in made}
    with db_core.transaction(seam, at=path) as w:
        w.execute("UPDATE kv SET body = 'w'")
    assert there() == made and {n: (path.parent / n).stat().st_ino for n in made} == ino
    assert con.execute("SELECT body FROM kv").fetchone() == ("w",)
    con.close()
    assert there() == ["goh.db"]


def _rows(path: Path) -> tuple:
    with db_core.ro(goh_db.SEAM, at=path) as con:
        return db_core.version(con), con.execute("SELECT count(*) FROM kv").fetchone()[0]


@pytest.mark.skipif(sys.platform != "darwin", reason="clonefile(2) is macOS's")
def test_a_fresh_database_is_a_clone_of_the_runs_first_and_holds_none_of_its_rows(
    tmp_path, monkeypatch
) -> None:
    assert os.environ.get(SEEDS) is None, "a maker's test is never seeded (_db_on_disk.MAKERS)"
    (seeds := tmp_path / "seeds").mkdir()
    monkeypatch.setenv(SEEDS, str(seeds))
    for d in ("a", "b", "wal"):
        (tmp_path / d).mkdir()
    _mode(tmp_path / "a" / "goh.db")  # makes it, keeps it, then writes a row
    (kept,) = seeds.iterdir()
    calls: list[int] = []
    monkeypatch.setattr(db_core.db_schema, "fresh", lambda *a: calls.append(1))
    with goh_db.connect(tmp_path / "b" / "goh.db"):
        pass
    assert not calls and clone_of(str(tmp_path / "b" / "goh.db"))[0] == clone_of(str(kept))[0]
    made, rows = _rows(tmp_path / "b" / "goh.db")
    assert (made, rows) == (_rows(tmp_path / "a" / "goh.db")[0], 0)
    with goh_db.transaction(tmp_path / "b" / "goh.db") as con:
        con.execute("DELETE FROM schema_version")  # a clone: the kept one is not this file
    assert _rows(kept)[0]["applied"] == made["applied"] and len(list(seeds.iterdir())) == 1


def test_a_seed_is_one_schemas_and_one_codes_and_only_a_throwaways(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(SEEDS, str(tmp_path))
    (schema := tmp_path / "schema").mkdir()
    (schema / "0001_a.sql").write_text("CREATE TABLE a(x);")
    p = tmp_path / "goh.db"
    first = db_settings._seed_file(schema, p, tmp_path)
    if sys.platform == "darwin":
        assert first == db_settings._seed_file(schema, tmp_path / "o.db", tmp_path) is not None
        assert first != db_settings._seed_file(schema, p, tmp_path / "another checkout")
    (schema / "0001_a.sql").write_text("CREATE TABLE a(y);")
    assert first is None or first != db_settings._seed_file(schema, p, tmp_path)
    monkeypatch.delenv(ENV)
    assert db_settings._seed_file(schema, p, tmp_path) is None, "production's file is never seeded"
