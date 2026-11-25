"""GOH.SPAWNKEY: a spawn's and an outcome's fields as columns and child rows (lib/spawn_columns.py),
written as 0013's triggers wrote an older copy's, read back as the document was; and the row its
table refuses (hook_ledgers.DEAD)."""

from __future__ import annotations

import copy
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

import _body_fixture as bf
import goh_db
import hook_ledgers as hl
import pytest
import spawn_columns
import state_columns
from _columns_fixture import AT, OUTCOME, SPAWN
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "hooks" / "claude"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import agent_audit
import model_choice
import usage_review


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("GOH_DB", str(tmp_path / "state" / "goh.db"))
    return goh_db.path()


def q(sql: str, *args: object) -> list[tuple]:
    with goh_db.ro() as con:
        return con.execute(sql, args).fetchall()


def old(con: sqlite3.Connection, row: dict) -> None:
    """As code of schema 10 writes it: the document alone, its columns the trigger's."""
    con.execute("INSERT INTO agent_spawns(at, event, agent, body) VALUES (?, ?, ?, ?)",
                (spawn_columns.instant(row.get("time")) or 0.0, row.get("event") or "",
                 row["agent_id"] if isinstance(row.get("agent_id"), str) else None,
                 json.dumps(row, sort_keys=True)))  # fmt: skip


def new(row: dict) -> None:
    assert hl.append(hl.SPAWNS, row) is None


def held(con: sqlite3.Connection, n: int = 1) -> dict:
    """Row N with its child rows, without its key."""
    cur = con.execute("SELECT * FROM agent_spawns WHERE n = ?", (n,))
    row = dict(zip([c[0] for c in cur.description], cur.fetchone(), strict=True))
    row["rest"] = json.loads(row["rest"])
    for t in ("spawn_lists", "spawn_app_exits"):
        row[t] = con.execute(f"SELECT * FROM {t} WHERE n = ? ORDER BY 2, 3", (n,)).fetchall()
    return {k: v for k, v in row.items() if k not in ("n", "item", "body")}


def found() -> list[str]:
    with goh_db.ro() as con:
        return state_columns.check(con)


BARE = {"event": "spawn", "time": SPAWN["time"], "items": ["A.B"]}
ODD = [
    {"items": ["A.B", 2]}, {"items": "A.B"}, {"items": []}, {"effort": 9}, {"effort": None},
    {"items": None, "no_item_why": "w"}, {"exempt": 1}, {"exempt": "no"}, {"cores": 1.5},
    {"cores": True}, {"tokens": {"total": "9", "input": 2}}, {"tokens": 7}, {"tokens": {}},
    {"meter": {"7d": 95, "5h": None, "why": "w"}}, {"meter": []}, {"meter": {"7d": True}},
    {"pool": {"said": "tried", "more": {"a": [1]}}}, {"load": [1.0, 2]}, {"load": [1, 2, 3.5]},
    {"load": [1, 2, "3"]}, {"load": None}, {"apps": None}, {"apps": ["a", 1]},
    {"apps": {"before": ["a"], "missing": "b", "exits": {}}},
    {"apps": {"exits": {"a": {"gone_by": 9}}}},
    {"apps": {"exits": {"a": {"gone_by": "g", "pid": 4}}}},
    {"apps": {"exits": {"a": {"gone_by": "g", "by": "x"}}}},
    {"apps": {"exits": {"a": {}, "b": {"last_seen": None}}}}, {"agent_id": 7},
    {"time": "2026-11-18T06:06:40-05:00"}, {"time": "soon"}, {"report": "caf\u00e9 \u2192"},
    {"nobody": {"a": [1]}}, {"nobody": 0},
]  # fmt: skip
SHAPES = [SPAWN, OUTCOME, BARE, *({**BARE, **odd} for odd in ODD)]


@pytest.mark.parametrize("doc", SHAPES, ids=[str(n) for n in range(len(SHAPES))])
def test_the_writer_and_an_older_copys_trigger_leave_the_same_row(doc) -> None:
    """Columns, child rows and `rest`; and it is read back as the document was. A value that fits no
    column the writer refuses."""
    ours, theirs = bf.memory(), bf.memory(bf.UNCUT)
    old(theirs, copy.deepcopy(doc))
    want = held(theirs)
    try:
        spawn_columns.write(ours, copy.deepcopy(doc))
    except spawn_columns.Refused:
        assert want["rest"] not in ({}, None)
        return
    assert held(ours) == want
    (got,) = spawn_columns.read(ours)
    assert state_columns.differs("agent_spawns", json.dumps(doc), json.dumps(got)) == []


def test_every_field_of_a_new_row_is_in_a_column(db) -> None:
    """B of the data plan: `rest` empty, every column of its kind filled, the document equal."""
    for row in (SPAWN, OUTCOME):
        new(row)
    with goh_db.ro() as con:
        filled = {k for n in (1, 2) for k, v in held(con, n).items() if v is not None}
        assert filled >= set(state_columns.columns(con, "agent_spawns")) - {"n", "item"}
    assert q("SELECT rest FROM agent_spawns") == [("{}",), ("{}",)] and found() == []
    assert q("SELECT kind, count(*) FROM spawn_lists GROUP BY 1 HAVING kind = 'item'") == [
        ("item", 2)
    ]


def test_two_items_share_a_spawns_tokens_and_none_takes_no_row(db) -> None:
    new(SPAWN)
    new(OUTCOME)
    new({**SPAWN, "tool_use_id": "U", "items": None, "no_item_why": "a probe"})
    new({**OUTCOME, "tool_use_id": "U"})
    assert q("SELECT no_item_why FROM agent_spawns WHERE event = 'spawn'") == [
        ("n",), ("a probe",)]  # fmt: skip
    assert sorted(q("SELECT item, tokens FROM spend_items_v1")) == [("A.B", 5.0), ("C.D", 5.0)]


@pytest.mark.parametrize(
    ("row", "items"),
    [
        ({"description": "GOH.X: do"}, ["GOH.X"]),  # an older copy read it off the description
        ({"item": "GOH.Y", "description": "GOH.X: do"}, ["GOH.Y"]),
        ({"description": "do GOH.X"}, None),
        ({"description": "GOH.X: do", "items": None}, None),  # this copy said: none
        ({"item": 9}, None),
    ],
)
def test_an_old_spill_is_drained_with_its_item_as_a_row(db, row, items) -> None:
    with goh_db.transaction():
        pass
    late = {"event": "spawn", "time": SPAWN["time"], "tool_use_id": "T", **row}
    hl._spill(hl.SPAWNS, late)
    assert [r.get("items") for r in hl.spawns()] == [items]  # read before it is drained
    new(OUTCOME)
    assert not hl.spill_file().exists() and hl.busy()["count"] == 1
    assert [v for (v,) in q("SELECT value FROM spawn_lists WHERE n = 1 AND kind = 'item'")] == (
        items or []
    )
    assert [x for x in found() if "give back" in x] == []
    assert hl.spawns()[0].get("items") == items


def test_a_wrong_type_is_dead_lettered_and_holds_no_other_row_back(db) -> None:
    wrong = {"event": "spawn", "time": SPAWN["time"], "items": None, "effort": 9}
    hl._spill(hl.SPAWNS, wrong)
    hl._spill(hl.SPAWNS, SPAWN)
    said = hl.append(hl.SPAWNS, {"event": "outcome", "time": 9, "tool_use_id": "V"})
    assert said and "effort: not a type its column takes" in said and "time" in said
    assert [(r["row"].get("effort"), r["why"].split(":")[0]) for r in hl.dead()] == [
        (9, "effort"), (None, "time")]  # fmt: skip
    assert not hl.spill_file().exists() and [r["event"] for r in hl.spawns()] == ["spawn"]
    new(OUTCOME)
    assert len(hl.spawns()) == 2 and hl.busy()["count"] == 2


def test_a_row_the_table_refuses_is_dead_lettered_whole_and_named(db, capsys) -> None:
    """`event` has a column and no `rest`: a value that is no text is refused by 0013's CHECK. The
    row is not tried again (it would hold every later one), and the check names it."""
    new(SPAWN)
    hl._spill(hl.SPAWNS, {"event": 7, "items": ["GOH.LOST"]})
    new_said = hl.append(hl.SPAWNS, OUTCOME)
    assert new_said and hl.DEAD in new_said
    assert [r["event"] for r in hl.spawns()] == ["spawn", "outcome"]
    assert q("SELECT count(*) FROM spawn_lists WHERE value = 'GOH.LOST'") == [(0,)]
    (line,) = found()
    assert line.startswith(f"agent_spawns: in {hl.DEAD}: ") and "GOH.LOST" in line
    assert state_columns.main() == 1 and "GOH.LOST" in capsys.readouterr().err


@pytest.mark.parametrize("odd", [{"meter": {"7d": float("inf")}}, {"load": [1, float("nan"), 2]}])
def test_a_number_that_is_no_json_is_dead_lettered(db, odd) -> None:
    assert hl.DEAD in (hl.append(hl.SPAWNS, {**BARE, **odd}) or "")
    assert hl.spawns() == [] and len(hl.dead()) == 1


def test_a_refused_child_row_takes_its_parent_with_it(db) -> None:
    with goh_db.transaction() as con:
        con.execute("CREATE TRIGGER no BEFORE INSERT ON spawn_lists WHEN NEW.value = 'GOH.LOST' "
                    "BEGIN SELECT RAISE(ABORT, 'no'); END")  # fmt: skip
    hl._spill(hl.SPAWNS, {**BARE, "items": ["A.B", "GOH.LOST"]})
    assert "no" in (hl.append(hl.SPAWNS, SPAWN) or "")
    assert len(hl.spawns()) == len(hl.dead()) == 1 and q("SELECT DISTINCT n FROM spawn_lists") == [
        (1,)
    ]


def test_a_dead_letter_waits_for_the_commit(db, monkeypatch) -> None:
    """A write goh.db does not take puts the spill back whole: nothing is buried twice."""
    new(SPAWN)
    hl._spill(hl.SPAWNS, {"event": 7})
    real = hl._kv_put

    def busy(*a: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(hl, "_kv_put", busy)
    assert "spilled" in (hl.append(hl.SPAWNS, OUTCOME) or "")
    assert hl.dead() == [] and len(hl._spilled(hl.spill_file())) == 2
    monkeypatch.setattr(hl, "_kv_put", real)
    hl.append(hl.SPAWNS, OUTCOME)
    assert len(hl.dead()) == 1 and len(hl.spawns()) == 3


def test_a_row_with_an_unknown_key_is_read_whole(db) -> None:
    docs = (SPAWN, OUTCOME, {**BARE, "nobody": 1})
    for doc in docs:
        new(doc)
    for doc, again in zip(docs, hl.spawns(db), strict=True):
        assert state_columns.differs("agent_spawns", json.dumps(doc), json.dumps(again)) == []


def test_text_no_column_holds_is_replaced(db) -> None:
    """A transcript's lone surrogate is no UTF-8: refused, it would cost the whole outcome."""
    new({**OUTCOME, "report": "cut \ud83d", "faults": ["\ud83d"]})
    assert q("SELECT report, rest FROM agent_spawns") == [("cut ?", "{}")] and found() == []


def test_a_time_no_clock_has_is_dead_lettered(db) -> None:
    for at in (1e20, float(1 << 80), -5):
        assert "time: not a type" in (hl.append(hl.SPAWNS, {**BARE, "time": at}) or "")
    new(BARE)
    assert q("SELECT at FROM agent_spawns") == [(AT,)]
    assert [r["row"]["time"] for r in hl.dead()] == [1e20, float(1 << 80), -5]


def test_the_reports_over_the_columns_equal_the_reports_over_the_documents(db) -> None:
    """Byte for byte: a REAL column's 95.0 is the meter's 95, an absent key a null one."""
    rows = []
    for i in range(24):
        at = f"2026-11-18T{i:02d}:00:00+00:00"
        cls = ("sonnet", "high") if i % 2 else ("opus", "high")
        rows.append({**SPAWN, "time": at, "tool_use_id": f"t{i}", "model": cls[0],
                     "effort": cls[1],
                     "declared": "@".join(cls), "tags": ["escalation"] if i % 5 == 0 else [],
                     "description": f"d{i % 3}", "route": None, "explored": False,
                     "pool": {"said": "unfit", "why": "w"} if i % 3 else None,
                     "meter": {"5h": None, "7d": 95 - i, "age_h": 0.5, "ok": True}})  # fmt: skip
        rows.append({**OUTCOME, "time": at, "tool_use_id": f"t{i}", "load": [i + 1, 2.0, 3],
                     "status": ("landed", "blocked", "unreported")[i % 3], "apps": None,
                     "faults": [f"f{i}"] * (i % 2), "scratch_loose": [],
                     "report": None if i % 4 else "sha abc1234"})  # fmt: skip
    for row in rows:
        new(row)
    rules = model_choice.load()
    now = dt.datetime(2026, 11, 19, tzinfo=dt.UTC)

    def said(records: list[dict]) -> str:
        review = usage_review.review(records, rules.data, rules.path, now, None)
        audit = agent_audit.audit(records, rules, 3)
        faults = agent_audit.fault_rows(records, None)
        return json.dumps([review, usage_review.render(review), audit, faults], default=str)

    assert said(hl.spawns()) == said(rows)
    assert '"min_7d": 72}' in said(hl.spawns())


def test_the_weekly_meters_minimum_is_a_whole_percent_either_way() -> None:
    rows = [{"event": "spawn", "meter": {"7d": v}} for v in (95.0, 12.0, 40)]
    assert json.dumps(usage_review.meter_stats(rows, None, ["7d"])["min_7d"]) == "12"
    rows[1]["meter"]["7d"] = 12.5
    assert usage_review.meter_stats(rows, None, ["7d"])["min_7d"] == 12.5
