"""lib/span_store.py, lib/timing_derived.py and lib/timing_ledger.py over goh.db's spans
(GOH.STEPTIMES): what a row holds, that nothing is kept twice, and the spool's way in.

The tables are 0014's own SQL in memory; only the spool is a file.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import re
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import span_store
import timing_derived
import timing_ledger
from conftest import REPO_ROOT

SCHEMA = REPO_ROOT / "lib" / "goh_db_schema" / "0014_spans_views.sql"
NOW = time.time()


def memory() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    con.executescript("CREATE TABLE lands(run TEXT); CREATE TABLE ships(run TEXT);")
    return con


def row(kind: str = "push", repo: str = "ztools", wall: float = 12.5, at: float = NOW - 60, **kw):
    return timing_ledger.row_for(
        kind, repo, wall, at=dt.datetime.fromtimestamp(at, dt.UTC), read_load=False, **kw
    )


FULL = row("land", "goh", 412.5, cpu_s=980.2, load1=6.11, ok=False, control_drift=-0.0213,
           top_steps=[["clippy", 90.5], ["pytest", 80.0]], load_end=9.5, sha="0123456789ab",
           item="A.B", version="1.2.3", exit=3, pre_s=1.5, wait_lock_s=4.5, slot_wait_s=2.5,
           slot_asked_at=NOW - 90, slot_class="work", pre_run_s=3.5, pre_cpu_s=8.5, pre_workers=4,
           pre_reused="s whole", pre_fast=1, pre_why="w", suite_reused="s", suite_why="x",
           before_s=7.0, at_day=True, backfill="CIP", fs_events=1, fs_dirs=2, fs_tests=3,
           fs_whole=1, shadow_f=1, shadow_ran=2, shadow_red_out=0, shadow_red="t::a")  # fmt: skip


@pytest.fixture
def ledger(monkeypatch, tmp_path: Path) -> SimpleNamespace:
    """The ledger's goh.db in memory, its spool under tmp_path; `ledger.down` refuses its writes."""
    con = memory()

    @contextlib.contextmanager
    def transaction():
        if down:
            raise sqlite3.OperationalError("database is locked")
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")

    down: list[int] = []
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path))
    monkeypatch.setattr(timing_ledger, "db", lambda: tmp_path / "goh.db")
    seam = SimpleNamespace(transaction=transaction, known=lambda: contextlib.nullcontext(con))
    monkeypatch.setattr(timing_ledger, "goh_db", seam)
    monkeypatch.setattr(timing_derived, "_ships", list)
    monkeypatch.setattr(timing_derived, "_attempts", list)
    return SimpleNamespace(con=con, down=down, spool=tmp_path / "ledger.jsonl")


def spans(con: sqlite3.Connection) -> list[dict]:
    return span_store.rows(con, 0.0)


def test_a_row_of_every_field_reads_back_as_it_was_written() -> None:
    con = memory()
    assert set(span_store.COLUMNS) <= set(FULL), "FULL names every column"
    for r in (FULL, row("suite:carried", "ztools", 0.0), row("push", "", 3.0, ref="CIP.X")):
        assert span_store.put(con, r)
    assert sorted(spans(con), key=json.dumps) == sorted(
        [FULL, row("suite:carried", "ztools", 0.0), row("push", "", 3.0, ref="CIP.X")],
        key=json.dumps)  # fmt: skip


def test_the_same_row_twice_is_one_span_and_another_run_of_that_tenth_is_kept() -> None:
    con = memory()
    assert span_store.put(con, FULL) and not span_store.put(con, FULL)
    other = {**FULL, "ok": True}  # the same subject, the same tenth of a second
    assert span_store.put(con, other) and not span_store.put(con, other)
    assert spans(con) == [FULL, other]
    assert con.execute("SELECT count(*) FROM span_steps").fetchone() == (4,)


@pytest.mark.parametrize("tail", [0.0, 0.04, 0.96, 0.999])
def test_one_instant_renders_both_spellings_of_the_row(tail: float) -> None:
    """59.96 was written as second 59 and as tenth 60.0: the one number gives both back."""
    con, r = memory(), row(at=1_795_000_059 + tail)
    assert span_store.put(con, r)
    assert spans(con) == [r]


def test_a_field_with_no_column_is_said_and_one_another_table_owns_is_not(capsys) -> None:
    con = memory()
    assert span_store.put(con, {**row("ship"), "failed_step": "verify", "land_wait_s": 3.0})
    assert "no column" not in capsys.readouterr().err
    assert span_store.put(con, {**row("push", wall=9.0), "surprise": 1, "exit": "soon"})
    assert "a push row's exit, surprise has no column" in capsys.readouterr().err
    assert len(spans(con)) == 2  # the measurement is kept


@pytest.mark.parametrize("bad", [{"wall_s": -1.0}, {"wall_s": "x"}, {"epoch": "x"}, {"kind": ""}])
def test_what_is_no_measurement_is_no_span_and_never_raises(bad: dict) -> None:
    assert not span_store.put(memory(), {**row(), **bad})


def test_a_row_with_a_text_no_column_holds_never_wedges_the_spool(ledger) -> None:
    odd = row("push", sha="a\ud800", ref="r\ud800", top_steps=[["s\ud800", 1.0]])
    assert timing_ledger._spool(row("suite")) and timing_ledger._spool({**odd, "sha": "x"})
    ledger.spool.write_bytes(ledger.spool.read_bytes().replace(b'"x"', b'"a\\ud800"'))
    assert timing_ledger.drain() == 2 and ledger.spool.read_bytes() == b""
    assert sorted(r.get("sha", "") for r in spans(ledger.con)) == ["", "a?"]


def test_every_field_a_shell_writer_names_has_a_column() -> None:
    named: set[str] = set()
    for p in (*REPO_ROOT.glob("lib/*.sh"), *REPO_ROOT.glob("tools/*.sh"),
              *REPO_ROOT.glob("gates/*.sh")):  # fmt: skip
        named |= set(re.findall(r'--field "(\w+)=', p.read_text(encoding="utf-8")))
    assert {"exit", "wait_lock_s", "sha", "slot_class"} <= named, named  # the scan sees them
    assert named - set(span_store.COLUMNS) == set()


def test_a_repo_is_kept_by_its_key_and_read_by_its_name(monkeypatch, tmp_path: Path) -> None:
    for d in ("gates_of_heck", "top", "group/inner/.git"):
        (tmp_path / d).mkdir(parents=True)
    monkeypatch.setenv("GOH_REPOS_ROOT", str(tmp_path))
    names = {"goh": "gates_of_heck", "top": "top", "inner": "group/inner", "gone": "gone", "": ""}
    con = memory()
    for name, key in names.items():
        assert span_store.key(name) == key
        assert span_store.put(con, row(repo=name))
    assert {r["repo"] for r in spans(con)} == set(names)
    assert [r["repo"] for r in span_store.rows(con, 0.0, (), "inner")] == ["inner"]


def test_a_row_is_a_span_and_leaves_no_line(ledger) -> None:
    assert timing_ledger.append(FULL)
    assert spans(ledger.con) == [FULL] and not ledger.spool.exists()
    assert timing_ledger.read() == [FULL]
    assert timing_ledger.p95("land", "gates_of_heck") == (419.5, 1)


def test_a_row_goh_db_would_not_take_waits_in_the_spool_and_goes_in_with_the_next(ledger) -> None:
    ledger.down.append(1)
    assert timing_ledger.append(FULL)
    assert spans(ledger.con) == [] and timing_ledger.read() == [FULL]  # read from the spool
    ledger.down.clear()
    late = row("suite", "goh", 30.0, at=NOW - 5)
    assert timing_ledger.append(late)
    assert spans(ledger.con) == [FULL, late] == timing_ledger.read()
    assert ledger.spool.read_bytes() == b""


def test_a_kill_between_the_commit_and_the_truncate_adds_nothing(ledger, monkeypatch) -> None:
    for r in (FULL, row("suite", "goh", 30.0)):
        assert timing_ledger._spool(r)

    def killed(*_: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(timing_ledger.os, "truncate", killed)
    with pytest.raises(KeyboardInterrupt):
        timing_ledger.drain()
    assert len(spans(ledger.con)) == 2 and ledger.spool.read_bytes()
    assert len(timing_ledger.read()) == 2  # in both places, read once
    monkeypatch.undo()
    before = spans(ledger.con)
    for r in timing_ledger.spooled(ledger.spool):
        assert not span_store.put(ledger.con, r)
    assert spans(ledger.con) == before


def test_a_failed_drain_leaves_every_line(ledger) -> None:
    assert timing_ledger._spool(FULL)
    was = ledger.spool.read_bytes()
    ledger.down.append(1)
    with pytest.raises(sqlite3.OperationalError):
        timing_ledger.drain()
    assert ledger.spool.read_bytes() == was


def test_a_ship_and_an_attempt_with_a_record_are_read_from_it_alone(ledger, monkeypatch) -> None:
    ledger.con.executescript("INSERT INTO lands VALUES ('1-A'); INSERT INTO ships VALUES ('s1');")
    made = [row("land attempt", "goh", 7.0, ref="1-A", mode="land")]
    monkeypatch.setattr(timing_derived, "_attempts", lambda: made)
    ship = row("ship", "goh", 5.0, ref="s0")  # its record is gone: the span is all there is
    for r in (row("land attempt", "goh", 9.0, ref="1-A"), row("ship", "goh", 4.0, ref="s1"), ship):
        assert timing_ledger.append(r)
    assert spans(ledger.con) == [ship]
    assert timing_ledger.read() == [ship, *made]
    assert timing_ledger.known_refs("land attempt", "goh") == {"1-A"}
    assert timing_ledger.read(kinds=("ship",)) == [ship]


def test_the_derived_rows_are_the_records_own(monkeypatch, capsys) -> None:
    """A land's attempt number is counted as the lander counts it; a ship's `release` is a span of
    its own; a fault of one table is said and costs the other nothing."""
    when = {"started": "2026-11-20T10:00:00+00:00", "finished": "2026-11-20T10:05:00+00:00"}
    rec = {**when, "item": "A.B", "repo": "/r/gates_of_heck", "state": "red", "mode": "land"}
    lands = [("1-A.B", rec), ("2-A.B", {**rec, "state": "landed"}), ("3-A.B", rec), ("4-X", {})]
    monkeypatch.setattr("land_records.records", lambda q, keys: lands)
    ship = {**when, "schema": 1, "name": "ztools", "result": "/s/r1.json", "status": "ok",
            "steps": [{"name": "release", "seconds": 9.0, "status": "ok"}]}  # fmt: skip
    monkeypatch.setattr("ship_records.every", lambda: [(Path("r1"), ship)])
    got = timing_derived.rows(timing_derived.KINDS, 0.0)
    assert [(r["kind"], r["ref"], r.get("attempt"), r["ok"]) for r in got] == [
        ("ship", "r1", None, True), ("land attempt", "1-A.B", 1, False),
        ("land attempt", "2-A.B", 2, True), ("land attempt", "3-A.B", 1, False)]  # fmt: skip
    assert timing_derived.rows(timing_derived.KINDS, got[0]["epoch"] + 1) == []

    def broken() -> list:
        raise sqlite3.OperationalError("no such table: ships")

    monkeypatch.setattr("ship_records.every", broken)
    assert len(timing_derived.rows(timing_derived.KINDS, 0.0)) == 3
    assert "the ships are unknown" in capsys.readouterr().err


def test_the_window_is_a_month_and_a_week(ledger) -> None:
    old = row(at=NOW - (timing_ledger.DAYS + 1) * 86400)
    assert timing_ledger.append(old) and timing_ledger.append(FULL)
    assert len(spans(ledger.con)) == 2 and timing_ledger.read() == [FULL]


def test_a_named_spool_is_no_goh_db_s(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "goh.db").write_bytes(b"")
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("GOH_DB", raising=False)
    assert timing_ledger.db() is None
    monkeypatch.setenv("GOH_DB", str(tmp_path / "goh.db"))
    assert timing_ledger.db() == tmp_path / "goh.db"
    monkeypatch.setenv("GOH_DB", str(tmp_path / "none.db"))
    assert timing_ledger.db() is None  # never made for a measurement's sake


@pytest.mark.parametrize("epoch", [{}, {"epoch": "soon"}])
def test_a_spooled_row_with_no_time_is_read_as_the_oldest(ledger, epoch: dict) -> None:
    odd = {"v": 1, "subject": "x push", "kind": "push", "repo": "x", "wall_s": 3.0, **epoch}
    timing_ledger.append(row())
    assert timing_ledger._spool(odd)
    assert timing_ledger.read()[0] == odd
