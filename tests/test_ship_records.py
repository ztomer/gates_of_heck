"""lib/ship_records.py, goh ship's records in goh.db (GOH.STATEDB S2, goh.db 0003): the rows are
the one record (wave 3: no result or pin file is written or read; `goh state retire ships`
archives the old ones, tests/test_state_retire.py), every reader answers from them, and a missing
goh.db is unknown for a reader of many records, never "no ship"."""

from __future__ import annotations

import json
from pathlib import Path

import goh_db
import pytest
import ship_records


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GOH_DB", str(tmp_path / "goh.db"))
    monkeypatch.setenv("GOH_SHIP_DIR", str(tmp_path / "ship"))
    return tmp_path / "goh.db"


def _rec(base: Path, name: str, stem: str, **kw) -> Path:
    """A ship record's row at base/NAME/STEM.json, as goh ship's save puts it."""
    p = base / name / f"{stem}.json"
    body = {"schema": 1, "repo": f"/r/{name}", "name": name, "item": "X.ONE", "sha": "a" * 40,
            "status": "ok", "started": "2026-11-20T00:00:00+00:00", "version": "1.0.0",
            "finished": "2026-11-20T00:10:00+00:00",
            "steps": [{"name": "push", "status": "ok", "log": str(p.with_suffix(".push.log"))}],
            "result": str(p), **kw}  # fmt: skip
    ship_records.put(body)
    return p


def _estate(base: Path) -> None:
    _rec(base, "alpha", "20261120T000000Z-aaaaaaaa")
    _rec(base, "alpha", "20261121T000000Z-bbbbbbbb", status="failed", failed_step="push")
    _rec(base, "beta", "v0.1.0", item=None)
    ship_records.put_pins(base / "alpha" / "20261121T000000Z-bbbbbbbb.json",
                          {"schema": 2, "tag": "v1", "repos": {}})  # fmt: skip


def _file(p: Path, **kw) -> None:
    """A record FILE as goh wrote one before wave 3: read by nothing now."""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema": 1, "status": "running", "result": str(p), **kw}))


def test_the_rows_are_every_record_and_a_file_beside_them_is_read_by_nothing(
    db: Path, tmp_path: Path
) -> None:
    base = tmp_path / "ship"
    _estate(base)
    _file(base / "alpha" / "20261119T000000Z-ffffffff.json")  # an old file: no record
    _file(base / "gamma" / "20261119T000000Z-eeeeeeee.json")
    _file(base / "alpha" / "20261120T000000Z-aaaaaaaa.pins.json", tag="v0")
    got = [(p.parent.name, p.name, r["status"]) for p, r in ship_records.every(base)]
    assert got == [
        ("alpha", "20261120T000000Z-aaaaaaaa.json", "ok"),
        ("alpha", "20261121T000000Z-bbbbbbbb.json", "failed"),
        ("beta", "v0.1.0.json", "ok"),
    ], got
    assert ship_records.get(base / "alpha" / "20261119T000000Z-ffffffff.json") is None
    assert ship_records.get_pins(base / "alpha" / "20261120T000000Z-aaaaaaaa.json") is None
    pins = ship_records.get_pins(base / "alpha" / "20261121T000000Z-bbbbbbbb.json")
    assert pins is not None and pins["tag"] == "v1"
    with goh_db.ro() as con:
        assert con.execute("SELECT run, repo, status FROM ships_v1 ORDER BY run").fetchall() == [
            ("20261120T000000Z-aaaaaaaa", "alpha", "ok"),
            ("20261121T000000Z-bbbbbbbb", "alpha", "failed"),
            ("v0.1.0", "beta", "ok"),
        ]


def test_no_goh_db_is_unknown_for_many_and_no_record_for_one(db: Path, tmp_path: Path) -> None:
    p = tmp_path / "ship" / "alpha" / "20261120T000000Z-aaaaaaaa.json"
    _file(p)  # a file is no answer either
    assert not db.exists()
    assert ship_records.get(p) is None and ship_records.get_pins(p) is None
    for read in (lambda: ship_records.in_dir(p.parent), ship_records.every, ship_records.live,
                 ship_records.repo_dirs):  # fmt: skip
        with pytest.raises(goh_db.Unknown):
            read()
    assert not db.exists()  # a read never creates it


def test_live_names_every_repos_running_records(db: Path, tmp_path: Path) -> None:
    base = tmp_path / "ship"
    _estate(base)
    one = _rec(base, "alpha", "20261122T000000Z-11111111", status="running")
    two = _rec(base, "gamma", "20261122T000000Z-22222222", status="bisecting")
    assert [p for p, _ in ship_records.live()] == [one, two]


def test_the_writer_row_is_the_record(db: Path, tmp_path: Path) -> None:
    p = _rec(tmp_path / "ship", "alpha", "20261120T000000Z-aaaaaaaa", status="running")
    ship_records.put({**ship_records.get(p), "status": "failed"})
    assert [r["status"] for _, r in ship_records.in_dir(p.parent)] == ["failed"]


def test_a_ship_step_writes_its_row_and_no_file(db: Path, tmp_path: Path) -> None:
    """The writer (lib/ship_status.py save, at each step) puts the row; no result file since the
    database plan's wave 3 (the loop reads ships_v2)."""
    import ship_status

    res = ship_records.get(_rec(tmp_path / "ship", "alpha", "20261120T000000Z-aaaaaaaa"))
    res["status"] = "running"
    ship_status.save(res)
    with goh_db.ro() as con:
        row = con.execute("SELECT status, body FROM ships WHERE result=?", (res["result"],))
        status, body = row.fetchone()
    assert status == "running" and json.loads(body) == res
    assert not Path(res["result"]).exists()  # its directory is made: the step logs live there
    assert [p.name for p in Path(res["result"]).parent.iterdir()] == []


def test_the_install_writes_its_pins_row_and_no_sidecar(db: Path, tmp_path: Path) -> None:
    import consumer_pins

    result = tmp_path / "ship" / "alpha" / "20261120T000000Z-aaaaaaaa.json"
    consumer_pins.write_record(str(result), "v1", "0.1", {"one": {"status": "committed"}})
    with goh_db.ro() as con:
        (body,) = con.execute(
            "SELECT body FROM ship_pins WHERE result=?", (str(result),)
        ).fetchone()
    assert json.loads(body)["repos"] == {"one": {"status": "committed"}}
    assert not consumer_pins.record_path(result).exists()
    res = {"result": str(result)}
    consumer_pins.carry(res)  # the row alone carries it into the result
    assert res["consumer_pins"]["committed"] == ["one"]


def test_the_readers_answer_from_the_rows_and_say_unknown_with_no_goh_db(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`goh ship verdict`, the ships in flight, the budget's backfill rows and its unmeasured
    repos answer from the rows; with no goh.db none of them reads as "no ship"."""
    import os

    import budget
    import budget_ships
    import full_run
    import ship_verdict

    monkeypatch.setattr(ship_verdict.reuse, "lookup_gate", lambda repo, sha: ({"how": "full"}, ""))
    monkeypatch.setattr(budget_ships.timing_ledger, "read", list)
    rows: list[dict] = []
    monkeypatch.setattr(budget_ships.timing_ledger, "append", rows.append)

    def answers() -> tuple:
        rows.clear()
        budget_ships.backfill_ships()
        verdict = ship_verdict.verdict(tmp_path / "alpha", "a" * 40)
        ledger = [(r["kind"], r["repo"], r["ref"]) for r in rows]
        return verdict, full_run.ships_in_flight("alpha"), ledger, budget.unmeasured(set())

    none = answers()  # no goh.db: unknown
    assert none[0][0] is None and "unknown" in none[0][1], none
    assert none[1] == ["unknown (no goh.db)"] and none[2] == [], none
    assert {"subject": "ship", "why": none[3][-1]["why"]} == none[3][-1], none
    assert none[3][-1]["why"].startswith("unknown"), none
    base = tmp_path / "ship"
    _estate(base)
    live = _rec(base, "alpha", "20261122T000000Z-cccccccc", status="running", pid=os.getpid(),
                repo=str(tmp_path / "alpha"), finished=None)  # fmt: skip
    (tmp_path / "alpha").mkdir()
    got = answers()
    assert got[0] == (live, "") and got[1] == [live.name], got
    assert len(got[2]) == 3 and got[3], got  # three finished ships, two repos unmeasured


def test_ships_v2_serves_every_field_the_loop_reads(db: Path, tmp_path: Path) -> None:
    """goh.db 0009 (GOH.SHIPSV2): the loop's ship readers (goh_view) read the highest ships view
    with `body`; every field they read from a record comes back equal from ships_v2."""
    base = tmp_path / "ship"
    loop = {"pid": 4242, "own_s": 12.5, "summary": "did it", "created": "2026-11-20T00:00:00Z",
            "timeout_s": 900, "handoff_sha": "b" * 40}  # fmt: skip
    _rec(base, "alpha", "20261120T000000Z-aaaaaaaa", **loop)
    _rec(base, "alpha", "20261121T000000Z-bbbbbbbb", status="failed", failed_step="push", **loop)
    files = {str(p): r for p, r in ship_records.every(base)}
    with goh_db.ro() as con:
        cols = [r[1] for r in con.execute("PRAGMA table_info(ships_v2)")]
        v1 = [r[1] for r in con.execute("PRAGMA table_info(ships_v1)")]
        rows = dict(con.execute("SELECT result, body FROM ships_v2").fetchall())
    assert set(v1) | {"body"} <= set(cols)
    assert set(rows) == set(files)
    keys = (
        "steps",
        "pid",
        "failed_step",
        "own_s",
        "summary",
        "created",
        "timeout_s",
        "handoff_sha",
    )
    for result, rec in files.items():
        got = json.loads(rows[result])
        assert {k: got.get(k) for k in keys} == {k: rec.get(k) for k in keys}, result
