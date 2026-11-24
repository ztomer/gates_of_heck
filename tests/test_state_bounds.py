"""lib/state_bounds.py (GOH.LANDLOGPRUNE): every per-run directory under goh's state root is
bounded, a land's files go together, a live run is never cut, the queue's record runs the sweep,
and a directory written per run with no bound is named at SessionStart."""

import fcntl
import importlib.util
import json
import os
import sys
from pathlib import Path

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import land_prestep
import state_bounds as sb

DAY = 86400.0
NOW = 1_795_000_000.0  # a fixed clock (2026-11-19): every age is relative to it, never the box's


def _file(p: Path, age_s: float, now: float) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    os.utime(p, (now - age_s, now - age_s))
    return p


def test_a_land_older_than_the_history_goes_with_its_timings_and_gate_logs(tmp_path) -> None:
    now = NOW
    logs = tmp_path / "landq" / "logs"
    old = [_file(logs / f"01700000000000000000-GOH.OLD{s}", 20 * DAY, now)
           for s in (".log", ".timings.jsonl", ".gate1.log", ".gate1.timings.jsonl")]  # fmt: skip
    new = _file(logs / "01800000000000000000-GOH.NEW.log", 2 * DAY, now)
    stray = _file(logs / "notes.txt", 90 * DAY, now)  # not a run's: never touched
    removed = sb.sweep(tmp_path, now)
    assert sorted(removed) == sorted(old)
    assert new.exists() and stray.exists()


def test_past_the_count_the_oldest_runs_go_and_a_live_one_stays(tmp_path) -> None:
    now = NOW
    rule = sb.Bound(days=30, keep=3, run=r"^\d+-", why="test")
    d = tmp_path / "logs"
    for i in range(6):
        _file(d / f"{100 + i}-ITEM.log", (10 - i) * 3600 + 7200, now)
    live = _file(d / "99-LIVE.log", 60, now)  # the oldest name, but moving now: in flight
    removed = sb.prune(rule, d, now)
    assert sorted(p.name for p in removed) == ["100-ITEM.log", "101-ITEM.log", "102-ITEM.log",
                                               "103-ITEM.log"]  # fmt: skip
    assert live.exists()


def test_a_held_lock_keeps_an_old_run_directory(tmp_path) -> None:
    now = NOW
    run = tmp_path / "run"
    held, idle = run / f"tree-{'a' * 16}", run / f"tree-{'b' * 16}"
    for d in (held, idle):
        _file(d / "tree.lock", 10 * DAY, now)
        os.utime(d, (now - 10 * DAY, now - 10 * DAY))
    fd = os.open(held / "tree.lock", os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        removed = sb.sweep(tmp_path, now)
    finally:
        os.close(fd)
    assert removed == [idle] and held.exists()


def test_each_repos_ship_results_are_bounded_and_its_reused_worktree_kept(tmp_path) -> None:
    now = NOW
    repo = tmp_path / "ship" / "some_repo"
    old = [_file(repo / f"20261001T000000Z-0123abcd{s}", 40 * DAY, now)
           for s in (".json", ".log", ".release.timings.jsonl")]  # fmt: skip
    fresh = _file(repo / "20261118T000000Z-4567cdef.json", DAY, now)
    wt = repo / "suite-wt"
    wt.mkdir()
    os.utime(wt, (now - 40 * DAY, now - 40 * DAY))
    assert sorted(sb.sweep(tmp_path, now)) == sorted(old)
    assert fresh.exists() and wt.exists()


def test_the_check_names_a_per_run_directory_with_no_bound(tmp_path) -> None:
    now = NOW
    for i in range(sb.GROWTH):
        _file(tmp_path / "newthing" / f"run-{i}.log", 0, now)
    for i in range(sb.GROWTH + 5):  # registered Kept: a store bounded at its writer
        _file(tmp_path / "reuse" / "profstore" / f"k{i}", 0, now)
    _file(tmp_path / "landq" / "logs" / "01700000000000000000-GOH.X.log", 30 * DAY, now)
    _file(tmp_path / "few" / "a.log", 0, now)
    found = sb.unbounded(tmp_path, now)
    assert any(line.startswith("newthing: 50 entries and no bound") for line in found), found
    assert any(line.startswith("landq/logs: a run 30 days old") for line in found), found
    assert not [line for line in found if line.startswith(("reuse", "few"))], found
    sb.sweep(tmp_path, now)
    assert [line for line in sb.unbounded(tmp_path, now) if not line.startswith("newthing")] == []


def test_the_registry_sizes_land_history_from_the_measured_cadence() -> None:
    logs = sb.REGISTRY["landq/logs"]
    assert isinstance(logs, sb.Bound) and logs.days == sb.HISTORY_DAYS
    assert logs.keep >= sb.LANDS_PER_DAY * sb.HISTORY_DAYS  # at least one run per land kept
    for rel in sb.REGISTRY:
        parent = rel.rpartition("/")[0]
        assert not parent or isinstance(sb.REGISTRY.get(parent), sb.Container), rel


def test_recording_a_land_runs_the_sweep(tmp_path) -> None:
    """The queue's record (tools/landq.py -> land_prestep.discard) is the one place it runs."""
    now = NOW
    q = tmp_path / "landq"
    gone = _file(q / "logs" / "01700000000000000000-GOH.OLD.log", 30 * DAY, now)
    land_prestep.discard({}, q)
    assert not gone.exists()  # its record is a goh.db row since wave 3 (GOH.STATEDB S1)


def test_session_start_names_an_unbounded_state_directory(tmp_path, monkeypatch) -> None:
    hook = REPO_ROOT / "hooks" / "claude" / "estate_advisories.py"
    spec = importlib.util.spec_from_file_location("estate_advisories_sb", hook)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    state = tmp_path / "estate_audit.json"
    state.write_text(json.dumps({"ran_at": "2026-11-19T00:00:00+00:00", "advisories": {}}))
    for i in range(sb.GROWTH):
        _file(tmp_path / "piling" / f"{i}.log", 0, NOW)
    lines = mod.state_lines(state)
    want = (
        "✗ goh state: piling: 50 entries and no bound -- bound it in "
        "lib/state_bounds.py REGISTRY, or excuse it there"
    )
    assert lines == [want]


def test_no_reuse_path_keeps_a_directory_of_record_files() -> None:
    # GOH.AFFECTEDBOUND once gave every reuse path stored under reuse/<name> its row here; since
    # GOH.VERDICTDB its records are goh-cache.db rows (lib/reuse_store.py, bounded by
    # lib/db_bounds.py), so a row for reuse/<name> would excuse a directory nothing writes.
    import reuse

    stale = [f"reuse/{n}" for n in reuse.PATHS if f"reuse/{n}" in sb.REGISTRY]
    assert not stale, f"reuse paths whose record files are retired, still registered: {stale}"
