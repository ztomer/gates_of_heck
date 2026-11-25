"""goh's small stores in goh.db 0004 (GOH.STATEDBS3): every store's file, retired (`goh state
retire small-stores`, wave 3), reads back equal from its table and is archived; no store reads its
file after that; and a store's rows live in the goh.db beside its path."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import small_stores

TEST = "tests/test_x.py::test_y"
KEY = "deadbeef"


PUSHED = {"at": "2026-11-23T04:55:42+00:00", "said": "behind", "status": "committed",
          "sweep": "/s/1", "where": "a"}  # fmt: skip
PINNED = {"/p/a": {"status": "committed", "said": "ok"}}
PINS = {"schema": 2, "tag": "v1.0.0", "ruff": "0.1", "repos": PINNED, "sweep": None}


def _state(d: Path) -> Path:
    """A state directory shaped like today's live one (2026-11-23), small."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "estate_audit.json").write_text(
        json.dumps(
            {"repos": {"/p/a": {"findings": ["x"]}}, "release_canary": {"verdict": "ok"}}, indent=2
        )
    )
    (d / "pin_push.json").write_text(json.dumps({"schema": 1, "repos": {"/p/a": PUSHED}}, indent=1))
    (d / "pin_sweeps.json").write_text(
        json.dumps({"schema": 1, "sweeps": [{"path": "/s/1", "repos": ["/p/a"]}]})
    )
    (d / "cache-evict.json").write_text(
        json.dumps({"L": 12.5, "objs": {"/c/o": {"uses": 2.0, "at": 1.0, "l": 0.0}}}, indent=1)
    )
    (d / "wf_pin.pins.json").write_text(json.dumps(PINS))
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


def test_every_store_retired_reads_back_equal_and_its_files_are_archived(
    state, monkeypatch
) -> None:
    import state_retire

    monkeypatch.setenv("XDG_STATE_HOME", str(state.parent))
    want = {f.name: json.loads(f.read_text()) for f in state.glob("*.json")}
    said: list[str] = []
    assert state_retire.retire("small-stores", day="d", say=said.append) is True, said
    left = [p for p in state.rglob("*") if p.is_file() and not p.name.startswith("goh.db")]
    to = state / "retired" / "d" / "small-stores" / "state"
    assert sorted(p.relative_to(to) for p in left) == sorted(
        p.relative_to(to) for p in to.rglob("*") if p.is_file()
    ), left  # every file archived, none left in place
    assert (to / "flakes" / "register.jsonl").is_file() and not (
        state / "flakes" / "register.jsonl"
    ).exists()
    got = small_stores.export(state)
    assert {f"{k}.json": v for k, v in got.items() if k in small_stores.KV_NAMES} == want
    assert [r["tree"] for r in got["commit_gated"]] == ["t0", "t1", "t2"]
    assert [r.get("filed") for r in small_stores.flakes(state / "flakes", TEST)] == [
        None,
        None,
        "GOH.FLAKEDEADBEEF",
    ]


def test_a_store_never_reads_its_file(state) -> None:
    p = state / "pin_push.json"
    assert small_stores.get(p) is None  # the file is there, and read by nothing
    assert small_stores.gated(state / "commit-gated.jsonl") == []
    assert small_stores.flakes(state / "flakes") == []
    seen: list[object] = []
    small_stores.update(p, lambda old: seen.append(old) or {"schema": 1, "repos": {"/p/a": PUSHED}})
    assert seen == [None] and small_stores.get(p)["repos"] == {"/p/a": PUSHED}


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


def test_the_estate_state_is_written_to_goh_db_and_its_file_left_alone(state) -> None:
    import estate_registry

    p = state / "estate_audit.json"
    was = p.read_text()
    new = estate_registry.update_state(p, lambda old: {**(old or {}), "grace_watch": {"ran": True}})
    assert p.read_text() == was, "the file is neither read nor written"
    assert estate_registry.read_state(p) == new == {"grace_watch": {"ran": True}}


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
    assert {"t-new"} == commit_stamp.stamped()  # the file's trees are read by nothing
    assert rec["id"] == f"{flakes.key(TEST)}.1", rec
    assert [r["id"] for r in flakes.recorded(TEST)][-1] == rec["id"]


def test_wf_pins_own_record_is_goh_dbs(state) -> None:
    """`goh wf-pin` run by no ship records into goh.db's `wf_pins` (beside the pin ledger),
    no sidecar file; a ship's record is lib/ship_records.py's (GOH.STATEDBS2)."""
    import consumer_pins

    where = state / "wf_pin.json"
    (state / "wf_pin.pins.json").unlink()
    consumer_pins.write_record(str(where), "v9.9.9", "0.1", PINNED, ship=False)
    assert not (state / "wf_pin.pins.json").exists()
    assert small_stores.get(state / "wf_pin.pins.json") == {
        "schema": 2, "tag": "v9.9.9", "ruff": "0.1", "repos": PINNED,
        "sweep": None}  # fmt: skip
