"""lib/timing_ledger.py and lib/timing_ledger.sh: the one timing record (GOH.GATEBUDGET).

The ledger is written by three gates (the push gate, land.sh, goh ship) and read by `goh budget`;
these tests pin the row, the atomic append, the never-fails rule, and the shell span's cpu-s, each
with the plant that makes it red.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import timing_ledger

LEDGER_SH = REPO_ROOT / "lib" / "timing_ledger.sh"


def test_a_row_names_its_subject_and_carries_the_measurement(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path))
    row = timing_ledger.record(
        "push", "ztools", 412.55, cpu_s=980.2, load1=6.11, ok=False, sha="ab12"
    )
    assert row and row["subject"] == "ztools push" and row["wall_s"] in (412.5, 412.6)
    on_disk = timing_ledger.read()
    assert on_disk == [row]
    assert (row["cpu_s"], row["load1"], row["ok"], row["sha"]) == (980.2, 6.11, False, "ab12")


def test_the_gates_checkout_is_goh_in_every_subject() -> None:
    assert timing_ledger.repo_name("/srv/repos/gates_of_heck") == "goh"
    assert timing_ledger.repo_name("ztools/") == "ztools"
    assert timing_ledger.subject_of("", "gate slot wait") == "gate slot wait"


def test_a_ref_already_recorded_is_not_written_twice(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path))
    assert timing_ledger.record("ship", "goh", 100, ref="r1") is not None
    assert timing_ledger.record("ship", "goh", 100, ref="r1") is None
    assert timing_ledger.record("release", "goh", 100, ref="r1") is not None  # another subject
    assert len(timing_ledger.read()) == 2


def test_a_ledger_that_cannot_be_written_returns_none_and_never_raises(
    monkeypatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("GOH_TIMING_DIR", str(blocker / "under"))  # a directory under a FILE
    assert timing_ledger.record("push", "ztools", 5) is None


def test_a_torn_or_foreign_line_is_skipped_and_the_rest_read(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path))
    timing_ledger.record("push", "a", 1)
    with timing_ledger.ledger_path().open("a", encoding="utf-8") as fh:
        fh.write('{"v": 1, "subject": "tor\n{"v": 2, "subject": "future"}\nnot json\n')
    timing_ledger.record("push", "b", 2)
    assert [r["repo"] for r in timing_ledger.read()] == ["a", "b"]


@pytest.mark.load_sensitive
def test_concurrent_writers_interleave_whole_lines(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path))
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import timing_ledger\n"
        "for i in range(40):\n"
        "    timing_ledger.record('push', 'r' + sys.argv[2], i, top_steps=[['s' * 200, 1]] * 5)"
    )
    env = hermetic_env(GOH_TIMING_DIR=str(tmp_path))
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(REPO_ROOT / "lib"), str(n)],
            env=env,
            stdin=subprocess.DEVNULL,
        )
        for n in range(6)
    ]
    assert [p.wait(timeout=patience(60)) for p in procs] == [0] * 6
    lines = timing_ledger.ledger_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 240 and all(json.loads(ln)["v"] == 1 for ln in lines)


def test_the_control_drift_and_the_costliest_steps_come_from_a_timings_file(tmp_path: Path) -> None:
    rows = [
        {
            "label": "control",
            "ms": 1,
            "rc": 0,
            "tier": "t",
            "kind": "rust",
            "control": True,
            "cpu_ms": 1000,
        },
        {"label": "clippy", "ms": 90_000, "rc": 0, "tier": "t", "kind": "rust", "cpu_ms": 80_000},
        {"label": "fmt", "ms": 2_000, "rc": 0, "tier": "t", "cpu_ms": 100},
        {
            "label": "control",
            "ms": 1,
            "rc": 0,
            "tier": "t",
            "kind": "rust",
            "control": True,
            "cpu_ms": 1200,
        },
    ]
    f = tmp_path / "t.jsonl"
    f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    drift, steps = timing_ledger.timings_facts(str(f))
    assert drift == 0.2 and steps == [["clippy", 90.0], ["fmt", 2.0]]  # controls are not steps
    assert timing_ledger.timings_facts(str(tmp_path / "absent")) == (None, [])


def _span(tmp_path: Path, body: str) -> dict:
    """Run BODY between goh_span_start and goh_span_record in a fresh bash; the one row written."""
    env = hermetic_env(
        GOH_TIMING_DIR=str(tmp_path / "ledger"), GOH_PY=sys.executable, GOH=str(REPO_ROOT)
    )
    script = f'. "{LEDGER_SH}"; goh_span_start; {body}; goh_span_record push proj 1 --field sha=abc'
    r = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=patience(60),
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    (row,) = [
        json.loads(ln) for ln in (tmp_path / "ledger" / "ledger.jsonl").read_text().splitlines()
    ]
    return row


def test_a_shell_span_records_the_cpu_its_children_spent(tmp_path: Path) -> None:
    busy = f'"{sys.executable}" -c "sum(i * i for i in range(2_000_000))"'
    row = _span(tmp_path, busy)
    assert row["subject"] == "proj push" and row["sha"] == "abc" and row["ok"] is True
    assert row["cpu_s"] >= 0.1 and row["wall_s"] >= row["cpu_s"] * 0.5, row


def test_a_sleeping_span_spends_wall_and_no_cpu(tmp_path: Path) -> None:
    """Calibration: wall and cpu-s are different instruments (a loaded box inflates the first)."""
    row = _span(tmp_path, "sleep 1.2")
    assert row["wall_s"] >= 1.2 and row["cpu_s"] < 0.5, row


def test_the_push_row_carries_the_slot_wait_once(tmp_path: Path) -> None:
    """The push gate's span starts after its slot (GOH.LOADSOURCES): the wait is timed from the
    ask, rides the next push row, and a second ref's row in the same push carries none."""
    env = hermetic_env(
        GOH_TIMING_DIR=str(tmp_path / "ledger"), GOH_PY=sys.executable, GOH=str(REPO_ROOT)
    )
    script = (
        f'. "{LEDGER_SH}"; goh_slot_asked; sleep 1.2; goh_slot_taken; goh_span_start; '
        f'goh_push_row "{tmp_path}" 0 aaaaaaaaaaaa; goh_span_start; '
        f'goh_push_row "{tmp_path}" 0 bbbbbbbbbbbb'
    )
    r = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=patience(60),
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    first, second = [
        json.loads(ln) for ln in (tmp_path / "ledger" / "ledger.jsonl").read_text().splitlines()
    ]
    assert (
        first["sha"] == "aaaaaaaaaaaa" and 1.2 <= first["slot_wait_s"] < first["slot_asked_at"]
    ), first
    assert first["epoch"] - first["slot_asked_at"] >= 1.2 and "slot_wait_s" not in second, second


def test_the_p95_counts_the_waits_an_agent_sat_through_and_names_its_sample(
    monkeypatch, tmp_path: Path
) -> None:
    """GOH.TOOLTIMEOUT: the push gate prints its own p95 so a caller knows it runs long. The wait
    for a slot or the land lock is time the caller's command sat through, so it counts."""
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path))
    for wall in range(1, 101):  # 1..100 s, one of them waited 500 s for a slot
        timing_ledger.record(
            "push", "ztools", float(wall), slot_wait_s=500.0 if wall == 3 else None
        )
    timing_ledger.record("push", "other", 9999.0)
    timing_ledger.record("land", "ztools", 9999.0)
    # nearest rank, 95th of 100: 96 s with the 503 s run counted, 95 s if its wait were dropped
    assert timing_ledger.p95("push", "ztools") == (96.0, 100)
    assert timing_ledger.p95("push", "nothing") is None
    argv = [sys.executable, str(REPO_ROOT / "lib" / "timing_ledger.py"), "p95", "--kind", "push",
            "--repo", "ztools"]  # fmt: skip
    env = {**hermetic_env(), "GOH_TIMING_DIR": str(tmp_path)}
    out = subprocess.run(argv, capture_output=True, text=True, env=env, check=True,
                         stdin=subprocess.DEVNULL)  # fmt: skip
    assert out.stdout.split() == ["96", "100"]
