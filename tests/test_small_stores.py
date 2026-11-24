"""goh's small stores in goh.db 0004 (GOH.STATEDBS3): every store's file, imported, reads back equal
from its table (handover plan (b) step 3), a re-import adds nothing, a file not yet imported is
read under the database's rows, and a store's rows live in the goh.db beside its file."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import small_stores

TEST = "tests/test_x.py::test_y"
KEY = "deadbeef"


def _state(d: Path) -> Path:
    """A state directory shaped like today's live one (2026-11-23), small."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "estate_audit.json").write_text(
        json.dumps(
            {"repos": {"/p/a": {"findings": ["x"]}}, "release_canary": {"verdict": "ok"}}, indent=2
        )
    )
    (d / "pin_push.json").write_text(
        json.dumps({"schema": 1, "repos": {"/p/a": {"where": "/p/a", "why": "behind"}}}, indent=1)
    )
    (d / "pin_sweeps.json").write_text(
        json.dumps({"schema": 1, "sweeps": [{"path": "/s/1", "repos": ["/p/a"]}]})
    )
    (d / "cache-evict.json").write_text(
        json.dumps({"L": 12.5, "objs": {"/c/o": {"uses": 2.0, "at": 1.0, "l": 0.0}}}, indent=1)
    )
    (d / "wf_pin.pins.json").write_text(json.dumps({"tag": "v1.0.0", "repos": {"/p/a": "ok"}}))
    (d / "commit-gated.jsonl").write_text(
        "".join(
            json.dumps(
                {"at": f"2026-11-22T21:2{i}:12Z", "repo": "/p/a", "tree": f"t{i}"}, sort_keys=True
            )
            + "\n"
            for i in range(3)
        )
        + "not json\n"
    )
    fl = d / "flakes"
    (fl / "by-test").mkdir(parents=True)
    recs = [
        {"id": f"{KEY}.{n}", "test": TEST, "epoch": 100.0 + n, "site": "s", "sha": "a"}
        for n in (1, 2)
    ]
    (fl / "register.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs))
    (fl / "by-test" / f"{KEY}.jsonl").write_text(
        "".join(json.dumps({"id": r["id"], "epoch": r["epoch"]}) + "\n" for r in recs)
        + json.dumps({"filed": "GOH.FLAKEDEADBEEF", "epoch": 102.0})
        + "\n"
    )
    return d


@pytest.fixture
def state(tmp_path, monkeypatch) -> Path:
    monkeypatch.delenv("GOH_DB", raising=False)
    return _state(tmp_path / "goh")


def test_every_store_reads_back_equal_after_its_import(state) -> None:
    before = small_stores.export(state)  # no database yet: the files, as their readers saw them
    assert not (state / "goh.db").exists()
    got = small_stores.import_all(state)
    assert got == {
        "estate_audit": 1,
        "pin_push": 1,
        "pin_sweeps": 1,
        "cache-evict": 1,
        "wf_pin.pins": 1,
        "commit_gated": 3,
        "flakes": 3,
    }, got
    for f in state.glob("*.json*"):
        f.unlink()  # the rows alone, no file under them
    import shutil

    shutil.rmtree(state / "flakes")
    assert small_stores.export(state) == before
    assert [r.get("filed") for r in small_stores.flakes(state / "flakes", TEST)] == [
        None,
        None,
        "GOH.FLAKEDEADBEEF",
    ]


def test_a_reimport_adds_nothing_and_a_grown_file_adds_its_new_lines(state) -> None:
    small_stores.import_all(state)
    assert set(small_stores.import_all(state).values()) == {0}
    with (state / "commit-gated.jsonl").open("a") as f:  # an old HEAD export still appending
        f.write(json.dumps({"at": "2026-11-23T00:00:00Z", "repo": "/p/b", "tree": "t9"}) + "\n")
    assert small_stores.import_all(state)["commit_gated"] == 1
    trees = [r["tree"] for r in small_stores.gated(state / "commit-gated.jsonl")]
    assert trees == ["t0", "t1", "t2", "t9"], trees


def test_a_file_not_yet_imported_reads_under_the_rows(state) -> None:
    small_stores.gated_add(state / "commit-gated.jsonl", {"at": 5, "repo": "/p", "tree": "new"})
    with (state / "commit-gated.jsonl").open("a") as f:
        f.write(json.dumps({"at": "2026-11-23T00:00:00Z", "repo": "/p/b", "tree": "late"}) + "\n")
    trees = [r["tree"] for r in small_stores.gated(state / "commit-gated.jsonl")]
    assert trees == ["late", "t0", "t1", "t2", "new"], trees


def test_a_kv_write_wins_over_its_file_and_is_read_alone(state) -> None:
    p = state / "pin_push.json"
    small_stores.update(p, lambda old: {**old, "repos": {}})
    p.write_text(json.dumps({"schema": 1, "repos": {"/stale": {}}}))  # an old writer, after
    assert small_stores.get(p) == {"schema": 1, "repos": {}}
    with sqlite3.connect(state / "goh.db") as con:
        names = sorted(r[0] for r in con.execute("SELECT name FROM kv"))
    assert names == ["pin_push"]  # a write imports its own file only


def test_the_database_is_beside_the_store_unless_goh_db_names_one(
    state, tmp_path, monkeypatch
) -> None:
    assert small_stores.db(state / "pin_push.json") == state / "goh.db"
    monkeypatch.setenv("GOH_DB", str(tmp_path / "named.db"))
    assert small_stores.db(state / "pin_push.json") == tmp_path / "named.db"
    small_stores.put(state / "pin_sweeps.json", {"sweeps": []})
    assert not (state / "goh.db").exists() and (tmp_path / "named.db").is_file()


def test_no_file_and_no_database_reads_as_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GOH_DB", raising=False)
    d = tmp_path / "empty"
    assert small_stores.get(d / "pin_push.json") is None
    assert small_stores.gated(d / "commit-gated.jsonl") == []
    assert small_stores.flakes(d / "flakes") == []
    assert not d.exists()  # a read never creates the database


def test_the_estate_state_is_written_to_goh_db_and_its_file_left_as_it_was(state) -> None:
    import estate_registry

    p = state / "estate_audit.json"
    was = p.read_text()
    new = estate_registry.update_state(p, lambda old: {**old, "grace_watch": {"ran": True}})
    assert p.read_text() == was, "the file stays until wave 3's delete commit, unwritten"
    assert estate_registry.read_state(p) == new == {**json.loads(was), "grace_watch": {"ran": True}}


def test_the_evictors_ledger_the_commit_stamps_and_the_flakes_write_goh_db(
    state, monkeypatch
) -> None:
    import cache_evict
    import commit_stamp
    import flakes

    monkeypatch.setenv("XDG_STATE_HOME", str(state.parent))
    monkeypatch.setenv("GOH_FLAKE_DIR", str(state / "flakes"))
    files = {p: p.read_bytes() for p in state.rglob("*") if p.is_file()}
    ledger = str(state / "cache-evict.json")
    cache_evict.write_ledger(ledger, {"L": 3.0, "objs": {}})
    commit_stamp.record("t-new", "/p/c")
    rec = flakes.record(TEST, "/p/a", "c" * 40, "site", load=1.0)
    assert {p: p.read_bytes() for p in state.rglob("*") if p.is_file() and p.suffix != ".lock"
            and not p.name.startswith("goh.db")} == files, "no store file is written"  # fmt: skip
    assert cache_evict.read_ledger(ledger) == {"L": 3.0, "objs": {}}
    assert {"t0", "t1", "t2", "t-new"} == commit_stamp.stamped()
    assert rec["id"] == f"{flakes.key(TEST)}.3", rec  # after the file's two records of TEST
    assert [r["id"] for r in flakes.recorded(TEST)][-1] == rec["id"]
