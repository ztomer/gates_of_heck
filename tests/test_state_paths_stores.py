"""Every store of goh.db reads its rows in both forms (GOH.REPOKEY): rows written as today, then
moved to tokens and keys by SQL alone (never by the codec under test), read back equal; a file whose
`meta.paths` is `token` takes tokens from the same callers and reads back equal too."""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import goh_db
import hook_ledgers
import land_records
import land_slot
import landq_state
import main_reading
import reuse_audit_store
import ship_records
import small_stores

RUN = "01795000000000000000-X.ONE"


class Host:
    def __init__(self, at: Path) -> None:
        self.state, self.repos = at / "state", at / "src"
        self.q = self.state / "landq"
        self.q.mkdir(parents=True)
        self.repo = str(self.repos / "group" / "app")

    def land(self) -> dict:
        return {"repo": self.repo, "state": "landed", "item": "X.ONE", "why": "see $STATE/x",
                "log": f"{self.q}/logs/a.log", "red_logs": [f"{self.q}/logs/b.log"]}  # fmt: skip

    def request(self) -> dict:
        return {"item": "X.ONE", "repo": self.repo, "sha": "a" * 40, "mode": "land",
                "worktree": f"{self.q}/slots/app/0", "lane": f"{self.state}/lane",
                "pre_keep": f"{self.q}/pre/x.tgz"}  # fmt: skip


def lands(h: Host) -> Callable[[], Any]:
    land_records.write(h.q, RUN, h.land())
    land_records.set_causes(h.q, {f"{RUN}.json": {"cause": "test"}})
    land_records.append(h.q, "guards", [{"guard": "g", "repo": h.repo, "at": "2026-11-24"}])
    land_records.append(h.q, "misses", [{"day": "2026-11-24", "test": "t"}])
    return lambda: [land_records.records(h.q), land_records.get(h.q, RUN), land_records.runs(h.q),
                    land_records.causes(h.q), land_records.ledger(h.q, "guards"),
                    land_records.ledger(h.q, "misses")]  # fmt: skip


def requests(h: Host) -> Callable[[], Any]:
    p = landq_state.put(h.q, RUN, h.request())
    e = landq_state.put(h.q, f"{RUN}2", h.request(), landq_state.EARLY)
    return lambda: [landq_state.get(p), landq_state.get(e), landq_state.state_of(h.q, RUN),
                    landq_state.rows(h.q, "pending"), landq_state.listing(h.q, "pending"),
                    landq_state.listing(h.q, "early"), landq_state.any_in(h.q, "pending"),
    ]  # fmt: skip


def slots(h: Host) -> Callable[[], Any]:
    owner = h.q / "slots" / "app-1" / "0.owner"
    assert land_slot._take(h.q, owner, "tok")
    return lambda: [land_slot._owner(owner), land_slot.owners(h.q), land_slot.presteps(h.q)]


def readings(h: Host) -> Callable[[], Any]:
    main_reading.record(h.repo, "s", "probe", ["t::a"], ["t::a"])
    return lambda: [main_reading.red_at(h.repo, "s"), main_reading.covered(h.repo, "s", ["t::a"])]


def ships(h: Host) -> Callable[[], Any]:
    d = h.state / "ship" / "group_app"
    at = str(d / "20261124T000000Z-aaaaaaaa.json")
    sweep = f"{d}/x.sweep.json"
    res = {"result": at, "repo": h.repo, "status": "running", "tail": [f"said {d}/x.log $STATE/y"],
           "loopctl": f"{h.repos}/.loop/tools/loopctl.py", "verdicts": f"{d}/x.verdicts",
           "before_clock": {"log": f"{d}/l.log"}, "consumer_pins": {"sweep": sweep},
           "grace_over": {"repos": {"group/app": ["r"]}},
           "steps": [{"name": "push", "log": f"{d}/p.log", "timings": f"{d}/p.jsonl"}]}  # fmt: skip
    ship_records.put(res)
    ship_records.put_pins(at, {"tag": "v1", "repos": {"app": "ok"}, "sweep": sweep})
    return lambda: [ship_records.get(at), ship_records.get_pins(at), ship_records.in_dir(d),
                    ship_records.live(), ship_records.repo_dirs(), ship_records.every(),
                    ship_records.test_rows((str(d),))]  # fmt: skip


def small(h: Host) -> Callable[[], Any]:
    s = h.state
    small_stores.put(s / "pin_push.json", {"repos": {h.repo: {"sweep": f"{s}/a.json"}}})
    small_stores.put(s / "pin_sweeps.json", {"sweeps": [{"path": f"{s}/a", "repos": [h.repo]}]})
    small_stores.put(s / "wf_pin.pins.json", {"repos": {"app": "ok"}, "sweep": f"{s}/a.json"})
    small_stores.put(s / "estate_audit.json", {"root": str(h.repos), "registry": f"{s}/i",
                                               "problems": ["$STATE/a is gone"]})  # fmt: skip
    small_stores.gated_add(s / "commit-gated.jsonl", {"tree": "t", "repo": h.repo, "at": 1.0})
    small_stores.flake_add(s / "flakes", "t::a", {"id": "1", "repo": h.repo, "epoch": 1.0,
                           "log": {"first": f"{s}/1.log", "second": f"{s}/2.log"}})  # fmt: skip
    return lambda: small_stores.export(s)


def ledgers(h: Host) -> Callable[[], Any]:
    hook_ledgers.append("agent_spawns", {"event": "outcome", "time": 1.0,
                        "apps": {"before": [f"{h.repos}/A.app"]}, "report": "$STATE/x"},
    )  # fmt: skip
    reading = {"disk": {"path": f"{h.state}/c"}, "slots": {"dir": f"{h.state}/d"}}
    hook_ledgers.append("lane_starts", {"at": 1.0, "reading": reading})
    hook_ledgers.record_watch({"rules": f"{h.repos}/m.json"}, None)
    return lambda: [hook_ledgers.spawns(), hook_ledgers.lanes(), hook_ledgers.watch()]


def reuse(h: Host) -> Callable[[], Any]:
    was = {"evidence": {"fields": {"repo": f"{h.repo}/.git"}}}
    reuse_audit_store.put({"path": "landed", "enabled": {"was": was}, **was})
    reuse_audit_store.shadow_add({"path": "landed", "epoch": 1.0, **was["evidence"]})
    return lambda: [reuse_audit_store.get("landed"), reuse_audit_store.shadow()]


STORES = (lands, requests, slots, readings, ships, small, ledgers, reuse)
# the columns and fields that hold a repo's key in a file of tokens (the plan's S7 rekey)
KEYED = {"lands": "repo", "requests": "repo", "main_readings": "repo"}


@pytest.fixture
def host(monkeypatch, tmp_path: Path) -> Host:
    at = tmp_path.resolve()
    h = Host(at)
    monkeypatch.setenv("GOH_STATE_ROOT", str(h.state))
    monkeypatch.setenv("GOH_REPOS_ROOT", str(h.repos))
    for var in ("GOH_LANDQ_DIR", "GOH_SHIP_DIR", "GOH_REUSE_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GOH_DB", str(h.state / "goh.db"))
    return h


def cells() -> list[tuple[str, str, str]]:
    """(table, column, text) of every text cell of the file."""
    con = sqlite3.connect(goh_db.path())
    out = []
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        if t not in ("meta", "schema_version"):
            cur = con.execute(f"SELECT * FROM {t}")
            names = [c[0] for c in cur.description]
            out += [(t, c, v) for r in cur for c, v in zip(names, r, strict=True)
                    if isinstance(v, str)]  # fmt: skip
    con.close()
    return out


def to_tokens(h: Host) -> int:
    """Every path cell of the file as a token and every repo as its key, by SQL text alone."""
    con = sqlite3.connect(goh_db.path())
    n = 0
    for t, c, _ in {(t, c, "") for t, c, _ in cells()}:
        for old, new in ((f'"{h.repo}"', '"group/app"' if t in KEYED else '"$REPOS/group/app"'),
                         (f'"{h.state}/', '"$STATE/'), (f'"{h.repos}/', '"$REPOS/'),
                         (f'"{h.repos}"', '"$REPOS"')):  # fmt: skip
            n += con.execute(f"UPDATE {t} SET {c} = replace({c}, ?, ?) WHERE instr({c}, ?)",
                             (old, new, old)).rowcount  # fmt: skip
        for old, new in ((str(h.state), "$STATE"), (str(h.repos), "$REPOS")):
            if KEYED.get(t) == c:
                n += con.execute(
                    f"UPDATE {t} SET {c} = 'group/app' WHERE {c} = ?", (h.repo,)
                ).rowcount
            n += con.execute(f"UPDATE {t} SET {c} = ? || substr({c}, ?) "
                             f"WHERE substr({c}, 1, ?) = ?",
                             (new, len(old) + 1, len(old), old)).rowcount  # fmt: skip
    con.commit()
    con.close()
    return n


@pytest.mark.parametrize("store", STORES, ids=lambda s: s.__name__)
def test_planted_tokens_and_keys_read_back_as_the_absolute_rows_did(store, host: Host):
    read = store(host)
    was = read()
    assert to_tokens(host) > 0
    assert read() == was


@pytest.mark.parametrize("store", STORES, ids=lambda s: s.__name__)
def test_a_file_of_tokens_takes_tokens_from_the_same_callers(store, host: Host, monkeypatch):
    read = store(host)
    was = read()
    monkeypatch.setenv("GOH_DB", str(host.state / "two" / "goh.db"))
    with goh_db.transaction() as con:
        con.execute("INSERT OR REPLACE INTO meta VALUES ('paths', 'token')")
    read = store(host)

    def stable(v: Any) -> Any:  # a slot's claim time is its own write's
        if isinstance(v, dict):
            return {k: stable(x) for k, x in v.items() if k != "at"}
        return [stable(x) for x in v] if isinstance(v, list | tuple) else v

    assert stable(read()) == stable(was)
    left = [c for c in cells() if str(host.state) in c[2] or str(host.repos) in c[2]]
    assert [c for c in left if "$STATE/y" not in c[2]] == []  # a ship's tail is text
    for t, c, v in cells():
        if (t, c) in KEYED.items() or (t, c) == ("ships", "repo"):
            assert v == "group/app", (t, c)


def test_a_request_lives_its_whole_life_on_a_row_of_tokens(host: Host):
    requests(host)
    to_tokens(host)
    path = landq_state.name(host.q, "pending", RUN)
    assert landq_state.update(path, {**host.request(), "mode": "push"})
    path = landq_state.move(path, "running")
    assert landq_state.get(path) == {**host.request(), "mode": "push"}
    land_records.write(host.q, RUN, host.land(), path)
    assert landq_state.state_of(host.q, RUN) is None
    assert landq_state.drop(landq_state.name(host.q, "early", f"{RUN}2"))
    assert landq_state.listing(host.q, "early") == []


def test_a_slot_claimed_in_tokens_is_held_and_freed(host: Host):
    slots(host)
    to_tokens(host)
    owner = host.q / "slots" / "app-1" / "0.owner"
    assert not land_slot._take(host.q, owner, "other")
    assert land_slot._take(host.q, owner, "tok")
    land_slot._free(owner, "tok")
    assert land_slot.owners(host.q) == []


def test_a_record_rewritten_over_its_token_row_reads_as_the_new_one(host: Host):
    lands(host)
    ships(host)
    to_tokens(host)
    land_records.write(host.q, RUN, {**host.land(), "item": "X.TWO"})
    got = land_records.get(host.q, RUN)
    assert got is not None and got["item"] == "X.TWO"
    assert [r["item"] for _, r in land_records.records(host.q)] == ["X.TWO"]
    at = str(host.state / "ship/group_app/20261124T000000Z-aaaaaaaa.json")
    ship_records.put({"result": at, "repo": host.repo, "status": "ok"})
    rec = ship_records.get(at)
    assert rec is not None and rec["status"] == "ok"
    assert ship_records.get("$STATE/ship/group_app/20261124T000000Z-aaaaaaaa.json") == rec
    assert [r["status"] for _, r in ship_records.every()] == ["ok"]


def test_dropping_test_rows_drops_both_forms(host: Host):
    ships(host)
    to_tokens(host)
    assert ship_records.drop_test_rows((str(host.state / "ship/group_app"),)) == 0  # a real ship's
    con = sqlite3.connect(goh_db.path())
    con.execute("UPDATE ships SET result = '$STATE/tmp/x.json'")
    con.commit()
    con.close()
    assert ship_records.drop_test_rows((str(host.state / "tmp"),)) == 1
    assert ship_records.every() == []
