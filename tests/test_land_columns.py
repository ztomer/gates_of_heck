"""lib/land_columns.py (GOH.LANDREADERS): a land record written and read as goh.db's typed columns
and child rows."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import _body_fixture as bf
import db_bounds_with
import diffcov_carry
import goh_db
import land_attempts
import land_check
import land_columns as lc
import land_e2e
import land_profile
import land_records
import land_reds
import land_waive
import landq_report
import landq_size
import pytest
import state_columns
import state_shape
from _columns_fixture import AT, LAND
from _contract_fixture import old_land

RUN = "01795000000000000000-A.B"
STATE = {"at": LAND["at"]}
WARNED = [{"node": "t::w", "kind": "rule-in-flight"}, {"node": "t::m", "kind": "main-red"}]
ODD = [  # (a document, what `rest` keeps of it)
    ({"slot_s": "soon", "early": 1, "k_why": ["w"], "recorded": "2026"}, None),
    ({"batch": {"id": 7, "new": 1, "gates": True}}, None),
    ({"batch": "b1", "pre_run": {"remote": "none", "wait_s": 1}}, {
        "batch": "b1", "pre_run": {"remote": "none"}}),
    ({"guard": "g", "guards": ["g", 2]}, None),
    ({"guard": "other", "guards": ["g"]}, {}),
    ({"guard": "alone"}, None),
    ({"flakes": [{"test": "t", "later": 1}], "cap_items": [{"lines": "9"}]}, None),
    ({"remote_shares": [{"tests": 1.5}], "cov_risk": {"carry": [{"own": 1}], "files": {"-": 3}}},
     None),
    ({"waived": [{"node": "n", "items": [1]}], "red_cause": {"tests": "t"}}, None),
    ({"nobody": {"a": [1]}, "at": "soon"}, None),
    ({"at": "2026-11-18T06:06:40-05:00", "flakes": [], "batch": None, "pre_run": {"remote": {}},
      "cov_risk": {"carry": []}, "waived": [{"node": "n", "items": None}]}, {}),
]  # fmt: skip


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("GOH_DB", str(tmp_path / "state" / "goh.db"))
    return goh_db.path()


def q(sql: str, *args: object) -> list[tuple]:
    with goh_db.ro() as con:
        return con.execute(sql, args).fetchall()


def run(sql: str, *args: object) -> None:
    with goh_db.transaction() as con:
        con.execute(sql, args)


def put(doc: dict, name: str = RUN, at: float = AT, keep: bool = False) -> None:
    with goh_db.transaction() as con:
        lc.put(con, "/q", name, doc, at, keep)


def get(keys: tuple[str, ...] | None = None, name: str = RUN) -> dict:
    with goh_db.ro() as con:
        return lc.read(con, "/q", keys)[name]


def rows(con: sqlite3.Connection) -> dict[str, list]:
    """Every column of `lands` but the document and what its writers spell apart, and the child
    rows."""
    cols = [c for c in state_columns.columns(con, "lands") if c not in ("body", "rest")]
    out = {"lands": con.execute(f"SELECT {', '.join(cols)} FROM lands").fetchall()}
    out["rest"] = [json.loads(r) for (r,) in con.execute("SELECT rest FROM lands")]
    for t in lc.CELLS:
        out[t] = sorted(con.execute(f"SELECT * FROM {t}"), key=repr)
    return out


def held() -> dict[str, list]:
    with goh_db.ro() as con:
        return rows(con)


def test_the_spec_is_the_schema(db) -> None:
    with goh_db.connect() as con:
        cols = {"queue", "run", "at", "rest", *lc.HAVE, *(c for _, c, _ in lc.SCALARS)}
        assert cols == set(state_columns.columns(con, "lands"))
        assert set(lc.CELLS) == set(db_bounds_with.withs(goh_db.SEAM)["lands"])
        for t, cells in lc.CELLS.items():
            assert [*lc.KEY, *cells, "at"] == state_columns.columns(con, t), t


@pytest.mark.parametrize(("doc", "rest"), [(LAND, {}), *ODD],
                         ids=["whole", *("-".join(d) for d, _ in ODD)])  # fmt: skip
def test_the_writer_leaves_what_the_fill_trigger_left(doc, rest, monkeypatch) -> None:
    """The document alone, as an older goh wrote it, and the columns by name: the same rows; what
    fits no column the cutover buried, as the writer does."""
    doc, letters = {**STATE, **doc}, []
    monkeypatch.setattr(state_shape, "bury", lambda _t, row, _why: letters.append(row))
    before, con = bf.memory(bf.UNCUT), bf.memory()
    old_land(before, "/q", RUN, doc)
    lc.put(con, "/q", RUN, doc, AT)
    filled, now = rows(before), rows(con)
    ((left,),) = before.execute("SELECT rest FROM lands").fetchall()
    kept, odd = state_shape.sift("lands", json.loads(left))
    filled["rest"] = [kept]
    assert now == filled and [{k: r[k] for k in odd} for r in letters] == [odd] * bool(odd)
    assert rest is None or now["rest"] == [rest]
    again = {k: v for k, v in lc.read(con, "/q")[RUN].items() if not odd}
    want = {k: v for k, v in doc.items() if not odd}
    assert state_columns.differs("lands", json.dumps(again), json.dumps(want)) in (
        [], ["guard"])  # fmt: skip  # `guards` alone: its first is the guard


def test_a_whole_record_is_read_back_and_each_key_of_it_alone(db) -> None:
    put(LAND)
    assert get() == LAND and not isinstance(get(), lc.Part)
    for key, v in LAND.items():
        assert get((key,)) == {key: v}, key
    assert set(LAND) == lc.KEYS


def test_a_key_that_was_not_asked_is_an_error(db) -> None:
    put({**STATE, "state": "landed"})
    part = get(("state", "why"))
    assert (part["state"], part.get("why", "-"), "why" in part) == ("landed", "-", False)
    with pytest.raises(KeyError, match=r"^'why'$"):
        part["why"]
    for ask in (lambda: part.get("sha"), lambda: part["sha"], lambda: "sha" in part):
        with pytest.raises(KeyError, match="sha: this record was read for state, why alone"):
            ask()
    with pytest.raises(KeyError, match="no such key of a land record: shaa"):
        get(("shaa",))


def test_a_list_the_second_write_does_not_have_leaves_no_row(db) -> None:
    put(LAND)
    put({**STATE, "state": "landed", "guards": ["g9"]})
    left = held()
    assert left["land_lists"] == [("/q", RUN, "guard", 0, "g9", AT)]
    assert all(left[t] == [] for t in lc.CELLS if t != "land_lists")
    assert q("SELECT why, red_class, cov_head, rest FROM lands") == [(None, None, None, "{}")]
    assert get() == {**STATE, "state": "landed", "guards": ["g9"], "guard": "g9"}
    put(LAND, keep=True)  # an import: the row is newer than its file
    assert held() == left


OTHER = {"text": 1.5, "real": "soon", "int": "soon", "bool": 2}


def test_a_column_refuses_the_wrong_type(db) -> None:
    put(LAND)
    for path, col, kind in lc.SCALARS:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            # a number becomes text in a TEXT column: bytes do not
            run(f"UPDATE lands SET {col} = ?", b"b" if kind == "text" else OTHER[kind])
        fits = json.dumps({"text": "x", "real": 1.5, "int": 1, "bool": True}[kind])
        with pytest.raises(sqlite3.IntegrityError, match="rest holds"):  # nor is it in `rest`
            run("UPDATE lands SET rest = json_set('{}', '$.' || ?, json(?))", path.split(".")[0],
                json.dumps(_under(path, json.loads(fits))))  # fmt: skip
    for table, words in ((t, w) for _, t, w in lc.OBJECTS):
        for _, col, kind in words:
            bad = b"b" if kind == "text" else OTHER[kind]
            with pytest.raises(sqlite3.IntegrityError):
                run(f"UPDATE {table} SET {col} = ?", bad)
    assert get() == LAND


def _under(path: str, v: object) -> object:
    for k in reversed(path.split(".")[1:]):
        v = {k: v}
    return v


def test_a_value_of_the_wrong_type_is_in_no_column_and_is_said(db, capsys) -> None:
    put({**STATE, "state": "landed", "slot_s": "soon", "batch": {"id": "b", "gates": "two"}})
    assert q("SELECT slot_s, batch, batch_gates, rest FROM lands") == [(None, "b", None, "{}")]
    assert f"lands {RUN}: batch, slot_s: not of its column's type" in capsys.readouterr().err
    assert get()["batch"] == {"id": "b"} and get(("slot_s",)) == {}
    with goh_db.ro() as con:
        (line,) = state_columns.check(con)
    assert "soon" in line and "two" in line


def test_what_json_or_sqlite_cannot_hold_is_no_refusal(db) -> None:
    put({**STATE, "pre_s": float("nan"), "why": "a\udc80b", "cov_risk": {"files": {3: "x"}}})
    assert get() == {**STATE, "why": "a?b", "cov_risk": {"files": {"3": "x"}}}
    assert q("SELECT rest FROM lands") == [("{}",)]


def test_a_judged_land_that_carries_no_line_says_so(db) -> None:
    """lib/diffcov_carry.py: a later judgment with an empty `carry` clears an earlier land's lines;
    one that judged nothing (no `head`) clears none."""
    put({**STATE, "cov_risk": {"said": "s", "head": "h", "carry": []}})
    put({**STATE, "cov_risk": {"said": "not judged"}}, "2-none")
    assert get()["cov_risk"] == {"said": "s", "head": "h", "carry": []}
    assert get(("cov_risk",), "2-none") == {"cov_risk": {"said": "not judged"}}


def test_ten_lands_written_are_whole_and_filled(db, tmp_path) -> None:
    """B(10): the rows the door writes, every column filled, `rest` {} and the document equal; one
    with no instant of its own takes its run's."""
    for i in range(10):
        more = {"guards": [f"g{n}" for n in range(i)], "guard": "g0"} if i else {}
        land_records.write(tmp_path, f"{i}-A.B", {**LAND, "guard": None, "guards": [], **more})
    land_records.write(tmp_path, f"{int(AT) * 10**9}-B.C", {"state": "landed"})
    with goh_db.ro() as con:
        assert state_columns.check(con) == []
        assert {r for (r,) in con.execute("SELECT rest FROM lands")} == {"{}"}
    assert land_records.get(tmp_path, f"{int(AT) * 10**9}-B.C") == {**STATE, "state": "landed"}
    assert land_records.get(tmp_path, "9-A.B")["guards"] == [f"g{n}" for n in range(9)]
    assert land_records.stamps(tmp_path)["9-A.B"] == AT
    assert land_records.runs(tmp_path)[0] == "0-A.B"


def test_a_reds_class_goes_to_its_land_once(db, tmp_path) -> None:
    red = {**STATE, "state": "red", "item": "A.B", "why": "exit 1: lander", "cause": "lander"}
    own = {"cause": "flake", "rule": "own", "tests": ["t::a"]}
    land_records.write(tmp_path, "1-A.B", red)
    land_records.write(tmp_path, "2-A.B", {**red, "red_cause": own})
    land_records.write(tmp_path, "3-A.B", red)
    other = {"cause": "real-bug", "rule": "r", "tests": ["t::b", "t::c"]}
    assert land_records.causes(tmp_path) == {"2-A.B.json": own}
    land_records.set_causes(tmp_path, {f"{n}-A.B.json": other for n in (1, 2, 9)})
    assert land_records.causes(tmp_path) == {"1-A.B.json": other, "2-A.B.json": own}
    assert q("SELECT run, red_class FROM lands ORDER BY run") == [
        ("1-A.B", "real-bug"), ("2-A.B", "flake"), ("3-A.B", None)]  # fmt: skip
    assert land_records.get(tmp_path, "1-A.B") == {**red, "red_cause": other}
    land_records.set_causes(tmp_path, {"3-A.B.json": own})
    assert land_records.get(tmp_path, "3-A.B") == {**red, "red_cause": own}


@pytest.fixture
def host(db, tmp_path, monkeypatch) -> Path:
    """A host whose state and checkouts are this test's: a path under them has a token."""
    at = tmp_path.resolve()
    monkeypatch.setenv("GOH_STATE_ROOT", str(at / "state"))
    monkeypatch.setenv("GOH_REPOS_ROOT", str(at / "src"))
    monkeypatch.delenv("GOH_LANDQ_DIR", raising=False)
    return at


def homed(host: Path) -> dict:
    return {**LAND, "repo": f"{host}/src/g/app", "log": f"{host}/state/landq/logs/x.log",
            "red_logs": [f"{host}/state/a.log", "/elsewhere/b.log"]}  # fmt: skip


def queues() -> set[str]:
    """Every queue named by a row of `lands` and its child tables."""
    return {v for t in ("lands", *lc.CELLS) for (v,) in q(f"SELECT DISTINCT queue FROM {t}")}


def test_a_file_of_tokens_takes_them_and_reads_whole(host) -> None:
    queue, doc = host / "state" / "landq", homed(host)
    land_records.write(queue, RUN, doc)
    assert queues() == {"$STATE/landq"}
    assert q("SELECT repo, log FROM lands") == [("g/app", "$STATE/landq/logs/x.log")]
    assert q("SELECT value FROM land_lists WHERE kind = 'red_log' ORDER BY ord") == [
        ("$STATE/a.log",), ("/elsewhere/b.log",)]  # fmt: skip
    with goh_db.ro() as con:
        assert state_columns.check(con) == []
    assert land_records.get(queue, RUN) == doc
    for key, v in doc.items():
        ((_, part),) = land_records.records(queue, keys=(key,))
        assert part == {key: v} and isinstance(part, lc.Part), key


SHA = "a" * 40


def _reports(queue: Path, repo: Path) -> dict:
    """What each reader of the land records says."""
    rep = landq_report.build(landq_report.read_attempts(queue, birth_of=lambda _: None))
    return {
        "report": rep,
        "reds": land_reds.report(queue),
        "guards": land_check.count(queue),
        "check": land_check.records(queue, 2),
        "profile": land_profile.lands(queue, []),
        "e2e": land_e2e.rows(queue, []),
        "carry": diffcov_carry._landed(queue, repo, {SHA: 0}),
        "waived": land_waive.warned(["A.B", "C.D"]),
        "attempt": land_attempts.attempt_of(queue, "A.B"),
        "size": landq_size.history(queue, str(repo)),
    }


def test_every_reader_reads_the_columns(host, monkeypatch) -> None:
    """Each says what the rows hold, its paths this host's."""
    monkeypatch.setattr(land_e2e, "instrumented", lambda *_: False)  # no log: a plain land
    queue, repo = host / "state" / "landq", host / "src" / "r"
    red = {**LAND, "repo": str(repo), "requested": "2026-11-18T11:00:00+00:00", "log": ""}
    landed = {**red, "state": "landed", "sha": SHA, "landed": SHA, "attempt": 3, "red_cause": None,
              "guards": [], "guard": None, "waived": WARNED,
              "finished": "2026-11-18T11:16:40+00:00"}  # fmt: skip
    land_records.write(queue, "01795000000000000000-A.B", red)
    land_records.write(queue, "01795000000000000001-A.B", landed)
    land_records.write(queue, "01795000000000000002-C.D", {**red, "item": "C.D"})
    said = _reports(queue, repo)
    assert [len(said[k]) for k in ("profile", "e2e", "carry", "check")] == [1, 1, 1, 1]
    assert said["waived"] == {"t::w"} and said["reds"]["red"] == 2
    assert said["check"][0]["guards"] == ["g1", "g2"]
    assert said["check"][0]["repo"] == said["carry"][0]["repo"] == str(repo)
    assert said["guards"] == {"g1": 2} and said["carry"][0]["cov_risk"] == LAND["cov_risk"]
    assert said["profile"][0]["stages"]["      slot wait land"] == [1.0, None]
