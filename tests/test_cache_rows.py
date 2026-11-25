"""GOH.KVBLOBS: the cached parts of the kv documents as rows of goh-cache.db (lib/cache_rows.py),
through the modules that write and read them, beside an older copy that knows only the document."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cache_evict
import cache_rows
import canary_memo
import durations
import estate_registry
import goh_cache_db
import kv_fill
import pytest
import remote_rates
import small_stores
import state_paths
from _kv_fixture import Held, duration_kv, kv
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import agent_audit
import landed_verify

NOW = int(time.time())
REC = {"epoch": NOW - 60, "green": ["a"], "red": [], "sweep": {"ran_at": "t"}}
DEAD = {**REC, "epoch": NOW - canary_memo.TTL_S - 5}
RATES = {"mac_width": 7.5, "worker_width": 6.25, "overhead_s": 27.0, "ratio": 1.5,
         "tests": {"tests/a.py::t": 0.5, "tests/b.py::u[x/y]": 8.0}}  # fmt: skip


@pytest.fixture
def held(monkeypatch) -> Held:
    monkeypatch.delenv("GOH_TEST_DURATIONS", raising=False)
    return Held().lay(monkeypatch)


def estate(held: Held, **recs: object) -> Path:
    kv(held.goh, "estate_audit", json.dumps(recs))
    return Path("/st/estate_audit.json")


def test_the_memo_moves_out_of_the_state_and_reads_the_same(held) -> None:
    state = estate(held, release_canary={"memo_records": {"k": REC, "old": DEAD}})
    assert canary_memo.records(state, "release_canary") == {"k": REC, "old": DEAD}
    kept = canary_memo.keep(state, "release_canary", {"verdict": "clear"})  # it swept nothing
    assert kept == {"verdict": "clear"}
    estate(held, release_canary=kept)
    assert canary_memo.records(state, "release_canary") == {"k": REC}
    assert canary_memo.records(state, "grace_watch") == {}
    new = {**REC, "epoch": NOW}
    rec = {"memo_records": {"j": new, "bad": 1}, "ran": True}
    assert canary_memo.keep(state, "release_canary", rec) == {"ran": True}
    canary_memo.keep(state, "release_canary", {"memo_records": {"k": new}})  # another land's
    canary_memo.keep(state, "release_canary", {"memo_records": {"k": REC}})  # read long ago
    assert canary_memo.records(state, "release_canary") == {"j": new, "k": new}


def test_a_memo_row_no_run_reuses_goes_and_the_other_canarys_stays(held) -> None:
    state = estate(held)
    for kind, at in (("release", NOW - canary_memo.TTL_S), ("grace", 5)):
        held.cache.execute("INSERT INTO canary_memo VALUES (?, 'k', '{}', ?)", (kind, at))
    assert cache_rows.put_memo(state, "release", {})
    assert held.cache.execute("SELECT kind FROM canary_memo").fetchall() == [("grace",)]


def test_a_canary_whose_cache_is_busy_keeps_the_memo_in_its_record(held) -> None:
    state = estate(held, grace_watch={"memo_records": {"k": REC}})
    held.down = True
    kept = canary_memo.keep(state, "grace_watch", {"ran": False})
    assert kept == {"ran": False, "memo_records": {"k": REC}}


OBJS = {"/c/a": {"uses": 1.5, "at": 10.0, "l": 12.8167342079147}, "/c/b": {"uses": 1.0, "at": 11.0,
                                                                          "l": 0.0}}  # fmt: skip


def test_the_evictors_objects_are_rows_and_its_scalars_stay(held) -> None:
    led = "/st/cache-evict.json"
    assert cache_evict.read_ledger(led) == {"L": 0.0, "objs": {}}
    cache_evict.write_ledger(led, {"L": 3.5, "sccache_owed": 1, "objs": OBJS})
    assert json.loads(held.goh.execute("SELECT body FROM kv").fetchone()[0]) == {
        "L": 3.5, "sccache_owed": 1, "objs": {}}  # fmt: skip
    assert cache_evict.read_ledger(led) == {"L": 3.5, "sccache_owed": 1, "objs": OBJS}
    cache_evict.write_ledger(led, {"L": 4.0, "objs": {"/c/a": {**OBJS["/c/a"], "uses": 2.0}}})
    assert cache_evict.read_ledger(led)["objs"] == {"/c/a": {**OBJS["/c/a"], "uses": 2.0}}


def test_an_older_copys_ledger_is_joined_the_last_use_winning(held) -> None:
    led = "/st/cache-evict.json"
    cache_evict.write_ledger(led, {"L": 3.5, "objs": OBJS})
    theirs = {
        "/c/a": {"uses": 9.0, "at": 20.0, "l": 1.0},
        "/c/b": {"uses": 9.0, "at": 1.0, "l": 1.0},
    }
    kv(held.goh, "cache-evict", json.dumps({"L": 5.0, "objs": theirs}))
    got = cache_evict.read_ledger(led)
    assert got == {"L": 5.0, "objs": {"/c/a": theirs["/c/a"], "/c/b": OBJS["/c/b"]}}


def test_two_evictors_and_a_stale_upsert_take_nothing_back(held) -> None:
    later = {"/c/a": {"uses": 3.0, "at": 30.0, "l": 2.0}}
    assert cache_rows.put_objs("/st/x", later)
    assert cache_rows.put_objs("/st/x", {"/c/a": OBJS["/c/a"]})
    assert cache_rows.objs("/st/x") == later


@pytest.mark.parametrize("odd", [{"/c/a": {"uses": 1.0}}, {"/c/a": {"uses": "x", "at": 1, "l": 1}}])
def test_objects_the_rows_do_not_take_stay_in_the_document(held, odd) -> None:
    led = "/st/cache-evict.json"
    cache_evict.write_ledger(led, {"L": 1.0, "objs": odd})
    assert cache_evict.read_ledger(led) == {"L": 1.0, "objs": odd}


def test_a_busy_cache_leaves_the_objects_in_the_document(held) -> None:
    held.down = True
    cache_evict.write_ledger("/st/cache-evict.json", {"L": 1.0, "objs": OBJS})
    assert cache_evict.read_ledger("/st/cache-evict.json") == {"L": 1.0, "objs": OBJS}


def rates() -> dict:
    return vars(remote_rates.load_rates())


def test_the_rates_document_is_read_until_it_is_rows_and_paces(held) -> None:
    assert remote_rates.load_rates() == remote_rates.Rates() and not remote_rates.unfilled()
    remote_rates.fill()  # nothing to take: the defaults hide no document of a later fill
    duration_kv(held.goh, "remote_rates", json.dumps(RATES), 50.0)
    assert rates() == RATES and remote_rates.unfilled()
    held.down = True
    assert not remote_rates.fill() and rates() == RATES
    held.down = False
    assert remote_rates.fill() and remote_rates.fill() and rates() == RATES
    assert held.cache.execute("SELECT DISTINCT repo, at FROM remote_secs").fetchall() == [
        ("gates_of_heck", 50.0)]  # fmt: skip
    duration_kv(held.goh, "remote_rates", json.dumps({**RATES, "ratio": 9.0, "tests": {}}))
    assert rates() == RATES  # an older copy's fold is its own from here


def test_a_land_folds_its_paces_into_what_the_document_held(held) -> None:
    duration_kv(held.goh, "remote_rates", json.dumps(RATES), 50.0)

    def change(old: remote_rates.Rates) -> remote_rates.Rates:
        assert (old.ratio, old.tests) == (1.5, {})
        return remote_rates.Rates(old.mac_width, old.worker_width, old.overhead_s, 2.0)

    held.down = True
    remote_rates.put({"tests/a.py::t": 0.25}, change)
    assert rates() == RATES and remote_rates.unfilled()
    held.down = False
    remote_rates.put({"tests/a.py::t": 0.25}, change)
    assert rates() == {**RATES, "ratio": 2.0, "tests": {**RATES["tests"], "tests/a.py::t": 0.25}}
    assert cache_rows.put_secs({"tests/a.py::t": 99.0}, 50.0)  # a stale upsert
    assert rates()["tests"]["tests/a.py::t"] == 0.25
    assert durations.scalars(["remote_rates", "remote_ratio"]) == {"remote_ratio": 2.0}


def test_a_verified_report_is_asked_once_and_the_file_becomes_rows(
    held, tmp_path, monkeypatch
) -> None:
    file = tmp_path / "agent-verify.json"
    file.write_text(json.dumps({"s/t1": ["a", "b", "b"]}))
    monkeypatch.setattr(agent_audit, "_cache_path", lambda: file)
    monkeypatch.setattr(landed_verify, "git_repos", lambda _root: [])
    monkeypatch.setattr(agent_audit.estate_registry, "existing", list)
    asked = []

    def verify(report, _repos, _gh):
        asked.append(report)
        return landed_verify.Verdict("verified" if report == "ok" else "silent-failure", ["c"])

    monkeypatch.setattr(landed_verify, "verify", verify)
    recs = [{"event": "outcome", "status": "landed", "report": r, "session_id": "s",
             "tool_use_id": t} for r, t in (("x", "t1"), ("ok", "t2"), ("bad", "t3"))]  # fmt: skip
    got = agent_audit.verify_landed(recs, tmp_path, None)
    assert got[("s", "t1")].shas == ["a", "b", "b"] and asked == ["ok", "bad"]
    assert not file.exists()
    agent_audit.verify_landed(recs, tmp_path, None)
    assert asked == ["ok", "bad", "bad"]
    assert cache_rows.verified() == {"s/t1": ["a", "b", "b"], "s/t2": ["c"]}


def test_a_verified_file_stays_until_its_rows_read_back(held, tmp_path) -> None:
    file = tmp_path / "v.json"
    assert cache_rows.take_verified(file)  # none
    file.write_text("{")
    assert not cache_rows.take_verified(file) and file.exists()
    file.write_text(json.dumps({"s/t": ["a"]}))
    held.down = True
    assert not cache_rows.take_verified(file) and file.exists()
    held.down = False
    cache_rows.put_verified({"s/t": ["z"]})
    assert not cache_rows.take_verified(file) and file.exists()


def test_the_backfill_puts_every_part_in_its_rows_and_twice_adds_nothing(
    held, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    (tmp_path / "goh").mkdir()
    (tmp_path / "goh" / "agent-verify.json").write_text(json.dumps({"s/t": ["a", "b"]}))
    monkeypatch.setattr(estate_registry, "state_file", lambda: Path("/st/estate_audit.json"))
    monkeypatch.setattr(cache_evict, "state_path", lambda: "/st/cache-evict.json")
    estate(held, release_canary={"memo_records": {"k": REC, "old": DEAD}}, grace_watch={
        "memo_records": {"g": REC}})  # fmt: skip
    kv(held.goh, "cache-evict", json.dumps({"L": 3.5, "objs": OBJS}))
    duration_kv(held.goh, "remote_rates", json.dumps(RATES))
    assert len(kv_fill.left()) == 5
    n = {"remote_secs": 2, "canary_memo": 2, "evict_objs": 2, "verified_shas": 2}
    docs = held.goh.execute("SELECT * FROM kv").fetchall()
    assert n.items() <= kv_fill.run().items() and kv_fill.left() == []
    once = "\n".join(held.cache.iterdump())
    assert n.items() <= kv_fill.run().items() and "\n".join(held.cache.iterdump()) == once
    assert held.goh.execute("SELECT * FROM kv").fetchall() == docs  # they stay
    assert cache_evict.read_ledger("/st/cache-evict.json") == {"L": 3.5, "objs": OBJS}
    later = {**OBJS, "/c/a": {"uses": 4.0, "at": 40.0, "l": 1.0}, "/c/new": OBJS["/c/b"]}
    cache_rows.put_objs("/st/cache-evict.json", later)
    kv_fill.run()
    assert cache_rows.objs("/st/cache-evict.json") == later


def test_where_the_rows_are(tmp_path, monkeypatch) -> None:
    own = goh_cache_db.path()
    monkeypatch.delenv("GOH_CACHE_DB", raising=False)
    monkeypatch.delenv("GOH_TEST_DURATIONS", raising=False)
    here = tmp_path / "goh-cache.db"
    assert cache_rows.beside(tmp_path / "x.json") == (here, False)
    assert cache_rows.beside(state_paths.state_root() / "x.json") == (own, False)
    assert cache_rows.rates() == (own, False)
    monkeypatch.setenv("GOH_TEST_DURATIONS", str(tmp_path / "goh.db"))
    monkeypatch.setenv(cache_rows.KEPT_ENV, "/kept.db")
    assert cache_rows.rates() == (here, False)  # a test's own table is no real one
    monkeypatch.setattr(cache_rows.db_core, "real_dirs", lambda: [tmp_path.resolve()])
    assert cache_rows.rates() == (Path("/kept.db"), True)
    monkeypatch.delenv(cache_rows.KEPT_ENV)
    assert cache_rows.rates() == (here, False)
    monkeypatch.setenv("GOH_CACHE_DB", str(tmp_path / "named.db"))
    for got in (cache_rows.rates(), cache_rows.beside(tmp_path / "y" / "x.json")):
        assert got == (tmp_path / "named.db", False)


def test_the_cache_reads_as_no_rows_when_there_is_no_file(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("GOH_CACHE_DB", raising=False)
    assert cache_rows.objs(tmp_path / "x.json") == {} and not list(tmp_path.iterdir())
    assert capsys.readouterr().err == ""


def test_a_store_that_is_not_a_document_is_empty(held) -> None:
    small_stores.put("/st/cache-evict.json", [1])
    assert cache_evict.read_ledger("/st/cache-evict.json") == {"L": 0.0, "objs": {}}
    assert canary_memo.records(estate(held, release_canary=[1]), "release_canary") == {}
