"""GOH.OLDWRITERS: a copy of goh that does not ship a schema number goes on writing exactly when
every number it does not ship is in meta.old_writers_safe (lib/db_core.py `newer`, a set check);
its INSERTs name their columns; a by-command number is never applied by an open."""

from __future__ import annotations

import ast
import dataclasses
import re
import sqlite3
import sys
from pathlib import Path

import db_bounds
import db_contract
import db_core
import db_schema
import goh_db
import pytest
import ship_records
from conftest import REPO_ROOT

TREE_READS = ("lib/", "tools/", "hooks/", "gates/", "checks/")  # every INSERT in them
SAFE, REFUSE = "safe", "refuse"


def seam(tmp_path: Path, monkeypatch, heads: dict[int, str | None], name: str) -> db_core.Seam:
    """A seam shipping numbers HEADS ({n: line 1's word, None for no head}) over tmp's goh.db."""
    d = tmp_path / name
    d.mkdir()
    for n, head in heads.items():
        line = f"-- old-writers: {head}\n" if head else ""
        (d / f"{n:04d}_m.sql").write_text(
            f"{line}-- bound: t{n} rows=100 stamp=at\nCREATE TABLE t{n}(x INTEGER, at REAL);\n"
        )
    monkeypatch.setenv("GOH_DB", str(tmp_path / "goh.db"))
    return dataclasses.replace(goh_db.SEAM, schema=d)


BASE: dict[int, str | None] = {1: None, 2: None}
SEQUENCES = [  # the plan's six, over code that ships 1 and 2: (what newer code applied, may write)
    ({3: SAFE}, True),
    ({3: SAFE, 4: REFUSE}, False),
    ({3: SAFE, 4: REFUSE, 5: SAFE}, False),
    ({3: SAFE, 4: SAFE, 5: SAFE}, True),
    ({3: SAFE, 5: SAFE}, True),  # a gap: 4 is not there yet
    ({3: REFUSE, 4: SAFE}, False),
]


@pytest.mark.parametrize(("ahead", "may"), SEQUENCES, ids=lambda v: str(v).replace(" ", ""))
def test_the_six_sequences(tmp_path, monkeypatch, ahead, may) -> None:
    """An older copy's write: lands through safe numbers alone, refused behind any other, wherever
    it stands; the newer code always writes; the older never moves the version down."""
    new = seam(tmp_path, monkeypatch, {**BASE, **ahead}, "new")
    old = seam(tmp_path, monkeypatch, BASE, "old")
    with db_core.transaction(new) as con:
        con.execute("INSERT INTO t1(x, at) VALUES (1, 0)")
    with db_core.ro(new) as con:
        was = db_core.version(con)
    if may:
        with db_core.transaction(old) as con:
            con.execute("INSERT INTO t1(x, at) VALUES (2, 0)")
    else:
        with pytest.raises(db_core.NewerFile), db_core.transaction(old):
            pass
    with db_core.transaction(new) as con:
        con.execute("INSERT INTO t2(x, at) VALUES (3, 0)")
    with db_core.ro(new) as con:
        now = db_core.version(con)
        assert [x for (x,) in con.execute("SELECT x FROM t1")] == ([1, 2] if may else [1])
    assert now == was
    safe = {n for n, h in ahead.items() if h == SAFE}
    assert db_schema.safe_numbers(now["meta"]) == safe


@pytest.mark.parametrize("ahead", [{3: REFUSE}, {3: SAFE, 4: REFUSE}], ids=str)
def test_code_of_the_middle_is_refused_behind_a_refuse(tmp_path, monkeypatch, ahead) -> None:
    """Code that ships the safe 3 is refused by 4 all the same."""
    new = seam(tmp_path, monkeypatch, {**BASE, **ahead}, "new")
    mid = seam(tmp_path, monkeypatch, {k: v for k, v in {**BASE, **ahead}.items()
                                       if v != REFUSE}, "mid")  # fmt: skip
    with db_core.connect(new):
        pass
    with pytest.raises(db_core.NewerFile, match="holds migration"), db_core.transaction(mid):
        pass


def test_old_code_still_applies_its_own_number_past_a_safe_gap(tmp_path, monkeypatch) -> None:
    """5 (safe) is in; code shipping 3 and not 5 migrates 3, and 5 stays."""
    with db_core.connect(seam(tmp_path, monkeypatch, {**BASE, 5: SAFE}, "new")):
        pass
    with db_core.connect(seam(tmp_path, monkeypatch, {**BASE, 3: SAFE}, "old")) as con:
        got = db_core.version(con)
    assert got["applied"] == [1, 2, 3, 5] and got["user_version"] == 3
    assert db_schema.safe_numbers(got["meta"]) == {3, 5}


@pytest.mark.parametrize("value", ["", "nonsense", "{}", '["3"]', "[true]"])
def test_a_mangled_safe_list_is_no_permission(tmp_path, monkeypatch, value) -> None:
    new = seam(tmp_path, monkeypatch, {**BASE, 3: SAFE}, "new")
    with db_core.transaction(new) as con:
        con.execute("UPDATE meta SET value=? WHERE key=?", (value, db_schema.SAFE_KEY))
    old = seam(tmp_path, monkeypatch, BASE, "old")
    with pytest.raises(db_core.NewerFile), db_core.transaction(old):
        pass


def test_a_number_that_only_says_safe_is_recorded_as_refuse(tmp_path, monkeypatch, capsys) -> None:
    """The file is applied (an open never wedges) and older code is shut out, as for a refuse."""
    with db_core.connect(seam(tmp_path, monkeypatch, BASE, "base")):
        pass
    new = seam(tmp_path, monkeypatch, {**BASE, 3: SAFE}, "new")
    (new.schema / "0003_m.sql").write_text("-- old-writers: safe\nDROP TABLE t2;\n")
    with db_core.connect(new) as con:
        got = db_core.version(con)
    assert got["applied"] == [1, 2, 3] and db_schema.SAFE_KEY not in got["meta"]
    assert "recorded as refuse" in capsys.readouterr().err


def test_the_shipped_safe_numbers_are_the_columns_files(tmp_path, monkeypatch) -> None:
    """GOH.LANDCOLS, GOH.KVBLOBS: 0011 to 0016, and no number before them; GOH.PINSWEEP: 0022;
    GOH.ONEGATE: 0023; GOH.TIMEACCOUNT: 0024; GOH.TESTWORK: 0026."""
    monkeypatch.setenv("GOH_DB", str(tmp_path / "goh.db"))
    with goh_db.connect() as con:
        meta = db_core.version(con)["meta"]
    assert db_schema.safe_numbers(meta) == {11, 12, 13, 14, 15, 16, 22, 23, 24, 26}


def test_an_older_copy_neither_sweeps_nor_judges_a_safe_files_tables(tmp_path, monkeypatch) -> None:
    """Writable is not sweepable: the older code does not know which child rows leave with a
    parent's, nor the bounds of tables it did not make."""
    new = seam(tmp_path, monkeypatch, {**BASE, 3: SAFE}, "new")
    old = seam(tmp_path, monkeypatch, BASE, "old")
    with db_core.transaction(new) as con:
        con.executemany("INSERT INTO t1(x, at) VALUES (?, ?)", [(i, i) for i in range(150)])
    assert not any("t3" in line for line in db_bounds.unbounded(old))
    assert db_bounds.sweep(old, 1000.0) == {}
    assert db_bounds.sweep(new, 1000.0) == {"t1": 50}


# -- a by-command number ---------------------------------------------------------------------------


def test_an_open_never_applies_a_contract(tmp_path, monkeypatch) -> None:
    """Nor does its absence make a lane's copy read-only."""
    with db_core.connect(seam(tmp_path, monkeypatch, BASE, "old")):
        pass
    new = seam(tmp_path, monkeypatch, {**BASE, 3: REFUSE, 4: SAFE}, "new")
    f = new.schema / "0003_m.sql"
    f.write_text(f.read_text().replace("refuse\n", f"refuse\n{db_schema.BY_COMMAND}\n"))
    with db_core.connect(new) as con:
        got = db_core.version(con)
        assert db_core.not_of_record(con, new) is None
    assert got["applied"] == [1, 2, 4] and got["user_version"] == 2
    db_contract.contract(new, 3)
    with db_core.connect(new) as con:
        assert db_core.version(con)["user_version"] == 4


# -- SQLite's own version --------------------------------------------------------------------------


def test_an_old_sqlite_is_refused_by_name(tmp_path, monkeypatch) -> None:
    s = seam(tmp_path, monkeypatch, BASE, "new")
    with db_core.connect(s):
        pass
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 43, 2))
    for opened in (db_core.connect, db_core.ro, db_core.transaction):
        with pytest.raises(db_core.OldSQLite, match=r"3\.44\.0 or newer"), opened(s):
            pass
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 44, 0))
    with db_core.connect(s):
        pass


# -- named INSERTs ---------------------------------------------------------------------------------

POSITIONAL = re.compile(r'\bINTO\s+[\w"]+\s+VALUES\b', re.IGNORECASE)


def positional(source: str) -> list[int]:
    """The lines of SOURCE's INSERTs that name no column (adjacent literals are one string, a
    formatted field one word)."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(v.value if isinstance(v, ast.Constant) else "x" for v in node.values)
        else:
            continue
        if POSITIONAL.search(text):
            out.append(node.lineno)
    return out


def test_every_insert_names_its_columns() -> None:
    """A positional INSERT fails on the first column a later number adds, so the older copy a safe
    number means to keep writing could not (P2: `ships`, 11 values for 70 columns)."""
    planted = 'x = "INSERT OR REPLACE INTO ships " \\\n    "VALUES (?)"\n'
    planted += 'y = f"INSERT INTO {t} VALUES (?)"'
    assert positional(planted) == [1, 3]
    assert positional('"INSERT INTO t(a) VALUES (?)"') == []
    found = [f"{p.relative_to(REPO_ROOT)}:{n}" for d in ("lib", "tools", "hooks", "gates", "checks")
             for p in sorted((REPO_ROOT / d).rglob("*.py"))
             for n in positional(p.read_text(encoding="utf-8"))]  # fmt: skip
    assert found == [], found


def test_the_ship_writers_write_a_widened_table(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GOH_DB", str(tmp_path / "goh.db"))
    with goh_db.transaction() as con:
        for t in ("ships", "ship_pins", "kv", "imports"):
            con.execute(f"ALTER TABLE {t} ADD COLUMN later TEXT")
    ship_records.put({"result": "/s/r/1.json", "status": "done"})
    ship_records.put_pins("/s/r/1.json", {"repos": {}})
    got = ship_records.get("/s/r/1.json")
    assert got and got["status"] == "done" and ship_records.get_pins("/s/r/1.json") == {"repos": {}}


def test_the_heads() -> None:
    assert db_schema.old_writers("-- old-writers: safe\nX") == "safe"
    for text in ("", "-- a\n-- old-writers: safe\n", "-- old-writers: maybe\n"):
        assert db_schema.old_writers(text) is None
    assert db_schema.by_command(f"-- old-writers: refuse\n{db_schema.BY_COMMAND}\nX;")
    assert not db_schema.by_command(f"X;\n{db_schema.BY_COMMAND}\n")
    assert db_schema.withs("-- with: a_b of a\n-- with: c of a_b\n") == [("a_b", "a"), ("c", "a_b")]
    with pytest.raises(ValueError, match="the form is"):
        db_schema.withs("-- with: a,b of c\n")


def test_the_state_command_names_contract_and_olds(capsys) -> None:
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    import state_retire

    with pytest.raises(SystemExit):
        state_retire.main(["olds"])
    assert "--since" in capsys.readouterr().err
    assert state_retire.main(["olds", "--since", "no-such-commit"]) == 2
    assert state_retire.main(["contract", "9999"]) == 1
