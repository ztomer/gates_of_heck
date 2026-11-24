"""lib/reuse.py's records are rows of goh-cache.db (GOH.VERDICTDB V1, lib/reuse_store.py).

~12k small files under ~/.local/state/goh/reuse, 200 MB of them verdicts, moved into the cache
file's `verdicts` table (migration 0002). Each calibration below is the move's own risk: a record
still written as a file, a file from before the switch read after its window closed (wave 3:
`goh state retire` imports them), a deleted cache that costs a verdict instead of a re-run, a path
that grows without bound, a database error that raises into a gate, and a suite that writes the
real file.
"""

from __future__ import annotations

import json
import sqlite3
import sys

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import db_core  # lib/, after its path
import reuse
import reuse_store

FIELDS = {"repo": "/r/.git", "tree": "t" * 40, "identity": "i" * 64}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "store"))
    for name in ("GOH_REUSE_TTL_S", "GOH_REUSE_NOW", "GOH_CACHE_DB"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "store"


def _rows(db) -> list[tuple]:
    with sqlite3.connect(db) as con:
        out = con.execute("SELECT path, key, verdict FROM verdicts").fetchall()
    con.close()
    return out


def test_a_record_is_a_row_of_goh_cache_db_and_no_file(store):
    loc = reuse.record("gate", FIELDS, "green", "test")
    db = store / "goh-cache.db"  # beside the named root, as lib/cpu_window.py's rule
    assert loc == f"{db}#gate/{reuse.key('gate', FIELDS)}"
    assert _rows(db) == [("gate", reuse.key("gate", FIELDS), "green")]
    assert not list(store.glob("gate/*/*.json"))  # no record file is written
    with sqlite3.connect(db) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] >= 2  # migration 0002 applied
    con.close()


def test_goh_cache_db_wins_over_the_named_root(store, tmp_path, monkeypatch):
    monkeypatch.setenv("GOH_CACHE_DB", str(tmp_path / "c.db"))
    reuse.record("gate", FIELDS, "green")
    assert _rows(tmp_path / "c.db") and not (store / "goh-cache.db").exists()


def test_a_file_from_before_the_switch_is_no_record_any_more(store):
    """Wave 3's delete commit: the files' window is closed. A record file left under the root is
    read by nothing (`goh state retire reuse-records` imports and removes them; lib/state_retire.py)
    and pruned by nothing: no lookup, no `records`, no prune sees it."""
    k = reuse.key("pushed", F2 := {"repo": "/r/.git", "tree": "t" * 40})
    old = store / "pushed" / k[:2] / f"{k}.json"
    old.parent.mkdir(parents=True)
    rec = {"format": reuse.FORMAT, "path": "pushed", "key": k, "fields": F2, "verdict": "green",
           "by": "push", "epoch": reuse.now()}  # fmt: skip
    old.write_text(json.dumps(rec))
    got, _ = reuse.lookup("pushed", F2)
    assert got is None and not (store / "goh-cache.db").exists()  # never read, never copied in
    stale = store / "gate" / "ab" / ("ab" + "0" * 62 + ".json")
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps({"epoch": reuse.now() - 2 * reuse.DAY_S}))
    assert list(reuse.records("gate")) == [] and reuse.prune("gate") == 0 and stale.exists()


def test_a_deleted_cache_is_a_miss_then_a_new_file(store):
    reuse.record("gate", FIELDS, "green")
    (store / "goh-cache.db").unlink()
    for side in ("-wal", "-shm"):
        (store / f"goh-cache.db{side}").unlink(missing_ok=True)
    rec, why = reuse.lookup("gate", FIELDS)
    assert rec is None and "no gate verdict" in why  # slower, never wrong
    reuse.record("gate", FIELDS, "green")
    assert reuse.lookup("gate", FIELDS)[0] is not None


def test_each_path_keeps_its_newest_rows_only(store, monkeypatch):
    monkeypatch.setattr(reuse_store, "CAP", 2)
    for n in range(4):
        monkeypatch.setenv("GOH_REUSE_NOW", str(1_000_000 + n))
        reuse.record("gate", {**FIELDS, "tree": str(n) * 40}, "green")
    reuse.record("pushed", {"repo": "/r/.git", "tree": "t" * 40}, "green")
    assert reuse.prune("gate") == 2
    trees = sorted(r[1]["fields"]["tree"][0] for r in reuse.records("gate"))
    assert trees == ["2", "3"]  # the newest two
    assert len(list(reuse.records("pushed"))) == 1  # another path's rows are its own


def test_a_database_error_is_a_miss_with_one_line(store, capsys):
    store.mkdir(parents=True)
    (store / "goh-cache.db").mkdir()  # a file that cannot be opened
    loc = reuse.record("gate", FIELDS, "green")
    rec, _ = reuse.lookup("gate", FIELDS)
    assert loc and rec is None
    assert "(a miss)" in capsys.readouterr().err


def test_a_suite_writes_the_real_file_only_when_its_runner_names_it(tmp_path, monkeypatch):
    real = tmp_path / "real"
    monkeypatch.setenv(db_core.REAL_HOME_ENV, str(real))
    monkeypatch.delenv(db_core.REAL_XDG_STATE_ENV, raising=False)
    monkeypatch.delenv("GOH_REUSE_DIR", raising=False)
    target = real / ".cache" / "goh" / "goh-cache.db"
    monkeypatch.setenv("GOH_CACHE_DB", str(target))
    monkeypatch.setattr(db_core, "of_record", lambda root=None: True)  # a lane's copy, as main's
    with pytest.raises(db_core.RealHomeOpen):
        reuse.record("gate", FIELDS, "green")
    monkeypatch.setattr(reuse_store, "NAMED", target)
    reuse.record("gate", FIELDS, "green")
    assert _rows(target)


def test_a_lane_copy_reads_an_unmigrated_shared_file_quietly_and_says_a_refusal_once(
    store, monkeypatch, capfd
):
    """A linked worktree never migrates the shared goh-cache.db (GOH.LANEMIGRATE): its reads of a
    table main has not made yet are silent misses, and its refused writes say so ONCE a process
    (the identity memo writes per miss; a line per write buried a gate's output)."""
    import goh_cache_db

    db = store / "goh-cache.db"
    store.mkdir(parents=True)
    con = sqlite3.connect(db)
    con.executescript(db_core.BOOT + "CREATE TABLE kv(name TEXT PRIMARY KEY, body TEXT, at REAL);"
                      "INSERT INTO schema_version VALUES (1, '0001_kv', 0, 'goh t');")  # fmt: skip
    con.close()
    monkeypatch.setattr(db_core, "of_record", lambda root=None: False)
    monkeypatch.setattr(db_core, "shared", lambda p, env=None: True)
    monkeypatch.setattr(goh_cache_db, "_REFUSED", set())
    capfd.readouterr()
    assert reuse.lookup("gate", FIELDS)[0] is None
    assert capfd.readouterr().err == ""  # no such table yet: a silent miss
    for _ in range(3):
        reuse.record("gate", FIELDS, "green")
    err = capfd.readouterr().err
    assert err.count("code of record") == 1, err
