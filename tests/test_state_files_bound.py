"""GOH.LEDGERBOUND: every single append-only file under goh's state root is bounded
(lib/state_bounds.py FILES) or excused, trimmed under its writer's lock (lib/state_append.py),
and an unlisted one that grows is named by the SessionStart check."""

import fcntl
import json
import os
import sys
from pathlib import Path

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import state_append
import state_bounds as sb
import state_files as sf

DAY = 86400.0
NOW = 1_795_000_000.0


def _rows(path: Path, ages_days: list[float], field: str = "epoch") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({field: NOW - a * DAY, "i": i}) + "\n"
                            for i, a in enumerate(ages_days)))  # fmt: skip


def test_the_timing_ledger_keeps_its_readers_window_and_drops_the_rest(tmp_path) -> None:
    ledger = tmp_path / "timing" / "ledger.jsonl"
    rule = sf.FILES["timing/ledger.jsonl"]
    assert isinstance(rule, sf.FileBound)
    _rows(ledger, [rule.days + 5, rule.days + 1, rule.days - 1, 1])
    sb.sweep(tmp_path, NOW)
    kept = [json.loads(x)["i"] for x in ledger.read_text().splitlines()]
    assert kept == [2, 3]


def test_the_count_bound_keeps_the_newest_lines(tmp_path) -> None:
    rule = sf.FileBound(days=None, lines=3, stamp="", lock="", why="t")
    log = tmp_path / "x.log"
    log.write_text("".join(f"line {i}\n" for i in range(10)))
    fd = os.open(log, os.O_WRONLY | os.O_APPEND)  # a holder's stdout: trimmed in place, still live
    try:
        assert sf.trim(rule, log, NOW) == 7
        os.write(fd, b"after\n")
    finally:
        os.close(fd)
    assert log.read_text() == "line 7\nline 8\nline 9\nafter\n"


def test_a_writer_holding_the_lock_is_never_trimmed_under(tmp_path) -> None:
    ledger = tmp_path / "timing" / "ledger.jsonl"
    _rows(ledger, [400, 1])
    with open(state_append.lock_path(ledger), "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_SH)  # a writer mid-append
        assert sf.trim(sf.FILES["timing/ledger.jsonl"], ledger, NOW) == 0
    assert sf.trim(sf.FILES["timing/ledger.jsonl"], ledger, NOW) == 1
    state_append.append(
        ledger, b'{"epoch": 1, "i": 9}\n'
    )  # the writer's next row lands in the new file
    assert len(ledger.read_text().splitlines()) == 2


def test_every_writer_appends_through_the_lock(tmp_path) -> None:
    import timing_ledger

    target = tmp_path / "ledger.jsonl"
    assert timing_ledger.append({"v": 1, "subject": "s"}, target)
    assert state_append.lock_path(target).exists()
    # lane_room's ledger is goh.db's lane_starts since GOH.STATEDBS4 (tests/test_hook_ledgers.py)


def test_the_check_names_an_unlisted_growing_file_and_a_file_past_its_bound(tmp_path) -> None:
    (tmp_path / "landq").mkdir()
    (tmp_path / "new-ledger.jsonl").write_text("{}\n")
    (tmp_path / "landq" / "other.log").write_text("x\n")
    (tmp_path / "reuse" / "audit").mkdir(parents=True)
    (tmp_path / "reuse" / "audit" / "growing.jsonl").write_text("{}\n")
    _rows(tmp_path / "timing" / "ledger.jsonl", [400])
    _rows(
        tmp_path / "reuse" / "profstore" / "ledger.jsonl", [400]
    )  # excused: bounded at its writer
    found = sb.unbounded(tmp_path, NOW)
    for rel in ("new-ledger.jsonl", "landq/other.log", "reuse/audit/growing.jsonl"):
        assert any(line.startswith(f"{rel}: an append-only file and no bound") for line in found), (
            found
        )
    assert any(line.startswith("timing/ledger.jsonl: a row 400 days old") for line in found), found
    assert not [x for x in found if x.startswith("reuse/profstore")], found
    sb.sweep(tmp_path, NOW)
    assert not [x for x in sb.unbounded(tmp_path, NOW) if x.startswith("timing")]


def test_each_bound_keeps_what_its_readers_need() -> None:
    # land_attempts/budget_ships/cip_history/land_e2e backfill from landq/done and ship/*,
    # deduping on the ledger's refs: a row must outlive its source, or the backfill writes it again.
    for rel in ("timing/ledger.jsonl", "landq/selection_misses.jsonl"):
        rule = sf.FILES[rel]
        assert isinstance(rule, sf.FileBound) and rule.days > sb.HISTORY_DAYS + 1, rel
    assert sf.HISTORY_DAYS == sb.HISTORY_DAYS
    assert sf.FILES["timing/ledger.jsonl"].days >= 28  # the weekly usage review's month
    for rel, rule in sf.FILES.items():
        assert rule.why, rel


def test_the_guard_log_is_bounded_and_its_writer_appends_through_the_lock(
    tmp_path, monkeypatch
) -> None:
    # GOH.GUARDSBOUND: landq/guards.jsonl (lib/land_check.py learn) was the one unlisted file.
    # GOH.STATEDB: learn writes goh.db's land_guards (a table bound); the file's bound stays for
    # the pre-switch file until wave 3 deletes it.
    import land_check
    import land_records

    monkeypatch.setenv("GOH_DB", str(tmp_path / "goh.db"))
    q = tmp_path / "landq"
    q.mkdir()
    land_check.learn(q, {"item": "T.G"}, "refused: tests/test_x.py::test_y failed")
    assert [r["guard"] for r in land_records.ledger(q, "guards")] == ["tests/test_x.py::test_y"]
    assert not (q / "guards.jsonl").exists()
    (q / "guards.jsonl").write_text('{"at": "2026-11-20T00:00:00Z"}\n')  # a pre-switch file
    assert not sb.unbounded(tmp_path, NOW), sb.unbounded(tmp_path, NOW)
    rule = sf.FILES["landq/guards.jsonl"]
    assert isinstance(rule, sf.FileBound) and rule.stamp == "at" and rule.lock == "state_file"
    (q / "unlisted.jsonl").write_text("{}\n")  # the check still bites beside it
    assert sb.unbounded(tmp_path, NOW) == ["landq/unlisted.jsonl: an append-only file and no bound"]
