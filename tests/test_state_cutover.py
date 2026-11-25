"""lib/state_cutover.py (GOH.ABSDATA): the one command, killed between every two steps and run on;
what refuses it; the fence at its three readers; the purge. The kills run on one file, the rest in
memory."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import _body_fixture as bf
import db_contract
import goh_db
import hook_ledgers
import land_columns
import land_records
import landq_state
import landq_window
import pid_owner
import pytest
import selection_miss
import ship_records
import ship_status
import ship_verify
import state_columns
import state_cutover
import state_paths
import state_rekey
import state_shape
import state_strip
from _columns_fixture import AT, LAND, SHIP
from _contract_fixture import CANDIDATE, contracts, read_all, write_all
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

KILLED = -signal.SIGKILL


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> str:
    for var in ("GOH_STATE_ROOT", "GOH_REPOS_ROOT", "XDG_STATE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    monkeypatch.setenv("GOH_DB", str(tmp_path / "state" / "goh.db"))
    return str(tmp_path / "h")


def filled(home: str) -> sqlite3.Connection:
    """A file of documents: every store's rows, 450 spawns more, an `item` in no row, a red cause in
    no land, a value no column takes."""
    con = bf.memory()
    write_all(con, home, runs=False)
    spawn = json.dumps({"event": "spawn", "time": "2026-11-18T11:06:40+00:00", "tags": ["a"]})
    con.executemany("INSERT INTO agent_spawns(at, event, body) VALUES (?, 'spawn', ?)",
                    [(AT, spawn)] * 450)  # fmt: skip
    con.execute("UPDATE agent_spawns SET item = 'OLD.ITEM' WHERE n = 5")
    land = {k: v for k, v in LAND.items() if k not in ("red_cause", "cov_risk")}
    land_columns.put(con, home + "/q", "1-RED", {**land, "slot_s": "soon"}, AT)
    con.execute("INSERT INTO red_causes(queue, run, cause, at, body) VALUES (?, '1-RED', 'x', 1, "
                "?)", (home + "/q", json.dumps(LAND["red_cause"])))  # fmt: skip
    return con


@pytest.fixture
def con(home, monkeypatch) -> sqlite3.Connection:
    """`filled`, the one connection behind every door of goh_db."""
    con = filled(home)

    @contextlib.contextmanager
    def transaction(*_a, **_k):
        con.execute("BEGIN")
        try:
            yield con
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")

    def opened(*_a, **_k):
        return contextlib.nullcontext(con)

    for name in ("ro", "known", "connect"):
        monkeypatch.setattr(goh_db, name, opened)
    monkeypatch.setattr(goh_db, "transaction", transaction)
    monkeypatch.setattr(state_columns, "writable", lambda: None)
    monkeypatch.setattr(state_cutover, "vacuum", lambda: "in memory")

    def contract(_seam, n, _check):
        try:
            contracts(con, n)
        except sqlite3.IntegrityError as e:
            raise db_contract.ContractRefused(f"number {n} refuses: {e}") from e
        return Path(f"before-{n}")

    monkeypatch.setattr(db_contract, "contract", contract)
    monkeypatch.setattr(hook_ledgers, "dead", list)
    return con


@pytest.fixture
def letters(monkeypatch) -> list[tuple]:
    out: list[tuple] = []
    monkeypatch.setattr(state_shape, "bury", lambda *a: out.append(a))
    return out


def run() -> list[str]:
    said: list[str] = []
    assert state_cutover.run(said.append) == 0
    return said


def decoded(con: sqlite3.Connection) -> dict[str, list[str]]:
    """Every row as its reader hands it on, the paths of this host."""
    out = {}
    for name, got in read_all(con, os.environ["HOME"]).items():
        docs = [d[-1] if isinstance(d, tuple) and name != "commit_gated" else d for d in got]
        out[name] = [json.dumps(state_paths.got(name, d), sort_keys=True) for d in docs]
    assert len(out["lands"]) >= 2
    return out


def compared(home: str, after: sqlite3.Connection) -> tuple[dict, dict]:
    """(what there was before, what is in AFTER), but for what the cutover says it changes."""
    before = filled(home)
    land_columns.add_cause(before, home + "/q", "1-RED", LAND["red_cause"], lambda _: AT)
    for c in (before, after):
        c.execute("UPDATE lands SET rest = '{}'")
        c.execute("DELETE FROM spawn_lists WHERE value = 'OLD.ITEM'")
    with contextlib.closing(before):
        return decoded(before), decoded(after)


def test_the_whole_command_then_nothing_to_do(con, home, letters, monkeypatch) -> None:
    items = {n for (n,) in con.execute("SELECT n FROM agent_spawns WHERE item IS NOT NULL")}
    said = run()
    assert state_cutover.plan(con) in ([], ["vacuum"]) and state_shape.fence(con) is None
    assert [s for s in said if s.startswith(("fill", "bury", "promote"))] == [
        "fill: 1 red causes into their lands", "bury: 1 values into the dead-letter file",
        "promote: 1 spawns' items"]  # fmt: skip
    assert [(t, r["run"], r["slot_s"]) for t, r, _ in letters] == [("lands", "1-RED", "soon")]
    assert state_columns.check(con) == [] and state_rekey.left(con) == {}
    before, after = compared(home, con)
    assert after == before
    assert items and con.execute("SELECT count(*) FROM commit_gated").fetchone() == (1,)
    monkeypatch.setattr(state_cutover, "plan", lambda _: [])
    assert run() == ["done already: nothing to do"]


def refuses(match: str, con: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.OperationalError, match=match):
        state_cutover.run(lambda _: None)
    assert state_shape.fence(con) is None and not state_paths.tokens(con)


def test_what_is_in_flight_refuses_and_lifts_the_fence(con, home, letters) -> None:
    body = {b for (b,) in con.execute("SELECT body FROM lands")}
    landq_state.insert(con, home + "/q", "1795000000000000000-A.B", {"item": "A.B"}, "pending")
    refuses("in flight: requests: 1", con)
    con.execute("DELETE FROM requests")
    con.execute("UPDATE ships SET status = 'bisecting'")
    refuses("in flight: ships still running: 1", con)
    assert {b for (b,) in con.execute("SELECT body FROM lands")} == body and letters == []


@pytest.mark.parametrize(
    ("table", "sql"),
    [
        ("lands", "UPDATE lands SET pre_s = 99 WHERE run <> '1-RED'"),
        ("selection_misses", "UPDATE selection_misses SET test = 'other' WHERE test IS NOT NULL"),
    ],
)
def test_a_document_its_columns_do_not_give_back_refuses(con, letters, table, sql) -> None:
    con.execute(sql)
    refuses(f"1 documents stay: {table}: .* do not give back", con)
    assert "body" in state_shape.columns(con, table)


def test_a_contract_that_refuses_stops_it_and_the_next_run_goes_on(con, home, letters) -> None:
    real = db_contract.contract

    def late(_seam, n, check):  # a writer of the code before, between the strip and 0019
        if n == 19:
            doc = {"event": "spawn", "lane": "l", "time": "1970-01-01T00:00:01+00:00"}
            con.execute("INSERT INTO agent_spawns(at, event, body) VALUES (1, 'spawn', ?)",
                        (json.dumps(doc),))  # fmt: skip
        return real(_seam, n, check)

    db_contract.contract = late
    try:
        refuses("number 19 refuses", con)
    finally:
        db_contract.contract = real
    assert "body" not in state_shape.columns(con, "ships")
    assert con.execute("SELECT count(*) FROM agent_spawns WHERE lane = 'l'").fetchone() == (1,)
    run()
    assert state_cutover.plan(con) in ([], ["vacuum"])
    assert con.execute("SELECT count(*) FROM agent_spawns WHERE lane = 'l'").fetchone() == (1,)


def fenced(con: sqlite3.Connection, pid: str) -> None:
    con.execute("INSERT OR REPLACE INTO meta VALUES ('fence', ?)", (pid,))


def test_two_cutovers_a_dead_fence_and_the_abort(con, letters, capsys) -> None:
    other = pid_owner.stamp_line(os.getppid())
    fenced(con, str(other))
    assert state_cutover.main() == 2 and f"another cutover runs: pid {other}" in (
        capsys.readouterr().err)  # fmt: skip
    assert state_cutover.main(abort=True) == 1 and "its cutover runs" in capsys.readouterr().err
    assert state_shape.fence(con) == other
    fenced(con, "999999 Thu Jan  1 00:00:00 1970")
    assert state_shape.paused() and state_cutover.main(abort=True) == 0
    assert state_shape.paused() is None
    assert state_cutover.main(abort=True) == 1 and "no cutover's fence" in capsys.readouterr().err
    fenced(con, "999999 Thu Jan  1 00:00:00 1970")
    run()  # it takes a dead one's over
    assert state_shape.fence(con) is None


def test_the_fence_holds_the_lander_and_a_ship_and_no_hook(con, home, letters, monkeypatch) -> None:
    q = Path(home) / "q"
    landq_state.insert(con, str(q), "1795000000000000000-A.B",
                       {"item": "A.B", "mode": "ship", "repo": "/r"}, "pending")  # fmt: skip
    fenced(con, "999999 x")
    said: list[str] = []
    waits: list[float] = []

    def sleep(s: float) -> None:
        waits.append(s)
        if len(waits) == 3:
            con.execute("DELETE FROM meta WHERE key = 'fence'")

    got = landq_window.pending(q, said.append, sleep=sleep)
    assert [p.stem for p in got] == ["1795000000000000000-A.B"] and len(waits) == 3
    assert len(said) == 1 and "goh state cutover" in said[0] and "--abort" in said[0]
    assert ship_status.unfenced({"a": 1}, ValueError) == {"a": 1}
    fenced(con, "999999 x")
    with pytest.raises(ValueError, match="new lands and ships wait"):
        ship_status.unfenced({}, ValueError)
    red = {"status": "failed", "failed_step": "verify", "steps": []}
    assert not ship_verify.reopen(red, dry=True)
    con.execute("DELETE FROM meta WHERE key = 'fence'")
    assert ship_verify.reopen(red, dry=True)


def test_the_purge_only_after_its_contract(con, home, letters, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(goh_db, "path", lambda: Path(__file__))
    for t in ("lands", "land_cov_carry", "land_cov_files"):
        con.execute(f"UPDATE {t} SET at = {AT - 30 * 86400}")
    assert land_columns.purge() == 0
    con.execute("UPDATE lands SET body = json_set(body, '$.at', '2026-10-19T11:06:40Z')")
    run()
    count = "SELECT (SELECT count(*) FROM land_cov_carry) + (SELECT count(*) FROM land_cov_files)"
    assert con.execute(count).fetchone()[0] > 0
    monkeypatch.setattr(land_columns, "PURGE_DAYS", 10**6)
    (was,) = [r for r in land_columns.read(con, home + "/q").values() if "cov_risk" in r]
    assert land_columns.purge() > 0 and con.execute(count).fetchone() == (0,)
    monkeypatch.setattr(land_columns, "PURGE_DAYS", 14)
    (now,) = [r for r in land_columns.read(con, home + "/q").values() if "cov_risk" in r]
    assert {k for k in was["cov_risk"] if was["cov_risk"][k] != now["cov_risk"].get(k)} == {
        "carry", "files"}  # fmt: skip
    assert "carry" not in now["cov_risk"]  # a land whose lines are gone judged nothing
    monkeypatch.setattr(land_columns, "PURGE_DAYS", 10**6)
    (young,) = [r for r in land_columns.read(con, home + "/q").values() if "cov_risk" in r]
    assert young["cov_risk"]["carry"] == []


# -- the kills, on one file ------------------------------------------------------------------------

KILL = """
import contextlib, os, signal, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import goh_db, state_cutover, state_rekey, state_strip
at, left = sys.argv[3], [1]
def die(*_): os.kill(os.getpid(), signal.SIGKILL)
state_cutover.passed = lambda step: die() if step == at else None
if at.startswith("strip-"):
    batch, left[0], real = state_strip._batch, int(at[6:]), goh_db.transaction
    @contextlib.contextmanager
    def dying(*a):  # the batch's statements are done and its COMMIT is not
        with real(*a) as con:
            yield con
            die()
    def counted(*a):
        left[0] -= 1
        goh_db.transaction = goh_db.transaction if left[0] else dying
        return batch(*a)
    state_strip._batch = counted
if at == "in-rekey": state_rekey._kv = die
state_cutover.run(lambda said: print(said, flush=True))
"""
POINTS = ("fence", "fill", "strip-2", "strip-7", "strip", "bury", "promote", "17", "18", "19", "20",
          "in-rekey", "rekey")  # fmt: skip


def probe(q: Path, n: int) -> None:
    """What lands, hooks and ships do meanwhile, by this goh, on the file as a kill left it."""
    run = f"17950000000000000{n:02d}-A.B"
    path = landq_state.put(q, run, bf.LAND)
    assert landq_state.get(path) == bf.LAND and landq_state.update(
        path, {**bf.LAND, "red_last": "r2"}
    )
    path = landq_state.move(path, "running")
    land_records.write(q, run, {**LAND, "item": f"P.{n}"}, request=path)
    assert land_records.get(q, run)["item"] == f"P.{n}" and landq_state.get(path) is None
    assert hook_ledgers.append(hook_ledgers.SPAWNS, {"event": "spawn", "lane": f"l{n}"}) is None
    hook_ledgers._spill(hook_ledgers.LANES, {**bf.LANE, "item": f"S.{n}"})
    assert hook_ledgers.append(hook_ledgers.LANES, bf.LANE) is None
    ship = {**SHIP, "result": str(q.parent / f"r/goh/{n}.json"), "status": "ok"}
    ship_records.put(ship)
    assert ship_records.get(ship["result"])["steps"] == SHIP["steps"]
    miss = {**bf.MISS, "test": f"t::{n}"}
    assert [selection_miss.append(q, [miss]) for _ in "ab"] == [1, 0]


def test_killed_at_every_point_it_goes_on_and_loses_nothing(home) -> None:
    with contextlib.closing(filled(home)) as mem:
        goh_db.path().parent.mkdir(parents=True)
        goh_db.path().write_bytes(mem.serialize())
    q = Path(home) / "q2"
    env = hermetic_env(GOH_DB=str(goh_db.path()), HOME=home)
    seen: list[str] = []
    for n, at in enumerate(POINTS):
        args = [sys.executable, "-c", KILL, str(REPO_ROOT / "lib"), str(REPO_ROOT), at]
        got = subprocess.run(args, env=env, capture_output=True, text=True, timeout=patience(60),
                             stdin=subprocess.DEVNULL, check=False)  # fmt: skip
        assert got.returncode == KILLED, (at, got.stdout, got.stderr)
        assert state_shape.paused(), at
        probe(q, n)
        seen.append(at)
    assert tuple(seen) == POINTS
    with goh_db.ro() as con:
        assert state_paths.tokens(con)
    assert state_cutover.run(seen.append) == 0 and state_shape.paused() is None
    with goh_db.ro() as con:
        assert [x for x in state_columns.check(con) if "dead.jsonl" not in x] == []
        assert state_rekey.left(con) == {} and state_cutover.plan(con) in ([], ["vacuum"])
        assert con.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        ours = con.execute("SELECT count(*) FROM lands WHERE item LIKE 'P.%'").fetchone()[0]
        spilled = con.execute("SELECT count(*) FROM lane_starts WHERE item LIKE 'S.%'").fetchone()[
            0
        ]
        assert (ours, spilled) == (len(POINTS), len(POINTS))
        with contextlib.closing(sqlite3.connect(":memory:")) as copy:
            con.backup(copy)
            before, after = compared(home, copy)
        assert len(con.execute("SELECT 1 FROM agent_spawns").fetchall()) == 452 + len(POINTS)
        assert {"OLD.ITEM"} <= {v for (v,) in con.execute("SELECT value FROM spawn_lists")}
        red = con.execute("SELECT red_class FROM lands WHERE run = '1-RED'").fetchone()
    assert red == (LAND["red_cause"]["cause"],)
    assert {r["table"] for r in hook_ledgers.dead()} == {"lands"}
    assert hook_ledgers.candidates() == [CANDIDATE] and state_strip.KEPT
    lost = {t: [d for d in docs if d not in after[t]] for t, docs in before.items()}
    assert not any(lost.values()), lost
