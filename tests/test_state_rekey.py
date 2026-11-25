"""lib/state_rekey.py (GOH.ABSDATA): a moved file is the file a writer of tokens makes, byte for
byte; what it refuses."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from pathlib import Path

import _body_fixture as bf
import goh_db
import kv_rows
import pid_owner
import pytest
import state_columns
import state_paths
import state_rekey
import state_shape
from _contract_fixture import NUMBERS, RUN, contracts, old_all, write_all

PUSH = {"repos": {"/r/goh": {"status": "pushed", "said": "s", "sweep": "/s/w.json", "where": "goh",
                             "log": "/s/w.log", "at": "2026-11-18T11:06:40+00:00"}},
        "schema": 1}  # fmt: skip
SWEEPS = {"schema": 1, "sweeps": [{"path": "/s/w.json", "repos": ["/r/goh", "/r/b"]}]}
PINS = {"repos": {"goh": {"status": "committed", "said": "s"}}, "schema": 2, "ruff": "1",
        "tag": "v1", "sweep": "/s/w.json"}  # fmt: skip


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> str:
    for var in ("GOH_STATE_ROOT", "GOH_REPOS_ROOT", "XDG_STATE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    return str(tmp_path / "h")


@contextlib.contextmanager
def transaction(con: sqlite3.Connection):
    con.execute("BEGIN")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def at(con: sqlite3.Connection, monkeypatch) -> sqlite3.Connection:
    monkeypatch.setattr(goh_db, "transaction", lambda: transaction(con))
    return con


def made(home: str, monkeypatch, token: bool, runs: bool = False) -> sqlite3.Connection:
    """Every store's rows with HOME's paths in them: written as tokens, or written whole by an older
    goh and contracted, the paths still absolute."""
    con = at(bf.memory() if token else bf.memory(bf.UNCUT), monkeypatch)
    if token:
        write_all(con, home, runs)
    else:
        old_all(con, home, runs)
        con.execute("UPDATE ships SET repo = ?", (home + "/r/goh",))
        for t in state_columns.TABLES:
            if t != "selection_misses":
                con.execute(f"UPDATE {t} SET body = '{{}}'")
        contracts(con, *NUMBERS)
    root = home + "/Projects"
    encode = state_paths.encode if token else str
    for name, whole in (("pin_push", PUSH), ("pin_sweeps", SWEEPS), ("wf_pin.pins", PINS)):
        doc = json.loads(
            json.dumps(whole).replace('"/r/', f'"{root}/').replace('"/s', f'"{home}/s')
        )
        kv_rows.put(con, name, state_paths.put(name, doc) if token else doc)
    doc = {"root": root, "registry": home + "/i"}
    text = json.dumps(state_paths.put("estate_audit", doc) if token else doc, sort_keys=True)
    con.execute("INSERT INTO kv(name, body, at) VALUES ('estate_audit', ?, 5)", (text,))
    for t in ("wf_pins", "pin_sweep_repos", "pin_pushes"):  # their writer's clock
        con.execute(f"UPDATE {t} SET at = 5")
    log = [encode(f"{home}/.local/state/goh/{x}") for x in ("slot", "a.jsonl")]
    con.execute("INSERT INTO slot_owners VALUES (?, 0, 't', 1, NULL, 5)", log[:1])
    con.execute("INSERT INTO imports(store, source, sha256, rows, at) VALUES ('s', ?, '', 1, 5)",
                log[1:])  # fmt: skip
    return con


def rows(con: sqlite3.Connection) -> dict[str, list]:
    tables = [t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND "
                                        "name NOT LIKE 'sqlite_%'").fetchall()]  # fmt: skip
    return {t: sorted(con.execute(f'SELECT * FROM "{t}"').fetchall(), key=repr) for t in tables
            if t not in ("meta", "schema_version")}  # fmt: skip


def test_a_moved_file_is_the_file_a_writer_of_tokens_makes(home, monkeypatch) -> None:
    want = rows(made(home, monkeypatch, token=True))
    con = made(home, monkeypatch, token=False)
    was = rows(con)
    moved = state_rekey.run()
    assert {t: (was[t], r) for t, r in rows(con).items() if r != want[t]} == {}
    assert state_rekey.left(con) == {}
    assert state_rekey.prose(con) == {"spawn_lists.value": 1}  # scratch_loose: text
    assert {c for c in moved if not c.endswith(".queue")} >= {
        "ships.result", "lands.repo", "flakes.repo", "pin_pushes.log", "kv.body", "imports.source",
        "slot_owners.repo_key", "land_lists.value", "ship_steps.timings"}  # fmt: skip
    assert state_rekey.run() == {}  # again: nothing to do


def test_every_path_field_a_store_names_is_a_column_moved(home, monkeypatch) -> None:
    """A field added to lib/state_paths.py with no column here would stay absolute in a moved file:
    written as a token and moved from the absolute form, the two files differ. The requests too,
    which move where they wait."""
    bare = made(home, monkeypatch, token=True, runs=True)
    moved = made(home, monkeypatch, token=False, runs=True)
    assert home in json.dumps(rows(moved)["requests"])
    state_rekey.run()
    for t in ("requests", "early", "request_nodes", "request_cov_files"):
        assert rows(moved)[t] == rows(bare)[t], t


def test_what_it_refuses(home, monkeypatch) -> None:
    con = at(bf.memory(bf.UNCUT), monkeypatch)
    contracts(con, 17, 18)
    was = rows(con)
    with pytest.raises(state_rekey.RekeyRefused, match=r"contracts \[19, 20, 21\] are not applied"):
        state_rekey.run()
    assert rows(con) == was
    con = made(home, monkeypatch, token=False, runs=True)
    con.execute("UPDATE ships SET status = 'bisecting', pid = ?, pid_stamp = ?",
                (os.getpid(), pid_owner.stamp_line(os.getpid())))  # fmt: skip
    with pytest.raises(state_rekey.RekeyRefused, match="ships still running: 1"):
        state_rekey.run()
    con.execute("UPDATE ships SET status = 'ok'")
    for queue in (home + "/q2", "$HOMEDIR/q2"):
        con.execute("INSERT INTO lands(queue, run, at, rest) VALUES (?, ?, 5, '{}')", (queue, RUN))
    was = rows(con)
    with pytest.raises(state_rekey.RekeyRefused, match=r"lands.queue: a row is there under both"):
        state_rekey.run()
    assert rows(con) == was and not state_paths.tokens(con)


def test_what_is_left_and_what_is_only_said(home, monkeypatch) -> None:
    con = made(home, monkeypatch, token=False)
    state_rekey.run()
    con.execute("UPDATE lands SET log = ?, why = ?", (f"{home}/x.log", f"see {home}/x.log"))
    assert state_rekey.portable(con) == (
        ["lands.log: 1 cells hold a path of this host"],
        ["lands.why: 1 cells name one in their text",
         "spawn_lists.value: 1 cells name one in their text"])  # fmt: skip
    assert state_shape.fence(con) is None
