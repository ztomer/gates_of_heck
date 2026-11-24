"""The reuse audit's records are rows of goh.db (GOH.VERDICTDB V1, goh.db 0008, lib/
reuse_audit_store.py).

The audit is durable: a path's record that says OFF, lost, turns a wrong reuse back on (handover
plan (0) 4), so it moves into goh.db, not the disposable cache. Each calibration is the move's own
risk: a record or a sample still written as a file, a file from before the switch still read after
wave 3 (its import, read-back and archive are `goh state retire reuse-audit`'s, tests/
test_state_retire_verdicts.py), a test's audit leaking into another's, and SessionStart's reader
still reading the file.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import subprocess
import sys

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import reuse  # lib/, after its path
import reuse_audit
import reuse_audit_store as store_mod

FIELDS = {"repo": "/r/.git", "sha": "a" * 40, "identity": "i" * 64}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "store"))
    for name in ("GOH_REUSE_NOW", "GOH_DB", "GOH_LOOPCTL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl.py"))  # a fault is never filed
    return tmp_path / "store"


def _q(db, sql: str) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _off(path: str = "carry") -> None:
    """Turn PATH off through the audit's own door: a repeated disagreement."""
    p = reuse.PATHS[path]
    reuse_audit.compare(path, dict.fromkeys(p.fields, "x"), "green", "red",
                        again=lambda: ("green", "red"))  # fmt: skip


def test_a_record_and_a_sample_are_goh_db_rows_and_no_file(store, monkeypatch):
    monkeypatch.setitem(reuse.PATHS, "carry", dataclasses.replace(reuse.PATHS["carry"], every=1))
    _off()
    db = store / "goh.db"  # beside the named root, as reuse_store's goh-cache.db
    (body,) = _q(db, "SELECT body FROM reuse_audit WHERE path='carry'")[0]
    assert json.loads(body)["state"] == "off"
    assert [p for (p,) in _q(db, "SELECT path FROM reuse_shadow")] == ["carry"]
    assert not (store / "audit" / "carry.json").exists()
    assert not (store / "audit" / "shadow.jsonl").exists()
    assert 8 in {v for (v,) in _q(db, "SELECT version FROM schema_version")}


def test_a_file_from_before_the_switch_is_no_record_any_more(store):
    """Wave 3's delete commit: only goh.db rows are read. A record file left under reuse/audit is
    `goh state retire reuse-audit`'s to import, read back and archive (lib/state_retire.py), and
    the audit's flock is reuse/audit.lock, beside the directory that goes."""
    legacy = store / "audit"
    legacy.mkdir(parents=True)
    (legacy / "carry.json").write_text(json.dumps({"path": "carry", "state": "off"}))
    (legacy / "shadow.jsonl").write_text(json.dumps({"path": "carry", "epoch": 1}) + "\n")
    assert store_mod.get("carry") is None and store_mod.shadow() == []
    assert store_mod.lock_path() == store / "audit.lock"
    assert not hasattr(store_mod, "legacy_dir") and not hasattr(store_mod, "import_all")


def test_each_named_root_is_its_own_audit(tmp_path, monkeypatch):
    monkeypatch.delenv("GOH_DB", raising=False)
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "a"))
    assert store_mod.db() == tmp_path / "a" / "goh.db"
    monkeypatch.setenv("GOH_DB", str(tmp_path / "g.db"))
    assert store_mod.db() == tmp_path / "a" / "goh.db"  # the test's root wins over a suite's file
    monkeypatch.delenv("GOH_REUSE_DIR")
    assert store_mod.db() == tmp_path / "g.db"
    monkeypatch.setattr(store_mod, "NAMED", tmp_path / "caller.db")
    assert store_mod.db() == tmp_path / "caller.db"


def test_session_start_names_an_unfiled_off_from_the_database(store, tmp_path, monkeypatch):
    monkeypatch.setitem(reuse.PATHS, "carry", dataclasses.replace(reuse.PATHS["carry"], every=1))
    _off()
    assert not (store / "audit" / "carry.json").exists()
    hook = REPO_ROOT / "hooks" / "claude" / "estate_advisories.py"
    r = subprocess.run([sys.executable, str(hook)], input='{"source": "startup"}',
                       capture_output=True, text=True, check=False,
                       env={"GOH_ESTATE_STATE": str(tmp_path / "estate" / "audit.json"),
                            "GOH_REUSE_DIR": str(store), "PATH": "/usr/bin:/bin"})  # fmt: skip
    assert r.returncode == 0, r.stderr
    said = [ln for ln in r.stdout.splitlines() if "reuse audit" in ln]
    assert len(said) == 1 and "carry" in said[0] and "not filed" in said[0], r.stdout
