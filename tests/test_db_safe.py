"""lib/db_safe.py (GOH.OLDWRITERS, L11): what a schema file that says `-- old-writers: safe` may
hold. Each forbidden statement is planted and named; the data plan's 0011 and 0012 pass as they
are (tests/fixtures/goh_db_plan, not shipped); a view's columns are compared in order."""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import db_safe
import goh_cache_db
import goh_db
import pytest

PLAN = Path(__file__).parent / "fixtures" / "goh_db_plan"
BASE = """CREATE TABLE p(q TEXT, r TEXT, st TEXT, body TEXT NOT NULL, at REAL, PRIMARY KEY (q, r));
CREATE TABLE other(n INTEGER PRIMARY KEY, x TEXT);
CREATE VIEW p_v1 AS SELECT q, r, st, at FROM p;
"""
KIDS = """CREATE TABLE p_kids(q TEXT, r TEXT, ord INTEGER, value TEXT, PRIMARY KEY (q, r, ord));
ALTER TABLE p ADD COLUMN c TEXT CHECK (typeof(c) IN ('text', 'null'));
ALTER TABLE p ADD COLUMN rest TEXT;
"""


def trigger(body: str, when: str = "INSERT") -> str:
    return f"{KIDS}CREATE TRIGGER t AFTER {when} ON p WHEN 1 BEGIN\n{body};\nEND;\n"


def judged(tmp_path: Path, sql: str, head: str = "safe") -> list[str]:
    d = tmp_path / "schema"
    d.mkdir(exist_ok=True)
    (d / "0001_base.sql").write_text(BASE)
    (d / "0011_m.sql").write_text(f"-- old-writers: {head}\n{sql}")
    return db_safe.check(dataclasses.replace(goh_db.SEAM, schema=d))


OWN = "WHERE q = NEW.q AND r = NEW.r"
GOOD = {
    "tables": KIDS,
    "index": "CREATE INDEX p_at ON p(at);",
    "unique index of its own": "CREATE TABLE a(x);\nCREATE UNIQUE INDEX a_x ON a(x);",
    "same view": "DROP VIEW p_v1;\nCREATE VIEW p_v1 AS SELECT q, r, upper(st) AS st, at FROM p;",
    "fill": trigger(
        f"UPDATE p SET c = NEW.body ->> '$.c', rest = 'x;END' {OWN} AND (rest IS NULL OR 1);\n"
        f"DELETE FROM p_kids {OWN};\n"
        "INSERT INTO p_kids SELECT NEW.q, NEW.r, e.key, e.value FROM json_each(NEW.body) e"
    ),
    "refill": trigger(
        f"DELETE FROM p {OWN};\nINSERT INTO p(q, r, body, at) VALUES (NEW.q, NEW.r, NEW.body, 1)",
        "UPDATE OF body",
    ),
    "gone": trigger("DELETE FROM p_kids WHERE q = OLD.q AND r = OLD.r", "DELETE"),
}
BAD = {  # a planted statement: what the gate says of it
    "drop table": ("DROP TABLE other;", "'DROP TABLE other' is not additive"),
    "drop column": ("ALTER TABLE p DROP COLUMN st;", "not additive"),
    "rename": ("ALTER TABLE p RENAME COLUMN at TO t;", "not additive"),
    "rename table": ("ALTER TABLE other RENAME TO o2;", "not additive"),
    "drop trigger": (trigger("DELETE FROM p_kids WHERE q = OLD.q", "DELETE")
                     + "DROP TRIGGER t;", "not additive"),
    "drop index": ("CREATE INDEX i ON p(at);\nDROP INDEX i;", "not additive"),
    "an update": ("UPDATE p SET st = 'x';", "not additive"),
    "an insert": ("INSERT INTO other(x) VALUES ('a');", "not additive"),
    "a delete": ("DELETE FROM other;", "not additive"),
    "a hidden drop": ("/* CREATE TABLE z(x) */ DROP TABLE other;", "not additive"),
    "view dropped": ("DROP VIEW p_v1;", "never created again"),
    "view created first": ("CREATE VIEW z AS SELECT 1 AS a;\nDROP VIEW z;", "never created again"),
    "view reordered": ("DROP VIEW p_v1;\nCREATE VIEW p_v1 AS SELECT r, q, st, at FROM p;",
                       "had the columns ['q', 'r', 'st', 'at'] and has ['r', 'q', 'st', 'at']"),
    "view narrowed": ("DROP VIEW p_v1;\nCREATE VIEW p_v1 AS SELECT q, r, st FROM p;", "had the"),
    "view widened": ("DROP VIEW p_v1;\nCREATE VIEW p_v1 AS SELECT *, 1 AS z FROM p;", "had the"),
    "view renamed column": ("DROP VIEW p_v1;\nCREATE VIEW p_v1 AS SELECT q, r, st, at AS t FROM p;",
                            "had the columns"),
    "unique index": ("CREATE UNIQUE INDEX u ON p(st);", "a UNIQUE index on p"),
    "check on an old column": ("ALTER TABLE p ADD COLUMN c TEXT CHECK (c IS NULL OR st <> '');",
                               "reads st"),
    "refuses its default": ("ALTER TABLE p ADD COLUMN c TEXT CHECK (c IS NOT NULL);",
                            "refuses a row that leaves it out"),
    "references": ("ALTER TABLE p ADD COLUMN c INTEGER REFERENCES other(n);", "REFERENCES"),
    "generated": ("ALTER TABLE p ADD COLUMN c TEXT GENERATED ALWAYS AS (json(body));", "GENERATED"),
    "table there already": ("CREATE TABLE IF NOT EXISTS other(n);", "there already"),
    "before trigger": (trigger("SELECT 1").replace(" AFTER ", " BEFORE "), "is no AFTER"),
    "update of another column": (trigger(f"UPDATE p SET c = 1 {OWN}", "UPDATE OF st"),
                                 "is no AFTER"),
    "any update": (trigger(f"UPDATE p SET c = 1 {OWN}", "UPDATE"), "is no AFTER"),
    "raise": (trigger("SELECT RAISE(ABORT, 'no')"), "it runs"),
    "half the key": (trigger("UPDATE p SET c = 1 WHERE q = NEW.q"), "not keyed to its own"),
    "no key": (trigger("DELETE FROM p"), "not keyed to its own"),
    "key behind an or": (trigger(f"UPDATE p SET c = 1 {OWN} OR 1"), "not keyed to its own"),
    "key in a string": (trigger("UPDATE p SET c = ' WHERE q = NEW.q AND r = NEW.r '"),
                        "not keyed to its own"),
    "key in a case": (trigger("UPDATE p SET c = 1 WHERE CASE WHEN 1 AND q = NEW.q AND r = NEW.r AND"
                              " 1 THEN 0 ELSE 1 END"), "not keyed to its own"),
    "crossed key": (trigger("UPDATE p SET c = 1 WHERE q = NEW.r AND r = NEW.q"),
                    "not keyed to its"),
    "an old column": (trigger(f"UPDATE p SET st = 'x' {OWN}"), "it sets st"),
    "another row's insert": (trigger("INSERT INTO p(q, r, body) VALUES (NEW.q, 'r', '')"),
                             "not its own"),
    "insert by position": (trigger("INSERT INTO p VALUES (NEW.q, NEW.r, '', '', 1, 1, 1)"),
                           "not its own"),
    "another table": (trigger("DELETE FROM other WHERE n = NEW.at"), "it writes other"),
    "another table's insert": (trigger("INSERT INTO other(x) VALUES (NEW.q)"), "it writes other"),
    "all the children": (trigger("DELETE FROM p_kids"), "p_kids rows not keyed by NEW"),
    "parent on delete": (trigger("DELETE FROM p WHERE q = OLD.q AND r = OLD.r", "DELETE"),
                         "it writes p"),
    "insert on delete": (trigger("INSERT INTO p_kids(q) VALUES (OLD.q)", "DELETE"),
                         "it writes p_kids"),
    "with no key": ("-- with: kid of p\nCREATE TABLE kid(q TEXT);", "does not carry p's key"),
    "with no table": ("-- with: kid of p\nCREATE TABLE k2(q TEXT);", "does not carry p's key"),
    "does not apply": ("ALTER TABLE gone ADD COLUMN c TEXT;", "does not apply"),
}  # fmt: skip


@pytest.mark.parametrize("name", GOOD)
def test_an_additive_file_is_safe(tmp_path, name) -> None:
    assert judged(tmp_path, GOOD[name]) == []


@pytest.mark.parametrize("name", BAD)
def test_a_planted_statement_is_named(tmp_path, name) -> None:
    sql, said = BAD[name]
    got = judged(tmp_path, sql)
    assert any(said in line and line.startswith("0011_m.sql: ") for line in got), got


def test_a_select_star_view_over_a_widened_table_is_seen(tmp_path) -> None:
    """Not dropped, and its columns change all the same: only a database shows it."""
    d = tmp_path / "schema"
    d.mkdir()
    (d / "0001_base.sql").write_text(BASE + "CREATE VIEW star AS SELECT * FROM other;\n")
    (d / "0002_m.sql").write_text("-- old-writers: safe\nALTER TABLE other ADD COLUMN y TEXT;\n")
    got = db_safe.check(dataclasses.replace(goh_db.SEAM, schema=d))
    assert got == ["0002_m.sql: view star had the columns ['n', 'x'] and has ['n', 'x', 'y']"]


def test_a_refuse_file_may_drop_and_a_head_is_required(tmp_path) -> None:
    assert judged(tmp_path, "DROP TABLE other;", "refuse") == []
    assert any("line 1 is not" in x for x in judged(tmp_path, "CREATE TABLE z(x);", "perhaps"))
    by = "-- applied-by: goh state contract\nCREATE TABLE z(x);"
    assert any("never safe" in x for x in judged(tmp_path, by))
    assert judged(tmp_path, by, "refuse") == []


@pytest.mark.parametrize("s", [goh_db.SEAM, goh_cache_db.SEAM], ids=lambda s: s.name)
def test_every_shipped_file_is_what_its_head_says(s) -> None:
    assert db_safe.check(s) == []


def test_the_plans_files_are_safe_and_a_reordered_lands_v1_is_not(tmp_path) -> None:
    d = tmp_path / "schema"
    shutil.copytree(goh_db.SEAM.schema, d)
    s = dataclasses.replace(goh_db.SEAM, schema=d)
    for p in PLAN.glob("*.sql"):
        shutil.copy(p, d)
    assert db_safe.check(s) == []
    f = d / "0011_lands_columns.sql"
    sql = f.read_text()
    f.write_text(
        sql.replace("VIEW lands_v1 AS SELECT run, item,", "VIEW lands_v1 AS SELECT item, run,")
    )
    got = db_safe.check(s)
    assert len(got) == 1 and "view lands_v1 had the columns ['run', 'item'" in got[0], got
