"""GOH.ONEGATE: the land judge (lib/land_judge_owe.py, lib/land_judge.py): the row a land owes, the
drain, what it says of its own failures and the report's rate. The database is in memory."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path

import goh_db
import land_judge as judge
import land_judge_owe
import land_judge_rows as rows
import pytest
from _kv_fixture import Held
from land_judge_rows import Step

REPO, A, B, BASE = "gates_of_heck", "a" * 40, "b" * 40, "0" * 40


@pytest.fixture
def held(monkeypatch, tmp_path) -> Held:
    monkeypatch.setattr(judge, "home", lambda: tmp_path / "judge")
    monkeypatch.setenv("GOH_LANDQ_DIR", str(tmp_path / "landq"))
    return Held().lay(monkeypatch)


def args(**kw) -> argparse.Namespace:
    said = {"main": Path("/m/gates_of_heck"), "repo": REPO, "tip": A, "base": BASE, "rc": 0,
            "changed": 1, "timings": None, "root": Path("/r"), "last": 20, "days": 14.0,
            "json": False}  # fmt: skip
    return argparse.Namespace(**{**said, **kw})


def land(tip: str = A) -> dict:
    (got,) = (x for x in rows.lands(REPO, 99, 0.0) if x["tip"] == tip)
    return got


def test_a_land_owes_a_row_and_a_suite_starts_no_judge(held, monkeypatch) -> None:
    started = []
    monkeypatch.setattr(land_judge_owe.subprocess, "Popen", lambda *a, **k: started.append((a, k)))
    assert land_judge_owe.owe(args()) == 0 and land()["status"] == "waiting" and not started
    assert land_judge_owe.owe(args(tip=B, changed=0)) == 0 and land(B)["status"] == "same"


def test_outside_a_suite_the_judging_is_a_session_of_its_own(held, monkeypatch) -> None:
    started = []
    monkeypatch.setattr(land_judge_owe.subprocess, "Popen", lambda *a, **k: started.append((a, k)))
    monkeypatch.setattr(land_judge_owe.full_run, "IN_SUITE", ())
    monkeypatch.setenv("GOH_HELD_LOCKS", "land:1:x")
    land_judge_owe.owe(args())
    (((argv,), kw),) = started
    assert argv[2:] == ["drain", "--main", "/m/gates_of_heck"] and kw["start_new_session"]
    assert "GOH_HELD_LOCKS" not in kw["env"]
    land_judge_owe.owe(args(tip=B, changed=0))
    assert len(started) == 1


def test_a_red_land_owes_the_steps_its_own_gate_had_red(held, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(land_judge_owe.steps, "declared", lambda _r: (["./a", "./b", "./c"], ""))
    spool = tmp_path / "t.jsonl"
    lines = [{"label": "./a", "rc": 1}, {"label": "./b", "rc": 1}, {"label": "./b", "rc": 0},
             {"label": "inner", "rc": 1}]  # fmt: skip
    spool.write_text("".join(json.dumps(x) + "\n" for x in lines) + "torn {\n")
    land_judge_owe.owe(args(rc=1, timings=str(spool)))
    assert rows.owed_steps((REPO, A, BASE)) == {"./a": 1} and land()["land_rc"] == 1
    land_judge_owe.owe(args(rc=1, tip=B, timings=str(tmp_path / "none")))
    assert [x["tip"] for x in rows.lands(REPO, 9, 0.0)] == [A]  # no step red: no verdict to pair


class Judged:
    """lib/land_judge_steps.py's judge, its answer given: what it was asked, after a 9 s wait."""

    def __init__(self, found) -> None:
        self.found, self.landed, self.estates = list(found), [], []

    def __call__(self, *args, landed: bool, **_kw) -> list[Step]:
        args[-1].waited = 9.0
        self.landed.append(landed)
        return self.found


def _wired(monkeypatch, found=(), prepare="", choose=None) -> Judged:
    monkeypatch.setattr(judge.full_run, "prepare", lambda *_: prepare)
    monkeypatch.setattr(judge, "export", lambda _m, rev: Path("/x") / rev)
    monkeypatch.setattr(judge.steps, "choose", choose or (lambda *_: []))
    monkeypatch.setattr(judge.steps, "judge", _judged := Judged(found))
    monkeypatch.setattr(judge, "estate", _judged.estates.append)
    return _judged


def test_a_judged_land_has_its_steps_and_names_what_it_found(held, monkeypatch, capsys) -> None:
    rows.owe((REPO, A, BASE), 0, True, {}, 1.0)
    _wired(monkeypatch, [Step("./s", "would_red", "✗ bad.txt", 1, 0), Step("./t", "green")])
    judge.one(args(), (REPO, A, BASE), 0, lambda: None)
    assert land()["status"] == "done" and [s["outcome"] for s in land()["steps"]] == [
        "would_red", "green"]  # fmt: skip
    said = capsys.readouterr().err
    assert "./s: would_red: ✗ bad.txt" in said and "./t" not in said


def test_a_land_is_judged_as_it_ended_its_slot_waits_not_in_its_seconds(held, monkeypatch) -> None:
    asked = _wired(monkeypatch)
    ticks = iter((100.0, 112.0))
    rows.owe((REPO, A, BASE), 1, True, {}, 1.0)
    judge.one(args(), (REPO, A, BASE), 1, lambda: None, lambda: next(ticks))
    assert land()["seconds"] == 3.0 and asked.landed == [False] and len(asked.estates) == 1


def test_a_green_land_is_judged_whole(held, monkeypatch) -> None:
    asked = _wired(monkeypatch)
    judge.one(args(), (REPO, A, BASE), 0, lambda: None)
    assert asked.landed == [True]


def test_the_canary_is_given_copies_and_keeps_its_verdict_there(monkeypatch, tmp_path) -> None:
    home, scratch = tmp_path / "home", tmp_path / "s"
    home.mkdir()
    scratch.mkdir()
    (home / "registry").write_text("/a/consumer\n")
    put = {}
    monkeypatch.delenv("GOH_DB", raising=False)
    monkeypatch.setattr(judge.estate_registry, "registry_path", lambda: home / "registry")
    monkeypatch.setattr(judge.estate_registry, "state_file", lambda: home / "state.json")
    monkeypatch.setattr(judge.small_stores, "get", lambda p: {"of": str(p)})
    monkeypatch.setattr(judge.small_stores, "put", put.__setitem__)
    judge.estate(scratch)
    assert (scratch / "registry").read_text() == "/a/consumer\n"
    assert put == {scratch / judge.steps.STATE: {"of": str(home / "state.json")}}
    monkeypatch.setenv("GOH_DB", "/named")  # one named database: there is no other to copy to
    put.clear()
    judge.estate(scratch)
    assert not put


@pytest.mark.parametrize("fault", ["tree", "choose"])
def test_its_own_failure_is_unavailable_by_its_reason(held, monkeypatch, capsys, fault) -> None:
    def broken(*_: object) -> list:
        raise RuntimeError("the judge is broken")

    rows.owe((REPO, A, BASE), 0, True, {}, 1.0)
    _wired(monkeypatch, prepare="no disk" if fault == "tree" else "", choose=broken)
    judge.one(args(), (REPO, A, BASE), 0, lambda: None)
    want = "no disk" if fault == "tree" else "the judge is broken"
    assert land()["status"] == "unavailable" and want in land()["why"]
    assert want in capsys.readouterr().err


def _drain(seen: list):
    found = [Step("./s", "green", None, 0, 0)]

    def run(_a: object, key: rows.Key, rc: int, _wait: object) -> None:
        seen.append((key[1], rc))
        rows.end(key, found, "", 1.0, 50.0)

    return run


def test_a_drain_judges_every_waiting_land_oldest_first(held) -> None:
    for i, tip in enumerate((A, B)):
        rows.owe((REPO, tip, BASE), i, True, {}, float(i))
    judge.drain(args(), _drain(seen := []), lambda: 100.0)
    assert seen == [(A, 0), (B, 1)]


def test_a_drain_with_nothing_waiting_still_settles_a_dead_judge(held) -> None:
    rows.owe((REPO, A, BASE), 0, True, {}, 1.0)
    rows.begin((REPO, A, BASE), 2.0)
    judge.drain(args(), lambda *_: pytest.fail("nothing waits"), lambda: 100.0)
    assert land()["status"] == "unavailable"


def test_a_full_sample_stops_the_drain_until_the_report_is_read(held, capsys, monkeypatch) -> None:
    monkeypatch.setenv(judge.SAMPLE, "2")
    tips = [t * 40 for t in "abcd"]
    for i, tip in enumerate(tips):
        rows.owe((REPO, tip, BASE), 0, True, {}, float(i))
    judge.drain(args(), _drain(seen := []), lambda: 100.0)
    assert [t for t, _ in seen] == tips[:2]
    assert [(land(t)["status"], land(t)["why"]) for t in tips[2:]] == [
        ("unjudged", "the sample is full")] * 2  # fmt: skip
    rc, said = _report(capsys)
    assert rc == 0 and "the sample is full: 2 of 2 judged" in said
    assert "land_judge.py continue" in said and "2 lands unjudged: the sample is full" in said
    assert judge.go_on(args(), lambda: 60.0) == 0
    rows.owe((REPO, "e" * 40, BASE), 0, True, {}, 70.0)
    judge.drain(args(), _drain(seen), lambda: 100.0)
    assert seen[-1][0] == "e" * 40 and len(seen) == 3
    rc, said = _report(capsys)
    assert "sample: 0 of 2" in said and "the sample is full: " not in said


@pytest.mark.parametrize(("raw", "size"), [("", 20), ("7", 7), ("0", 0), ("x", 20), ("-1", 20)])
def test_the_samples_size_is_declared(monkeypatch, capsys, raw, size) -> None:
    monkeypatch.setenv(judge.SAMPLE, raw)
    assert judge.sample() == size
    assert ("no whole number" in capsys.readouterr().err) == (raw in ("x", "-1"))


def test_a_land_past_its_bound_is_unjudged_and_not_run(held) -> None:
    rows.owe((REPO, A, BASE), 0, True, {}, 0.0)
    rows.owe((REPO, B, BASE), 0, True, {}, 10.0)
    judge.drain(args(), _drain(seen := []), lambda: judge.AGE_S + 5)
    assert seen == [(B, 0)] and (land()["status"], land()["why"]) == ("unjudged", "no room")


def test_a_lands_wait_is_said_in_its_row_and_bounded_by_its_own_age(held, monkeypatch) -> None:
    rows.owe((REPO, A, BASE), 0, True, {}, 10.0)
    waits = []
    judge.drain(args(), lambda _a, key, _rc, wait: (waits.append(wait), rows.unjudge(key, "x")),
                lambda: 100.0)  # fmt: skip
    (wait,) = waits
    assert wait.until == 10.0 + judge.AGE_S
    wait.told("CPU 1% idle")
    assert (land()["status"], land()["why"]) == ("waiting", "no room: CPU 1% idle")
    wait.told("")
    assert land()["status"] == "running" and land()["started"]


def test_no_room_mid_way_is_unjudged_with_no_step(held, monkeypatch) -> None:
    rows.owe((REPO, A, BASE), 0, True, {}, 1.0)
    _wired(monkeypatch)

    def never() -> None:
        raise judge.room.NoRoom("CPU 1% idle")

    judge.one(args(), (REPO, A, BASE), 0, never)
    assert (land()["status"], land()["why"]) == ("unjudged", "no room: CPU 1% idle")


def test_a_day_without_room_is_said_plainly(held, capsys) -> None:
    day = judge.AGE_S
    rows.owe((REPO, A, BASE), 0, True, {}, 10.0)
    rows.wait((REPO, A, BASE), "no room: CPU 1% idle")
    word = "the box has had no room for 24 h: no land was judged (no room: CPU 1% idle)"
    rc, said = _report(capsys, now=day)
    assert rc == 0 and word not in said and "1 waiting" in said  # not a day yet
    rc, said = _report(capsys, now=day + 10)
    assert word in said and "1 unjudged" in said  # with no drain alive to write it
    rows.owe((REPO, B, BASE), 0, True, {}, 20.0)
    rows.begin((REPO, B, BASE), 30.0)
    rc, said = _report(capsys, now=day + 25)
    assert word not in said  # a step ran within the day
    rc, said = _report(capsys, now=day + 30)
    assert word in said
    rows.unjudge((REPO, A, BASE), judge.FULL)
    rc, said = _report(capsys, now=day + 30)
    assert "no room for 24 h" not in said  # a full sample is no want of room


def test_a_second_drain_leaves_the_rows_to_the_first(held) -> None:
    rows.owe((REPO, A, BASE), 0, True, {}, 1.0)

    def run(_a: object, key: rows.Key, _rc: int, _wait: object) -> None:
        with contextlib.ExitStack() as s:  # the lock is this drain's: another takes nothing
            before = os.environ.pop("GOH_HELD_LOCKS", None)
            s.callback(lambda: before is None or os.environ.update(GOH_HELD_LOCKS=before))
            if key[1] == A:
                rows.owe((REPO, B, BASE), 0, True, {}, 2.0)
            beside = lambda *_: pytest.fail("judged beside another")  # noqa: E731
            assert judge.drain(args(), beside, lambda: 100.0) == 0
        rows.end(key, [], "", 1.0, 50.0)

    judge.drain(args(), run, lambda: 100.0)
    assert land()["status"] == land(B)["status"] == "done"


def _report(capsys, now: float = 100.0, **kw) -> tuple[int, str]:
    rc = judge.report(args(**kw), lambda: now)
    got = capsys.readouterr()
    return rc, got.out + got.err


def test_the_report_is_the_rate_and_what_was_not_judged(held, capsys) -> None:
    tips = {t: t * 40 for t in "abcdefg"}
    for t, tip in tips.items():
        rows.owe((REPO, tip, BASE), 0, t != "a", {}, 50.0)
    rows.end((REPO, tips["b"], BASE), [Step("./s", "would_red", "r", 1, 0, 2.0)], "", 10.0, 99.0)
    rows.end((REPO, tips["c"], BASE), [Step("./s", "would_red", "r", 1, 0, 2.0),
                                       Step("./u", "unavailable", "late")],
             "", 30.0, 99.0)  # fmt: skip
    rows.end((REPO, tips["d"], BASE), [Step("./s", "green", None, 0, 0)], "", 20.0, 99.0)
    rows.end((REPO, tips["e"], BASE), [Step("./u", "unavailable", "late")], "", 20.0, 99.0)
    rows.end((REPO, tips["f"], BASE), [], "no slot", 0.0, 99.0)
    rows.rule("bbb", "./s", "false", "a rule moved")
    held.goh.execute("INSERT INTO spans(kind, repo, at, wall_s, ok, sha) VALUES "
                     "('land', ?, 60, 1, 1, '999999999999')", (REPO,))  # fmt: skip
    rc, said = _report(capsys)
    assert rc == 0
    assert "7 lands: 1 changed no gate code, 4 judged (2 in part), 1 unavailable" in said
    assert "1 waiting, 0 running, 0 unjudged, 1 with no row" in said
    assert "would-be red: 2 of 3 judged lands; ruled false 1, true 0, unruled 1" in said
    assert "false-red rate 1/3 = 33%, at most 2/3" in said and "p50 20.0 s, max 30.0 s" in said
    assert "ffffffff unavailable: no slot" in said and "would_red (ruled false): ./s" in said
    rc, said = _report(capsys, json=True)
    assert rc == 0 and json.loads(said)["false"] == 1


def test_a_report_of_nothing_or_of_no_database_is_not_green(held, capsys, monkeypatch) -> None:
    rc, said = _report(capsys)
    assert rc == 1 and "unknown" in said
    monkeypatch.setattr(
        rows, "lands", lambda *_: (_ for _ in ()).throw(goh_db.Unknown("no goh.db"))
    )
    rc, said = _report(capsys)
    assert rc == 1 and "unavailable: no goh.db" in said


def test_lands_none_of_which_was_judged_have_no_rate(held, capsys) -> None:
    rows.owe((REPO, A, BASE), 0, True, {}, 50.0)
    rc, said = _report(capsys)
    assert rc == 0 and "unknown" in said and "false-red rate 0" not in said


def test_a_ruling_of_nothing_is_an_error(held, capsys) -> None:
    said = argparse.Namespace(tip="aaaa", step="s", said="false", why="w")
    assert judge.rule(said) == 1 and "no would-be red" in capsys.readouterr().err
    rows.owe((REPO, A, BASE), 0, True, {}, 1.0)
    rows.end((REPO, A, BASE), [Step("./s", "would_red")], "", 1.0, 99.0)
    assert judge.rule(said) == 0
