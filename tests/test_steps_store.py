"""lib/steps_store.py: a run's gate steps as rows of goh-cache.db's `steps` (GOH.STEPTIMES). The
table is 0004's own SQL in memory; a spool is the one small file a test writes.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import goh_cache_db
import land_steps
import pytest
import ship_profile
import state_bounds
import state_paths
import step_attribution
import step_timings
import steps_store
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

SCHEMA = REPO_ROOT / "lib" / "goh_cache_schema" / "0004_steps.sql"
T = 1_795_000_000.5
RUN = "01795072197233745000-GOH.X1"


def step(label: str, ms: float = 1000.0, tier: str = "step", **kw) -> dict:
    return {"label": label, "ms": ms, "rc": 0, "tier": tier, "parent": "", "cwd": "/r/x", **kw}


def native(label: str, ms: float, parent: str = "gate", **kw) -> dict:
    return step(label, ms, "native", parent=parent, kind="native", **kw)


@pytest.fixture
def cache(monkeypatch, tmp_path: Path):
    """goh-cache.db in memory; `cache.down` makes its writes refuse."""
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    down: list[int] = []

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

    here = tmp_path / "here"
    here.touch()
    seam = SimpleNamespace(path=lambda: here, transaction=transaction,
                           ro=lambda: contextlib.nullcontext(con),
                           missable=goh_cache_db.missable)  # fmt: skip
    monkeypatch.setattr(steps_store, "goh_cache_db", seam)  # this module's: the suite's is the real
    return SimpleNamespace(con=con, down=down, here=here, seam=seam)


def spool(d: Path, lines: list[dict], name: str = f"{RUN}.gate1") -> Path:
    d.mkdir(exist_ok=True)
    p = d / f"{name}.timings.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
    return p


def dump(con: sqlite3.Connection) -> list[tuple]:
    cols = ", ".join(steps_store.COLUMNS)
    return con.execute(f"SELECT {cols} FROM steps ORDER BY run, part, seq").fetchall()


@pytest.mark.parametrize(("name", "want"), [
    (RUN, (RUN, "")),
    (f"{RUN}.gate3", (RUN, "gate3")),
    ("20261116T130249Z-7c29deb1.verify", ("20261116T130249Z-7c29deb1", "verify")),
    ("v1.2.3.sweep", ("v1.2.3", "sweep")),
])  # fmt: skip
def test_a_spool_s_name_is_its_run_and_its_part(name: str, want: tuple) -> None:
    assert steps_store.named(f"/d/{name}.timings.jsonl") == want


def test_a_spool_of_no_run_is_named_by_its_path_in_token_form() -> None:
    at = state_paths.state_root() / "estate" / "pins.sweep.timings.jsonl"
    assert steps_store.named(at) == ("$STATE/estate/pins.sweep", "")


LINES = [
    step("clippy", 90_000.0, cpu_ms=80_000.5, load1=6.5, t=T, kind="rust", cache="hit"),
    step("control", 900.0, "control", cpu_ms=800.0, t=T + 1, kind="rust", control=True),
    {"label": "goh.db begin", "ms": 3.0, "rc": 0, "tier": "db", "parent": "pytest", "db": "goh",
     "pid": 77, "wait_s": 0.003},
    step("red check", 12.0, "native", rc=1),
]  # fmt: skip


def test_the_rows_are_the_lines_and_the_spool_is_gone(cache, tmp_path: Path) -> None:
    p = spool(tmp_path, LINES)
    assert steps_store.ingest(p) and not p.exists()
    assert cache.con.execute("SELECT DISTINCT run, part FROM steps").fetchall() == [(RUN, "gate1")]
    got = steps_store.rows(p)
    assert got is not None and [r.pop("t") for r in got] == [T, T + 1, T + 1, T + 1]
    want = [{"cwd": "", **{k: v for k, v in r.items() if k != "t"}} for r in LINES]
    assert got == want
    assert step_timings.report(got) == step_timings.report(want)


def test_the_path_is_stored_in_token_form_and_read_as_a_path(cache, tmp_path: Path) -> None:
    where = str(state_paths.repos_root() / "x" / "crate")
    p = spool(tmp_path, [step("a", cwd=where)])
    assert steps_store.ingest(p)
    assert cache.con.execute("SELECT dir FROM steps").fetchone() == ("$REPOS/x/crate",)
    assert [r["cwd"] for r in steps_store.rows(p) or ()] == [where]


@pytest.mark.parametrize(("ts", "want"), [
    ([None, 5.0, None, 7.0], [5.0, 5.0, 5.0, 7.0]),
    ([None, None], [99.0, 99.0]),
])  # fmt: skip
def test_every_step_has_the_one_timestamp(ts: list, want: list[float]) -> None:
    lines = [step(str(i)) | ({"t": t} if t else {}) for i, t in enumerate(ts)]
    assert [r["at"] for r in steps_store.records(lines, "r", "", 99.0)] == want


def test_the_small_native_steps_of_one_parent_are_one_row() -> None:
    lines = [step("gate", 9000.0, t=T), native("a", 10.4), native("b", 20.3, cpu_ms=1.0),
             native("big", 250.0), native("bad", 5.0, rc=1), native("p", 30.0),
             native("under p", 7.0, parent="p"), native("c", 1.0), native("ctl", 2.0, control=True),
             native("z", 3.0, parent="other"), native("y", 4.0, parent="other")]  # fmt: skip
    recs = steps_store.records(lines, "r", "", T)
    folded = [(r["seq"], r["parent"], r["ms"], r["calls"], r["cpu_ms"]) for r in recs
              if r["label"] == steps_store.FOLDED]  # fmt: skip
    assert folded == [(1, "gate", 31.7, 3, 1.0), (9, "other", 7.0, 2, None)]
    assert {r["label"] for r in recs} == {
        "gate", "big", "bad", "p", "under p", "ctl", steps_store.FOLDED}  # fmt: skip
    assert sum(r["calls"] for r in recs) == len(lines)  # none is lost count of


def test_what_a_parent_s_rows_name_is_the_same_sum_after_the_fold(cache, tmp_path: Path) -> None:
    lines = [step("gate", 60_000.0, t=T), *(native(f"n{i}", 200.0) for i in range(40)),
             native("slow", 4000.0)]  # fmt: skip
    before = step_attribution.gaps(lines)
    p = spool(tmp_path, lines)
    assert steps_store.ingest(p) and len(dump(cache.con)) == 3
    assert before and step_attribution.gaps(steps_store.rows(p) or []) == before


def test_the_land_s_steps_are_the_same_from_the_table(cache, tmp_path: Path) -> None:
    lines = [step("cargo test", 5000.0, cpu_ms=9.0), step("cargo test", 2000.0, parent="x"),
             native("a", 1.0), step("cargo test", 1000.0)]  # fmt: skip
    p = spool(tmp_path, lines)
    before = land_steps.steps_from_timings(p)
    assert steps_store.ingest(p) and before and land_steps.steps_from_timings(p) == before
    assert land_steps.steps_from_timings(tmp_path / "none.timings.jsonl") is None


def test_bytes_no_column_holds_are_replaced_and_the_rest_is_kept(cache, tmp_path: Path) -> None:
    """One torn or odd line is never the reason a spool stays for ever."""
    p = spool(tmp_path, [step("a"), step("lone \ud800 surrogate"), step("b")])
    p.write_bytes(p.read_bytes() + b'{"label": "torn \xff"\n[1]\n"x"\n')
    assert steps_store.ingest(p)
    assert [r["label"] for r in steps_store.rows(p) or ()] == ["a", "lone ? surrogate", "b"]


def test_a_spool_taken_in_twice_is_the_same_rows(cache, tmp_path: Path, monkeypatch) -> None:
    """A kill between the commit and the removal leaves it: read once, taken in to nothing new."""
    p = spool(tmp_path, LINES)

    def killed(*_: object) -> None:
        raise KeyboardInterrupt

    with monkeypatch.context() as m:
        m.setattr(steps_store.Path, "unlink", killed)
        with pytest.raises(KeyboardInterrupt):
            steps_store.ingest(p)
    once = dump(cache.con)
    assert len(once) == 4 and p.exists()
    assert len(steps_store.rows(p) or ()) == 4  # not eight
    assert steps_store.ingest(p) and dump(cache.con) == once


def test_a_child_that_outlived_its_run_adds_its_lines_after_the_run_s(cache, tmp_path) -> None:
    assert steps_store.ingest(spool(tmp_path, LINES))
    late = spool(tmp_path, [step("late", 3.0, t=T + 60)])
    assert [r["label"] for r in steps_store.rows(late) or ()][-2:] == ["red check", "late"]
    assert steps_store.ingest(late)
    assert [r[2] for r in dump(cache.con)] == [0, 1, 2, 3, 4]


def test_a_resumed_step_is_profiled_afresh(cache, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GOH_TIMINGS", "")
    stem = tmp_path / "20261116T130249Z-7c29deb1"
    p = Path(ship_profile.profile(stem, "release"))
    p.write_text(json.dumps(step("first")) + "\n")
    ship_profile.keep({"steps": [{"name": "release", "timings": str(p)}]})
    assert os.environ.get("GOH_TIMINGS") is None  # a later lock wait opens no new spool
    assert [r["label"] for r in steps_store.rows(p) or ()] == ["first"] and not p.exists()
    again = Path(ship_profile.profile(stem, "release"))
    assert again == p and dump(cache.con) == []
    p.write_text(json.dumps(step("second")) + "\n")
    assert [r["label"] for r in steps_store.rows(p) or ()] == ["second"]  # live


def test_the_status_table_leaves_the_many_small_ones_out(cache, tmp_path: Path) -> None:
    p = spool(tmp_path, [step("slow", 900.0), *(native(f"n{i}", 200.0) for i in range(40))],
              "20261116T130249Z-7c29deb1.release")  # fmt: skip
    assert steps_store.ingest(p)
    table = ship_profile.table({"steps": [{"name": "release", "timings": str(p)}]})
    assert len(table) == 2 and "slow" in table[1], table


@pytest.mark.parametrize("why", ["refused", "absent"])
def test_a_spool_the_table_cannot_take_stays_and_is_read(cache, tmp_path: Path, why: str) -> None:
    p = spool(tmp_path, LINES)
    cache.down.append(1) if why == "refused" else cache.here.unlink()
    assert not steps_store.ingest(p) and steps_store.ingest_all([str(p)]) == 0
    assert steps_store.rows(p) == step_timings.load(str(p)) and dump(cache.con) == []


def test_the_sweep_takes_in_the_spools_nobody_writes(cache, tmp_path: Path, monkeypatch) -> None:
    """Every spool of before the table, and any a killed run leaves; never a running ship's."""
    monkeypatch.setenv("GOH_LANDQ_DIR", str(tmp_path / "q"))
    monkeypatch.setenv("GOH_SHIP_DIR", str(tmp_path / "s"))
    (tmp_path / "q").mkdir()
    (tmp_path / "s").mkdir()
    now = T  # no clock: the sweep is told the time
    land = spool(tmp_path / "q" / "logs", LINES, RUN)
    ship = spool(tmp_path / "s" / "goh", LINES, "20261116T130249Z-7c29deb1.install")
    for p, age in ((land, 2 * 3600), (ship, 2 * 3600)):
        os.utime(p, (now - age, now - age))
    monkeypatch.setattr(state_bounds, "REGISTRY", {})
    monkeypatch.setattr(state_bounds.state_files, "FILES", {})
    monkeypatch.setattr(state_bounds.db_bounds, "under", lambda *_: [])
    state_bounds.sweep(now=now)
    assert not land.exists() and ship.exists()
    state_bounds.sweep(now=now + 5 * 3600)
    assert not ship.exists() and len(dump(cache.con)) == 8
    assert steps_store.sweep(now) == 0


def test_this_process_s_own_lock_wait_is_no_line_of_the_spool_it_takes_in(
    cache, tmp_path: Path, monkeypatch
) -> None:
    p = spool(tmp_path, LINES)
    monkeypatch.setenv("GOH_TIMINGS", str(p))
    inner = cache.seam.transaction

    @contextlib.contextmanager
    def waited():
        step_timings.record("begin", 1.0, 0, "db")  # lib/db_waits.py's, at BEGIN
        with inner() as con:
            yield con

    cache.seam.transaction = waited
    assert steps_store.ingest(p) and not p.exists()
    assert os.environ["GOH_TIMINGS"] == str(p) and len(dump(cache.con)) == 4


def test_a_land_s_rows_are_taken_in_by_its_exit(tmp_path: Path) -> None:
    """tools/land.sh's own function, its python a recorder: the file is moved, then taken in, and a
    python that fails leaves it and the land's status alone."""
    src, keep, said = tmp_path / "t", tmp_path / f"{RUN}.timings.jsonl", tmp_path / "said"
    src.write_text("{}\n")
    fake = tmp_path / "py"
    fake.write_text(f'#!/bin/sh\necho "$@" >>{said}\nexit 1\n')
    fake.chmod(0o755)
    code = (f'. "$GOH/lib/land_row.sh"; land_timings={src}; GOH_LAND_TIMINGS_KEEP={keep}; '
            'land_timings_keep')  # fmt: skip
    r = subprocess.run(["bash", "-c", code], capture_output=True, text=True, check=False,
                       env=hermetic_env(GOH=str(REPO_ROOT), GOH_PY=str(fake)),
                       stdin=subprocess.DEVNULL, timeout=patience(30))  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert keep.read_text() == "{}\n" and not src.exists()
    assert said.read_text().split() == [f"{REPO_ROOT}/lib/steps_store.py", "ingest", str(keep)]
    land = (REPO_ROOT / "tools" / "land.sh").read_text()
    assert "land_timings_keep " in land and '--spool "$GOH_LAND_TIMINGS_KEEP"' in land
