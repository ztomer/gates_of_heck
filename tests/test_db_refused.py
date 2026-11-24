"""A goh.db write this copy may not make fails OPEN (GOH.HEADCOPYRECORD).

THE CASE (2026-11-24). A lane's head copy applied 0009 to the real goh.db before 0009 was on main.
Main's goh then opened the file read-only (db_core.NewerFile on a write), and scripts/
release_canary.py, which records its result in goh.db's kv through lib/estate_registry.py, RAISED
it after the canary had judged: every land not carrying 0009 went red on a record write.

Here goh.db is a stand-in at tmp_path holding a migration this goh does not ship. Every store
writer (a side record: the estate state, a commit stamp, a flake, a land ledger, a ship's pins)
says the write was refused on one line and returns; the release canary exits on its verdict, not
on the record. A land's or a ship's OWN record is no side record: it has no other home, so its
write is raised (tests/test_record_must.py, GOH.STATEDB S1).
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "lib"))
import estate_registry  # lib/, on the path from the line above
import goh_db
import land_records
import ship_records
import small_stores

SCRIPT = REPO_ROOT / "scripts" / "release_canary.py"


def _newer(db: Path) -> Path:
    """DB as a newer goh leaves it: every migration this goh ships, and 9999 that it does not."""
    with goh_db.transaction(db):
        pass
    con = sqlite3.connect(db)
    con.execute("INSERT INTO schema_version VALUES (9999, '9999_newer', 0, 'goh 9.9.9')")
    con.commit()
    con.close()
    return db


WRITERS = {
    "estate_registry.update_state": lambda d: estate_registry.update_state(
        d / "estate_audit.json", lambda old: {**old, "k": 1}
    ),
    "small_stores.put": lambda d: small_stores.put(d / "cache-evict.json", {"L": 1}),
    "small_stores.gated_add": lambda d: small_stores.gated_add(
        d / "commit-gated.jsonl", {"tree": "t", "repo": "/r", "at": 1.0}
    ),
    "small_stores.flake_add": lambda d: small_stores.flake_add(d / "flakes", "t", {"epoch": 1.0}),
    "land_records.append": lambda d: land_records.append(d / "q", "misses", [{"a": 1}]),
    "land_records.set_causes": lambda d: land_records.set_causes(d / "q", {"r.json": {}}),
    "ship_records.put_pins": lambda d: ship_records.put_pins(d / "r.json", {}),
}


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_a_store_writer_fails_open_on_a_file_it_may_not_write(name, tmp_path, monkeypatch, capsys):
    db = _newer(tmp_path / "goh.db")
    monkeypatch.setenv("GOH_DB", str(db))
    WRITERS[name](tmp_path)
    assert "9999_newer" in capsys.readouterr().err, f"{name} refused nothing, or said nothing"
    con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    try:
        assert 9999 in {r[0] for r in con.execute("SELECT version FROM schema_version")}
    finally:
        con.close()


def test_estate_update_returns_the_change_it_could_not_keep(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GOH_DB", str(_newer(tmp_path / "goh.db")))
    got = estate_registry.update_state(tmp_path / "estate_audit.json", lambda old: {**old, "k": 2})
    assert got == {"k": 2}


def test_the_release_canary_fails_open_on_a_newer_goh_db(tmp_path) -> None:
    """S4's land, 2026-11-24 00:45: `db_core.NewerFile: goh.db holds migration 0009_ships_v2 (by
    goh unknown)` from release_canary.py line 430, exit 1. The verdict is the canary's exit."""
    db = _newer(tmp_path / "goh.db")
    state = tmp_path / "state" / "estate_audit.json"
    state.parent.mkdir()
    (tmp_path / "estate").mkdir()
    argv = [sys.executable, str(SCRIPT), "--root", str(tmp_path / "estate"), "--state", str(state)]
    argv += ["--goh", str(REPO_ROOT), "--base", "HEAD"]
    r = subprocess.run(argv, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
                       env=hermetic_env(GOH_DB=str(db)))  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert "9999_newer" in r.stdout + r.stderr, "the refused record write was not said"
