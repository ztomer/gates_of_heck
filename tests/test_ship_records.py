"""lib/ship_records.py, goh ship's records in goh.db (GOH.STATEDB S2, goh.db 0003): the record and
pin files import once and read back equal, a re-run adds nothing and never overwrites a newer row,
the readers see the same records with the files gone, and the round trip names a planted miss."""

from __future__ import annotations

import json
import shutil
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
    p = base / name / f"{stem}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"schema": 1, "repo": f"/r/{name}", "name": name, "item": "X.ONE", "sha": "a" * 40,
            "status": "ok", "started": "2026-11-20T00:00:00+00:00", "version": "1.0.0",
            "finished": "2026-11-20T00:10:00+00:00",
            "steps": [{"name": "push", "status": "ok", "log": str(p.with_suffix(".push.log"))}],
            "result": str(p), **kw}  # fmt: skip
    p.write_text(json.dumps(body, indent=1) + "\n")
    return p


def _estate(base: Path) -> None:
    _rec(base, "alpha", "20261120T000000Z-aaaaaaaa")
    _rec(base, "alpha", "20261121T000000Z-bbbbbbbb", status="failed", failed_step="push")
    _rec(base, "beta", "v0.1.0", item=None)
    pins = base / "alpha" / "20261121T000000Z-bbbbbbbb.pins.json"
    pins.write_text(json.dumps({"schema": 2, "tag": "v1", "repos": {}}))
    (base / "alpha" / "20261121T000000Z-bbbbbbbb.sweep.json").write_text("{}")  # no record
    (base / "alpha" / "covsrc.json").write_text("{}")  # the coverage summary: no record


def test_the_files_import_once_and_read_back_equal(db: Path, tmp_path: Path) -> None:
    base = tmp_path / "ship"
    _estate(base)
    before = [(p, r) for p, r in ship_records.every(base)]
    assert [p.name for p, _ in before] == [
        "20261120T000000Z-aaaaaaaa.json",
        "20261121T000000Z-bbbbbbbb.json",
        "v0.1.0.json",
    ]
    assert ship_records.import_files(base) == {"ships": 3, "ship_pins": 1}
    assert ship_records.import_files(base) == {"ships": 0, "ship_pins": 0}  # a re-run is harmless
    assert ship_records.roundtrip(base) == []
    with goh_db.ro() as con:
        assert con.execute("SELECT run, repo, status FROM ships_v1 ORDER BY run").fetchall() == [
            ("20261120T000000Z-aaaaaaaa", "alpha", "ok"),
            ("20261121T000000Z-bbbbbbbb", "alpha", "failed"),
            ("v0.1.0", "beta", "ok"),
        ]
    shutil.rmtree(base)  # wave 3's delete: the rows alone answer every reader
    assert ship_records.every(base) == before
    pins = ship_records.get_pins(before[1][0])
    assert pins is not None and pins["tag"] == "v1"


def test_an_import_never_overwrites_a_newer_row(db: Path, tmp_path: Path) -> None:
    """The writer's row is newer than the file an old HEAD export left: INSERT OR IGNORE."""
    p = _rec(tmp_path / "ship", "alpha", "20261120T000000Z-aaaaaaaa", status="running")
    ship_records.put({**json.loads(p.read_text()), "status": "ok"})
    ship_records.import_files(tmp_path / "ship")
    assert ship_records.get(p)["status"] == "ok"
    assert ship_records.roundtrip(tmp_path / "ship") == [f"{p}: row differs"]  # names the gap


def test_a_planted_miss_fails_the_round_trip(db: Path, tmp_path: Path) -> None:
    base = tmp_path / "ship"
    _estate(base)
    ship_records.import_files(base)
    with goh_db.transaction() as con:
        con.execute("DELETE FROM ship_pins")
    got = ship_records.roundtrip(base)
    assert got == [f"{base}/alpha/20261121T000000Z-bbbbbbbb.pins.json: no row"], got


def test_a_reader_with_no_goh_db_reads_the_files(db: Path, tmp_path: Path) -> None:
    p = _rec(tmp_path / "ship", "alpha", "20261120T000000Z-aaaaaaaa")
    assert not db.exists()
    assert ship_records.get(p)["status"] == "ok"
    assert [q for q, _ in ship_records.in_dir(p.parent)] == [p]
    assert not db.exists()  # a read never creates it


def test_the_writer_row_wins_over_its_file(db: Path, tmp_path: Path) -> None:
    p = _rec(tmp_path / "ship", "alpha", "20261120T000000Z-aaaaaaaa", status="running")
    ship_records.put({**json.loads(p.read_text()), "status": "failed"})
    assert [r["status"] for _, r in ship_records.in_dir(p.parent)] == ["failed"]


def test_a_ship_step_writes_its_row_and_its_file(db: Path, tmp_path: Path) -> None:
    """The writer (lib/ship_status.py save, at each step) puts the row first; until wave 3's
    delete commit the file is written beside it for the readers outside goh."""
    import ship_status

    res = json.loads(_rec(tmp_path / "ship", "alpha", "20261120T000000Z-aaaaaaaa").read_text())
    res["status"] = "running"
    ship_status.save(res)
    with goh_db.ro() as con:
        row = con.execute("SELECT status, body FROM ships WHERE result=?", (res["result"],))
        status, body = row.fetchone()
    assert status == "running" and json.loads(body) == res
    assert json.loads(Path(res["result"]).read_text()) == res


def test_the_install_writes_its_pins_row(db: Path, tmp_path: Path) -> None:
    import consumer_pins

    result = tmp_path / "ship" / "alpha" / "20261120T000000Z-aaaaaaaa.json"
    consumer_pins.write_record(str(result), "v1", "0.1", {"one": {"status": "committed"}})
    with goh_db.ro() as con:
        (body,) = con.execute(
            "SELECT body FROM ship_pins WHERE result=?", (str(result),)
        ).fetchone()
    assert json.loads(body)["repos"] == {"one": {"status": "committed"}}
    res = {"result": str(result)}
    consumer_pins.record_path(result).unlink()  # the row alone carries it into the result
    consumer_pins.carry(res)
    assert res["consumer_pins"]["committed"] == ["one"]


def test_the_readers_answer_the_same_from_the_rows_alone(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plan's red first: `goh ship verdict`, the ships in flight, the budget's backfill rows
    and its unmeasured repos read the same before the import (files) and after it with the files
    gone (rows)."""
    import os

    import budget
    import budget_ships
    import full_run
    import ship_verdict

    base = tmp_path / "ship"
    _estate(base)
    live = _rec(base, "alpha", "20261122T000000Z-cccccccc", status="running", pid=os.getpid(),
                repo=str(tmp_path / "alpha"), finished=None)  # fmt: skip
    ship_records.put(json.loads(live.read_text()))  # the running ship's own writer: a row
    (tmp_path / "alpha").mkdir()
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

    before = answers()
    assert before[0] == (live, "") and before[1] == [live.name], before
    assert len(before[2]) == 3 and before[3], before  # three finished ships, two repos unmeasured
    ship_records.import_files(base)
    for p in base.glob("*/*.json"):
        p.unlink()
    assert answers() == before


def test_a_running_record_is_imported_only_once_it_ends(db: Path, tmp_path: Path) -> None:
    """A ship an older goh runs across the switch rewrites its FILE at each step: imported while
    running, its row would freeze and outrank the file. It is read from the file until it ends."""
    base = tmp_path / "ship"
    p = _rec(base, "alpha", "20261120T000000Z-aaaaaaaa", status="running")
    assert ship_records.import_files(base) == {"ships": 0, "ship_pins": 0}
    _rec(base, "alpha", "20261120T000000Z-aaaaaaaa", status="ok")  # the old worker's last step
    assert ship_records.import_files(base) == {"ships": 1, "ship_pins": 0}
    assert ship_records.get(p)["status"] == "ok"


def test_ships_v2_serves_every_field_the_loop_reads(db: Path, tmp_path: Path) -> None:
    """goh.db 0009 (GOH.SHIPSV2): the loop's ship readers (goh_view) read the highest ships view
    with `body`; every field they read from a result file comes back equal from ships_v2."""
    base = tmp_path / "ship"
    loop = {"pid": 4242, "own_s": 12.5, "summary": "did it", "created": "2026-11-20T00:00:00Z",
            "timeout_s": 900, "handoff_sha": "b" * 40}  # fmt: skip
    _rec(base, "alpha", "20261120T000000Z-aaaaaaaa", **loop)
    _rec(base, "alpha", "20261121T000000Z-bbbbbbbb", status="failed", failed_step="push", **loop)
    files = {str(p): r for p, r in ship_records.every(base)}
    ship_records.import_files(base)
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
