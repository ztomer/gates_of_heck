"""GOH.ONEGATE: the land judge's rows (goh.db 0023, lib/land_judge_rows.py), in memory."""

from __future__ import annotations

import sqlite3

import goh_db
import land_judge_rows as rows
import pytest
from _kv_fixture import Held
from land_judge_rows import Step

REPO, A, B, BASE = "gates_of_heck", "a" * 40, "b" * 40, "0" * 40


@pytest.fixture
def held(monkeypatch) -> Held:
    return Held().lay(monkeypatch)


def key(tip: str) -> rows.Key:
    return (REPO, tip, BASE)


def status(tip: str) -> tuple:
    (land,) = (x for x in rows.lands(REPO, 9, 0.0) if x["tip"] == tip)
    return land["status"], land["why"]


def test_an_owed_land_is_claimed_oldest_first_and_ended(held) -> None:
    rows.owe(key(B), 0, True, {}, 2.0)
    rows.owe(key(A), 1, True, {"./s": 2}, 1.0)
    assert rows.claim("other") is None
    assert rows.claim(REPO) == (key(A), 1, 1.0) and rows.owed_steps(key(A)) == {"./s": 2}
    rows.begin(key(A), 3.0)
    assert rows.claim(REPO) == (key(B), 0, 2.0)
    rows.end(key(A), [Step("./s", "broken", "why", 0, 2, 1.5), Step("./t", "same")], "", 4.0, 9.0)
    rows.end(key(B), [], "no slot", 0.0, 9.0)
    assert rows.claim(REPO) is None
    assert status(A) == ("done", None) and status(B) == ("unavailable", "no slot")
    (newest, oldest) = rows.lands(REPO, 9, 0.0)
    assert (newest["tip"], oldest["tip"]) == (B, A)
    assert [(s["step"], s["outcome"], s["tip_rc"]) for s in oldest["steps"]] == [
        ("./s", "broken", 2), ("./t", "same", None)]  # fmt: skip
    assert [x["tip"] for x in rows.lands(REPO, 9, 1.5)] == [B]


def test_a_land_that_changed_no_gate_code_is_never_owed(held) -> None:
    rows.owe(key(A), 0, False, {}, 1.0)
    assert rows.claim(REPO) is None and status(A) == ("same", None)


def test_a_land_tried_again_is_written_whole(held) -> None:
    rows.owe(key(A), 1, True, {"./s": 2}, 1.0)
    rows.end(key(A), [Step("./s", "broken", None, 0, 2)], "", 1.0, 5.0)
    rows.owe(key(A), 0, True, {}, 2.0)
    (land,) = rows.lands(REPO, 9, 0.0)
    assert (land["status"], land["land_rc"], land["seconds"], land["steps"]) == (
        "waiting",
        0,
        None,
        [],
    )


def test_a_running_row_is_settled_as_unavailable(held) -> None:
    rows.owe(key(A), 0, True, {}, 1.0)
    rows.owe(key(B), 0, True, {}, 2.0)
    rows.begin(key(A), 3.0)
    assert rows.settle("other") == 0 and rows.settle(REPO) == 1 and rows.settle(REPO) == 0
    assert status(A)[0] == "unavailable" and "ended before" in status(A)[1]
    assert status(B) == ("waiting", None)


def test_a_wait_is_said_and_a_step_takes_it_back(held) -> None:
    rows.owe(key(A), 0, True, {}, 1.0)
    rows.begin(key(A), 3.0)
    rows.wait(key(A), "no room: CPU")
    assert status(A) == ("waiting", "no room: CPU") and rows.claim(REPO) == (key(A), 0, 1.0)
    assert rows.settle(REPO) == 0  # a wait is no dead judge's: the next drain takes it
    rows.begin(key(A), 7.0)
    (land,) = rows.lands(REPO, 9, 0.0)
    assert (land["status"], land["why"], land["started"]) == ("running", None, 3.0)
    rows.unjudge(key(A), "no room: x")
    assert status(A) == ("unjudged", "no room: x")


def test_waiting_lands_past_their_bound_are_unjudged_by_their_own_reason(held) -> None:
    for tip, at in ((A, 1.0), (B, 2.0), ("c" * 40, 3.0)):
        rows.owe(key(tip), 0, True, {}, at)
    rows.wait(key(A), "no room: CPU")
    assert rows.expire("other", 9.0, "no room") == 0 and rows.expire(REPO, 2.0, "no room") == 2
    assert status(A) == ("unjudged", "no room: CPU") and status(B) == ("unjudged", "no room")
    rows.wait(key("c" * 40), "no room: CPU")
    assert rows.expire(REPO, None, "the sample is full") == 1
    assert status("c" * 40) == ("unjudged", "the sample is full")


def test_a_sample_is_what_was_judged_since_it_was_read(held) -> None:
    found = {A: [Step("./s", "green", None, 0, 0)], B: [Step("./s", "unavailable")],
             "c" * 40: [Step("./s", "flaky", None, 1, 0)]}  # fmt: skip
    for i, (tip, steps) in enumerate(found.items()):
        rows.owe(key(tip), 0, True, {}, float(i))
        rows.end(key(tip), steps, "", 1.0, 10.0 + i)
    assert rows.judged(REPO) == 2 and rows.judged("other") == 0
    rows.read(REPO, 10.0)
    assert rows.judged(REPO) == 1
    rows.read(REPO, 99.0)
    assert rows.judged(REPO) == 0


def test_the_last_judged_lands_span_holds_what_was_not_judged_between_them(held) -> None:
    for i, tip in enumerate((A, B, "c" * 40, "d" * 40)):
        rows.owe(key(tip), 0, True, {}, float(i))
    for tip in (A, "c" * 40):
        rows.end(key(tip), [], "", 1.0, 9.0)
    tips = lambda n: [x["tip"][0] for x in rows.lands(REPO, n, 0.0)]  # noqa: E731
    assert (tips(1), tips(2), tips(9)) == (["d", "c"], ["d", "c", "b", "a"], ["d", "c", "b", "a"])


def test_only_a_would_be_red_is_ruled(held) -> None:
    rows.owe(key(A), 0, True, {}, 1.0)
    found = [Step("./gates/a.sh", "would_red"), Step("./gates/b.sh", "would_red"),
             Step("./gates/c.sh", "green")]  # fmt: skip
    rows.end(key(A), found, "", 1.0, 5.0)
    assert rows.rule("aaaa", "a.sh", "false", "the rule moved on purpose") == 1
    assert rows.rule("bbbb", "a.sh", "true", "x") == 0 == rows.rule("aaaa", "c.sh", "true", "x")
    (land,) = rows.lands(REPO, 9, 0.0)
    assert [s["ruled"] for s in land["steps"]] == ["false", None, None]
    with pytest.raises(sqlite3.IntegrityError):
        rows.rule("aaaa", "a.sh", "maybe", "x")


def test_a_landed_tip_with_no_row_is_counted(held) -> None:
    for at, ok, sha, repo in ((1.0, 1, A, REPO), (2.0, 1, B, REPO), (3.0, 0, "c" * 40, REPO),
                              (4.0, 1, "d" * 40, "other"), (0.1, 1, "e" * 40, REPO)):  # fmt: skip
        held.goh.execute("INSERT INTO spans(kind, repo, at, wall_s, ok, sha) VALUES "
                         "('land', ?, ?, 1, ?, ?)", (repo, at, ok, sha[:12]))  # fmt: skip
    rows.owe(key(A), 0, False, {}, 1.0)
    assert rows.unrowed(REPO, 0.5) == [B[:12]]


def test_a_file_from_before_0023_is_an_error_never_no_lands(monkeypatch) -> None:
    Held(last=21).lay(monkeypatch)
    with pytest.raises(sqlite3.OperationalError):
        rows.lands(REPO, 9, 0.0)
    with pytest.raises(sqlite3.OperationalError):
        rows.owe(key(A), 0, True, {}, 1.0)


def test_no_file_is_unknown(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GOH_DB", str(tmp_path / "none.db"))
    with pytest.raises(goh_db.Unknown):
        rows.lands(REPO, 9, 0.0)
