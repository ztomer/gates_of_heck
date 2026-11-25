"""lib/db_contract.py (GOH.OLDWRITERS): `goh state contract N` applies a by-command number, in
order, under the write lock, the file copied first; refused or killed, the file is as it was."""

from __future__ import annotations

import dataclasses
import hashlib
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import db_contract
import db_core
import db_schema
import goh_db
import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

HEAD = f"-- old-writers: refuse\n{db_schema.BY_COMMAND}\n"
SQL = {
    1: "CREATE TABLE t(x INTEGER, body TEXT);\n",
    2: "-- old-writers: safe\nCREATE TABLE u(x INTEGER);\n",
    3: HEAD + "CREATE TABLE r(n INTEGER CHECK (n = 0));\n"
       "INSERT INTO r(n) SELECT count(*) FROM t WHERE body <> '{}';\nDROP TABLE r;\n"
       "ALTER TABLE t DROP COLUMN body;\n",
    4: HEAD + "DROP TABLE u;\n",
}  # fmt: skip


def seam(tmp_path: Path, monkeypatch, body: str = "{}") -> db_core.Seam:
    d = tmp_path / "schema"
    d.mkdir()
    for n, sql in SQL.items():
        (d / f"{n:04d}_m.sql").write_text(sql)
    monkeypatch.setenv("GOH_DB", str(tmp_path / "goh.db"))
    s = dataclasses.replace(goh_db.SEAM, schema=d)
    with db_core.transaction(s) as con:
        con.execute("INSERT INTO t(x, body) VALUES (1, ?)", (body,))
    return s


def dump(p: Path) -> str:
    con = sqlite3.connect(p)
    try:
        uv = con.execute("PRAGMA user_version").fetchone()
        return hashlib.sha256("\n".join([*con.iterdump(), str(uv)]).encode()).hexdigest()
    finally:
        con.close()


def test_contracts_go_in_order_and_leave_the_file_before(tmp_path, monkeypatch) -> None:
    s = seam(tmp_path, monkeypatch)
    for n, why in ((1, "applied by an open"), (4, "0003_m is not applied"), (9, "ships no number")):
        with pytest.raises(db_contract.ContractRefused, match=why):
            db_contract.contract(s, n)
    assert db_contract.copies(s) == []
    was = dump(tmp_path / "goh.db")
    first = db_contract.contract(s, 3)
    assert dump(first) == was and first.name.startswith("goh-before-0003-")
    with db_core.ro(s) as con:
        v = db_core.version(con)
        assert [r[1] for r in con.execute("PRAGMA table_info(t)")] == ["x"]
    assert v["user_version"] == 3 and v["meta"]["schema_version"] == "3"
    assert db_schema.safe_numbers(v["meta"]) == {2}
    with pytest.raises(db_contract.ContractRefused, match="applied already"):
        db_contract.contract(s, 3)
    db_contract.contract(s, 4)
    assert len(db_contract.copies(s)) == 2


def test_a_contract_that_refuses_changes_nothing_and_writes_go_on(tmp_path, monkeypatch) -> None:
    s = seam(tmp_path, monkeypatch, body='{"a": 1}')
    was = dump(tmp_path / "goh.db")
    with pytest.raises(db_contract.ContractRefused, match="number 3 refuses: CHECK"):
        db_contract.contract(s, 3)
    assert dump(tmp_path / "goh.db") == was and db_contract.copies(s) == []
    with db_core.transaction(s) as con:
        con.execute("UPDATE t SET body = '{}'")
    db_contract.contract(s, 3)


def test_the_copy_is_taken_under_the_write_lock(tmp_path, monkeypatch) -> None:
    """A copy taken before the lock would miss a write that lands between."""
    s = seam(tmp_path, monkeypatch)
    monkeypatch.setenv(s.busy_env, "50")
    real = db_contract.copy

    def with_a_writer(*a):
        for _ in range(2):
            with pytest.raises(sqlite3.OperationalError, match="locked"), db_core.transaction(s):
                pass
        return real(*a)

    monkeypatch.setattr(db_contract, "copy", with_a_writer)
    db_contract.contract(s, 3)


def test_the_newest_three_copies_stay(tmp_path, monkeypatch) -> None:
    s = seam(tmp_path, monkeypatch)
    for i in range(4):
        (tmp_path / f"goh-before-000{i}-{i:018d}.db").touch()
    to = db_contract.contract(s, 3)
    got = db_contract.copies(s)
    assert [p.name[:15] for p in got] == ["goh-before-0002", "goh-before-0003", "goh-before-0003"]
    assert got[-1] == to


def test_only_the_code_of_record_contracts_the_shared_file(tmp_path, monkeypatch) -> None:
    s = seam(tmp_path, monkeypatch)
    monkeypatch.setattr(db_core, "shared", lambda *_a: True)
    monkeypatch.setattr(db_core, "of_record", lambda *_a: False)
    with pytest.raises(db_core.NotOfRecord, match="code of record"):
        db_contract.contract(s, 3)
    assert db_contract.copies(s) == []


def test_a_newer_file_and_a_missing_one(tmp_path, monkeypatch) -> None:
    s = seam(tmp_path, monkeypatch)
    old = dataclasses.replace(s, schema=tmp_path / "old")
    old.schema.mkdir()
    for n in (1, 3):
        (old.schema / f"{n:04d}_m.sql").write_text(SQL[n])
    db_contract.contract(s, 3)
    db_contract.contract(s, 4)
    with pytest.raises(db_core.NewerFile):
        db_contract.contract(old, 3)
    monkeypatch.setenv("GOH_DB", str(tmp_path / "none.db"))
    with pytest.raises(db_contract.ContractRefused, match=r"no goh\.db"):
        db_contract.contract(s, 3)
    assert not (tmp_path / "none.db").exists()


KILLED = """
import dataclasses, os, signal, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import db_contract, db_core, db_schema, goh_db
at, die = sys.argv[3], lambda: os.kill(os.getpid(), signal.SIGKILL)
if at == "copy":
    db_contract.os.fsync = lambda fd: die()
elif at == "commit":
    rows = db_schema.stamp_rows
    db_schema.stamp_rows = lambda *a: (rows(*a), die())
else:
    split = db_schema.statements
    def until(sql):
        for i, s in enumerate(split(sql)):
            if i == int(at): die()
            yield s
    db_schema.statements = until
db_contract.contract(dataclasses.replace(goh_db.SEAM, schema=Path(sys.argv[2])), 3)
"""


@pytest.mark.parametrize("at", ["copy", "0", "3", "commit"])
def test_a_kill_leaves_the_file_and_the_rerun_completes(tmp_path, monkeypatch, at) -> None:
    s = seam(tmp_path, monkeypatch)
    was = dump(tmp_path / "goh.db")
    got = subprocess.run([sys.executable, "-c", KILLED, str(REPO_ROOT / "lib"), str(s.schema), at],
                         capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
                         env=hermetic_env(GOH_DB=os.environ["GOH_DB"]),
                         timeout=patience(60))  # fmt: skip
    assert got.returncode == -signal.SIGKILL, got.stderr
    assert dump(tmp_path / "goh.db") == was
    to = db_contract.contract(s, 3)
    assert dump(to) == was and not list(tmp_path.glob("*.tmp*"))
    with db_core.ro(s) as con:
        assert db_core.version(con)["user_version"] == 3
