"""GOH.BODYCOLS: each store over the file its contract leaves (no document, no trigger of an older
copy): what its module writes, its module reads, from the columns and child rows alone."""

from __future__ import annotations

import types
from pathlib import Path

import flakes
import goh_db
import hook_ledgers
import land_records
import land_slot
import landq_state
import pytest
import reuse_audit_store
import selection_miss
import small_stores
import state_columns
from _body_fixture import (
    DOCS,
    FLAKE,
    JUDGED,
    LAND,
    LANE,
    LOW,
    MARK,
    MISS,
    OFF,
    ON,
    SAMPLE,
    STARVED,  # fmt: skip
    STEP,
    contract,
)
from body_tables import TABLES


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("GOH_DB", str(tmp_path / "state" / "goh.db"))
    monkeypatch.setattr(reuse_audit_store, "NAMED", goh_db.path())
    with goh_db.transaction() as con:
        for t in TABLES:
            contract(con, t)
        con.execute("ALTER TABLE commit_gated DROP COLUMN body")
    return goh_db.path()


def test_every_store_writes_and_reads_the_contracts_file(db, tmp_path) -> None:
    for row in (LANE, LOW):
        assert hook_ledgers.append(hook_ledgers.LANES, row) is None
    assert hook_ledgers.lanes() == [LANE, LOW]
    small_stores.flake_add(db.parent / "flakes", "t::a", FLAKE)
    small_stores.flake_add(db.parent / "flakes", "t::b", DOCS["flakes"][1])
    assert small_stores.flakes(db.parent / "flakes") == DOCS["flakes"]
    assert small_stores.flakes(db.parent / "flakes", "t::b") == DOCS["flakes"][1:]
    rows = [JUDGED, MISS, STEP]
    assert selection_miss.append(tmp_path / "q", rows) == 3
    assert selection_miss.append(tmp_path / "q", rows) == 0
    assert selection_miss.read(tmp_path / "q") == rows
    assert selection_miss.read(tmp_path / "other") == []
    assert land_records.append(tmp_path / "q", "guards", [{"guard": "g", "at": "x"}] * 2) == 1
    assert land_records.append(tmp_path / "q", "guards", []) == 0
    reuse_audit_store.shadow_add({**SAMPLE, "outcome": "agreed"})
    assert reuse_audit_store.shadow() == [{**SAMPLE, "outcome": "agreed"}]
    for rec in (OFF, ON, STARVED):
        reuse_audit_store.put(rec)
        assert reuse_audit_store.get("carry") == rec
    assert reuse_audit_store.get("none") is None
    with goh_db.ro() as con:
        assert state_columns.orphans(con) == []
        assert con.execute("SELECT count(*), max(at) > 0 FROM reuse_audit").fetchone() == (1, 1)


def test_a_requests_whole_life(db, tmp_path) -> None:
    """Marked, its pre-step noted, superseded, filed early, filed, rewritten, run, landed; another
    cancelled: read back equal at each step, no child row left at the end."""
    q = tmp_path / "q"
    marker = {k: v for k, v in MARK.items() if k not in ("pre", "superseded")}
    mark = landq_state.put(q, "7-A.B", marker, "enqueuing")
    assert landq_state.get(mark) == marker
    noted = {**marker, "pre": MARK["pre"]}
    assert landq_state.update(mark, noted) and landq_state.get(mark) == noted
    assert landq_state.update(mark, MARK) and landq_state.rows(q, "enqueuing") == [(mark, MARK)]
    early = landq_state.put(q, "1795000000000000000-A.B", LAND, landq_state.EARLY)
    assert landq_state.get(early) == LAND and landq_state.listing(q, "early") == [early]
    assert landq_state.drop(early) and landq_state.get(early) is None
    bare = {k: v for k, v in LAND.items() if k not in ("main_red", "no_pre")}
    path = landq_state.put(q, "1795000000000000001-A.B", bare, replaces="7-A.B")
    assert landq_state.listing(q, "enqueuing") == [] and landq_state.get(path) == bare
    assert landq_state.update(path, LAND) and landq_state.get(path) == LAND
    with goh_db.ro() as con:
        assert land_slot._queued(con, q, "tok") and not land_slot._queued(con, q, "other")
    other = landq_state.put(q, "1795000000000000002-C.D", {**LAND, "item": "C.D"})
    assert landq_state.get(other) == {**LAND, "item": "C.D"} and landq_state.get(path) == LAND
    run = landq_state.move(path, "running")
    assert landq_state.get(path) is None and landq_state.get(run) == LAND
    assert not landq_state.update(path, LAND)
    with goh_db.transaction() as con:
        landq_state.finish(con, run)
    assert landq_state.drop(other)
    with goh_db.ro() as con:
        assert state_columns.orphans(con) == []
        for t in ("requests", "early", "request_nodes", "request_cov_carry", "request_cov_files"):
            assert con.execute(f"SELECT count(*) FROM {t}").fetchone() == (0,), t


def test_a_miss_is_one_row_however_often_it_is_written(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GOH_DB", str(tmp_path / "goh.db"))
    assert [land_records.append(tmp_path, "misses", [JUDGED, MISS]) for _ in "ab"] == [2, 0]
    assert land_records.ledger(tmp_path, "misses") == [JUDGED, MISS]


def test_the_commit_gated_rows_are_their_two_columns(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GOH_DB", str(tmp_path / "goh.db"))
    at = "2026-11-18T11:06:40Z"
    small_stores.gated_add(tmp_path / "x", {"tree": "t", "repo": "/w", "at": at})
    assert small_stores.gated(tmp_path / "x") == [{"tree": "t", "at": at}]


def test_a_flakes_at_is_the_second_of_its_epoch(tmp_path, monkeypatch) -> None:
    """Rounded apart, a record of the last half millisecond of a second said two instants."""
    kept: list[dict] = []
    monkeypatch.setenv("GOH_FLAKE_DIR", str(tmp_path / "flakes"))
    monkeypatch.setattr(small_stores, "flakes", lambda *_: [])
    monkeypatch.setattr(small_stores, "flake_add", lambda _d, _t, row: kept.append(row))
    clock = types.SimpleNamespace(time=lambda: 1_795_000_000.9997)
    monkeypatch.setattr(flakes, "time", clock)
    flakes._record("t::a", "/r", "s", "x", ("", ""), 1.0, "red", "green")
    assert (kept[0]["at"], kept[0]["epoch"]) == ("2026-11-18T11:06:41+00:00", 1_795_000_001.0)
