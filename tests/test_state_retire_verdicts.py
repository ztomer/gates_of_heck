"""`goh state retire` for GOH.VERDICTDB's stores (lib/state_retire_verdicts.py, wave 3).

The reuse records' files are imported into goh-cache.db's `verdicts` and read back equal (or the row
is newer), then deleted with no archive (a cache the rows now hold); the reuse audit's files go into
goh.db and are archived (durable: an OFF lost turns a wrong reuse back on); the pure caches (parse
findings, the shell verdicts, the identity memo) are dropped, unread. Every emptied directory goes,
so `goh state` names none of them, and `verdict-caches` retires them all.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import reuse  # lib/, after its path
import reuse_audit_store
import reuse_store
import state_retire as sr

DAY = "2026-11-24"
F = {"repo": "/r/.git", "tree": "t" * 40, "identity": "i" * 64}


@pytest.fixture
def home(monkeypatch, tmp_path: Path) -> Path:
    for var, sub in (("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state")):
        (tmp_path / sub / "goh").mkdir(parents=True)
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("GOH_DB", str(tmp_path / "state" / "goh" / "goh.db"))
    monkeypatch.setenv("GOH_CACHE_DB", str(tmp_path / "cache" / "goh" / "goh-cache.db"))
    for name in ("GOH_REUSE_DIR", "GOH_REUSE_NOW"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _record(home: Path, path: str, fields: dict, verdict: str, epoch: int) -> Path:
    k = reuse.key(path, fields)
    f = home / "state" / "goh" / "reuse" / path / k[:2] / f"{k}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    rec = {"format": reuse.FORMAT, "path": path, "key": k, "fields": fields, "verdict": verdict,
           "by": "t", "epoch": epoch}  # fmt: skip
    f.write_text(json.dumps(rec))
    return f


def test_the_reuse_records_are_imported_read_back_and_dropped(home: Path) -> None:
    now = reuse.now()
    reuse.record("gate", F, "green")  # a row since the switch, newer than its file below
    newer = _record(home, "gate", F, "red", now - 60)
    pushed = _record(home, "pushed", {"repo": "/r/.git", "tree": "p" * 40}, "green", now)
    bad = home / "state" / "goh" / "reuse" / "carry" / "ab" / "ab.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{torn")  # no record: its readers read it as a miss
    (home / "state" / "goh" / "reuse" / "profstore").mkdir()  # not a verdict store: stays
    said: list[str] = []
    assert sr.retire("reuse-records", day=DAY, say=said.append) is True, said
    assert reuse.lookup("pushed", {"repo": "/r/.git", "tree": "p" * 40})[0] is not None
    assert reuse.lookup("gate", F)[0]["verdict"] == "green"  # the newer row kept
    root = home / "state" / "goh" / "reuse"
    assert sorted(p.name for p in root.iterdir()) == ["profstore"], "every emptied dir went"
    assert not newer.exists() and not pushed.exists() and not bad.exists()
    assert not (home / "state" / "goh" / "retired").exists(), "a cache's files are not archived"
    assert "3 files read back equal, dropped" in said[-1], said
    assert sr.stray() == []


def test_a_file_newer_than_its_differing_row_keeps_every_file(home: Path) -> None:
    reuse.record("gate", F, "green")
    db = home / "cache" / "goh" / "goh-cache.db"
    con = sqlite3.connect(db)
    with con:
        con.execute("UPDATE verdicts SET epoch=1")  # the row is older than the file's record
    con.close()
    f = _record(home, "gate", F, "red", reuse.now())
    said: list[str] = []
    assert sr.retire("reuse-records", day=DAY, say=said.append) is False
    assert f.exists() and "NOT retired" in said[0], said


def test_the_reuse_audit_goes_into_goh_db_archived(home: Path) -> None:
    audit = home / "state" / "goh" / "reuse" / "audit"
    (audit / "pending").mkdir(parents=True)  # an empty directory an older audit left
    (audit / ".lock").write_text("")
    off = {"path": "test", "state": "off", "window": {"start": 1}, "closed": []}
    (audit / "test.json").write_text(json.dumps(off, sort_keys=True, indent=1))
    lines = [{"path": "carry", "epoch": 10 + i, "outcome": "agreed"} for i in range(3)]
    (audit / "shadow.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines) + "{torn")
    reuse_audit_store.shadow_add(lines[0])  # a sample already a row: not imported twice
    said: list[str] = []
    assert sr.retire("reuse-audit", day=DAY, say=said.append) is True, said
    assert reuse_audit_store.get("test") == off
    assert reuse_audit_store.shadow() == lines
    assert not audit.exists()
    to = home / "state" / "goh" / "retired" / DAY / "reuse-audit" / "state" / "reuse" / "audit"
    assert sorted(p.name for p in to.iterdir()) == ["shadow.jsonl", "test.json"]
    assert sr.retire("reuse-audit", day=DAY, say=said.append) is True
    assert said[-1] == "reuse-audit: no file left to retire"


def test_the_pure_caches_are_dropped_unread(home: Path) -> None:
    state, cache = home / "state" / "goh", home / "cache" / "goh"
    files = [state / "parse-findings" / "00" / "a.json",
             state / "self-walk-verdicts" / "shell-lint" / "00" / "b.json",
             state / "tree-lint-verdicts" / "shellcheck-version" / "c.json",
             cache / "verdicts" / "shell-lint" / "ff" / "d.json",
             cache / "identity" / "ls-e.json"]  # fmt: skip
    for f in files:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}")
    (cache / "step-control").mkdir()  # a cargo workspace, not a verdict cache: stays
    assert sr.main(["retire", "--dry-run", "verdict-caches"]) == 0
    assert all(f.exists() for f in files)
    assert sr.GROUPS["verdict-caches"] == ("reuse-records", "reuse-audit", "parse-findings",
                                           "shell-verdicts", "identity-memo")  # fmt: skip
    said: list[str] = []
    assert all(sr.retire(n, day=DAY, say=said.append) for n in sr.GROUPS["verdict-caches"])
    assert "shell-verdicts: 3 files dropped unread, with no archive (a cache" in said[3], said
    assert not any(f.exists() for f in files) and sr.stray() == []
    assert sorted(p.name for p in cache.iterdir() if p.is_dir()) == ["step-control"]
    for gone in ("parse-findings", "self-walk-verdicts", "tree-lint-verdicts"):
        assert not (state / gone).exists()
    assert not (state / "retired").exists()


def test_the_record_files_are_read_by_nothing_but_the_retire(home: Path) -> None:
    f = _record(home, "pushed", {"repo": "/r/.git", "tree": "q" * 40}, "green", reuse.now())
    assert reuse.lookup("pushed", {"repo": "/r/.git", "tree": "q" * 40})[0] is None
    assert sr.stray() == [f]
    assert reuse_store.root() == home / "state" / "goh" / "reuse"
