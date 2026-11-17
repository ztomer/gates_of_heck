"""lib/landq_report.py (`goh land --report`, GOH.LANDREPORT): where the queue's wall time goes.

The fixture is a queue of done records and logs whose table was computed BY HAND (the comments
below carry the arithmetic), so a wrong sum, a wrong day, or a negative run time fails here. The
lander's own change is pinned too: every new done record carries started / finished / slot_wait_s
and the load at its start.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from test_landq import FAKE_LOOPCTL, LANDQ, landq, make_repo
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import landq_report as lr

T0 = dt.datetime(2026, 11, 17, 10, 0, tzinfo=dt.UTC).timestamp()


def iso(offset: float) -> str:
    return dt.datetime.fromtimestamp(T0 + offset, dt.UTC).isoformat(timespec="milliseconds")


def put(q: Path, enq: float, item: str, repo: str, state: str, fin: float, **extra) -> Path:
    """A done record `enq` seconds after T0 finishing at `fin`, and its log (text in `log=`)."""
    stem = f"{int((T0 + enq) * 1e9):020d}-{item}"
    log = q / "logs" / f"{stem}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(extra.pop("log", ""))
    rec = {"repo": f"/x/{repo}", "state": state, "log": str(log), "at": iso(fin)[:19] + "Z"}
    rec.update(extra)
    (q / "done").mkdir(parents=True, exist_ok=True)
    out = q / "done" / f"{stem}.json"
    out.write_text(json.dumps(rec))
    return log


def fixture(q: Path, *, new_only: bool) -> dict[Path, float]:
    """The hand-computed queue. Returns each old record's log -> its BIRTH time (the seam's table).

    alpha 11-17: A new   wait  60 gate  600 slot 100 run  500 landed load 4
                 B old   wait 600 gate 1200 slot  30 run 1170 red      (log born T+700)
                 C new   wait   0 gate  300 slot   0 run  300 landed load 8
    beta  11-17: D old, its log born BEFORE its request (a log a later attempt reused): unknown
                 F old   wait  10 gate 1200 slot 270 run  930 red      (log born T+20)
    alpha 11-16: E (outside --since 2026-11-17)
    """
    born: dict[Path, float] = {}
    put(q, 0, "A", "alpha", "landed", 660, started=iso(60), slot_wait_s=100, load_start=4.0)
    put(q, 2000, "C", "alpha", "landed", 2300, started=iso(2000), slot_wait_s=0, load_start=8.0)
    put(q, -86400, "E", "alpha", "landed", -86000, started=iso(-86300), slot_wait_s=0)
    log = put(q, 100, "B", "alpha", "red", 1900, log="load: gate slot 1/2 taken after 30s (x)\n")
    born[log] = T0 + 700
    log = put(q, 0, "D", "beta", "landed", 900, log="gate slot 1/1 taken after 5s waiting\n")
    born[log] = T0 - 500
    log = put(q, 10, "F", "beta", "red", 1220, log="waited 1m 00s for a gate slot; still held:\n"
              "waited 4m 30s for a gate slot; still held:\n")  # fmt: skip
    born[log] = T0 + 20
    if new_only:
        for p in (q / "done").glob("*-[BDF].json"):
            p.unlink()
    return born


def test_the_report_reproduces_the_hand_computed_table(tmp_path):
    born = fixture(tmp_path, new_only=False)
    atts = lr.read_attempts(tmp_path, birth_of=born.get)
    rep = lr.build(atts, "2026-11-17")
    alpha, beta = rep["rows"]
    assert (alpha["repo"], alpha["day"], beta["repo"]) == ("alpha", "2026-11-17", "beta")
    assert (alpha["attempts"], alpha["landed"], alpha["red"]) == (3, 2, 1)
    assert (alpha["queue_wait_s"], alpha["gate_s"], alpha["slot_wait_s"], alpha["run_s"]) == (
        660, 2100, 130, 1970,
    )  # fmt: skip
    assert (alpha["load_avg"], alpha["gates_per_landed"], alpha["unknown"]) == (6.0, 1.5, 0)
    assert (beta["attempts"], beta["landed"], beta["red"], beta["unknown"]) == (2, 1, 1, 1)
    assert (beta["queue_wait_s"], beta["gate_s"], beta["slot_wait_s"], beta["run_s"]) == (
        10, 1200, 270, 930,
    )  # fmt: skip
    assert (beta["load_avg"], beta["gates_per_landed"]) == (None, 2.0)
    tot = rep["totals"]
    assert (tot["attempts"], tot["landed"], tot["red"], tot["unknown"]) == (5, 3, 2, 1)
    assert (tot["queue_wait_s"], tot["gate_s"], tot["slot_wait_s"], tot["run_s"]) == (
        670, 3300, 400, 2900,
    )  # fmt: skip
    assert rep["unloaded_s"] == 300  # the shortest green run: C
    assert alpha["cause"] == {}  # PREPARITY fills it


def test_no_run_time_is_ever_negative_and_a_reused_log_is_unknown(tmp_path):
    born = fixture(tmp_path, new_only=False)
    by = {a.item: a for a in lr.read_attempts(tmp_path, birth_of=born.get)}
    assert by["D"].gate_s is None and by["D"].queue_wait_s is None  # log older than its request
    # a log born after the attempt finished is no start either
    born = dict.fromkeys(born, T0 + 5000)
    for a in lr.read_attempts(tmp_path, birth_of=born.get):
        assert a.run_s is None or a.run_s >= 0
    assert {a.item for a in lr.read_attempts(tmp_path, birth_of=born.get)
            if a.gate_s is None} == {"B", "D", "F"}  # fmt: skip


def test_two_records_sharing_one_log_are_both_unknown(tmp_path):
    first = put(tmp_path, 0, "G", "alpha", "red", 400)
    second = tmp_path / "done" / f"{int((T0 + 500) * 1e9):020d}-G.json"
    second.write_text(json.dumps({"repo": "/x/alpha", "state": "landed", "log": str(first),
                                  "at": iso(900)[:19] + "Z"}))  # fmt: skip
    atts = lr.read_attempts(tmp_path, birth_of=lambda p: T0 + 50)
    assert all(a.gate_s is None for a in atts), atts


@pytest.fixture
def qenv(tmp_path: Path) -> dict[str, str]:
    (tmp_path / "loopctl.py").write_text(FAKE_LOOPCTL)
    return hermetic_env(
        drop_git=True,
        GOH_LANDQ_DIR=str(tmp_path / "landq"),
        GOH_LOOPCTL=str(tmp_path / "loopctl.py"),
        FAKE_LOOPCTL_LOG=str(tmp_path / "loopctl.log"),
        HOOK_LOG=str(tmp_path / "hook.log"),
        GOH_LAND_TIMEOUT="60",
    )


def cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LANDQ), "--report", *args],
        env=hermetic_env(GOH_LANDQ_DIR=str(tmp_path)),
        capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
        timeout=patience(120),
    )  # fmt: skip


def test_the_command_prints_the_json_and_the_text_table(tmp_path):
    fixture(tmp_path, new_only=True)  # birth times are real here; only records with `started`
    got = cli(tmp_path, "--since", "2026-11-17", "--json")
    assert got.returncode == 0, got.stderr
    rep = json.loads(got.stdout)
    assert [(r["repo"], r["attempts"], r["gate_s"]) for r in rep["rows"]] == [("alpha", 2, 900)]
    assert rep["totals"]["slot_wait_s"] == 100 and rep["unloaded_s"] == 300
    text = cli(tmp_path, "--since", "2026-11-17")
    assert text.returncode == 0 and "alpha" in text.stdout and "unloaded" in text.stdout
    assert "2026-11-16" not in text.stdout  # --since cuts the earlier day


def test_an_empty_queue_prints_an_empty_table_not_a_traceback(tmp_path):
    got = cli(tmp_path, "--json")
    assert got.returncode == 0, got.stderr
    assert json.loads(got.stdout)["rows"] == []


def test_the_log_parser_reads_every_slot_line_the_gates_print():
    assert lr.slot_wait_s("x\nload: gate slot 1/2 taken after 7s (GOH_GATE_SLOTS)\n") == 7
    assert (
        lr.slot_wait_s("gate slot 1/1 taken after 3s waiting\nload: gate slot 2/2 taken after 4s")
        == 7
    )
    assert lr.slot_wait_s("waited 30s for a gate slot\nwaited 4m 30s for a gate slot; x") == 270
    assert lr.slot_wait_s("no slot line") is None


def test_the_lander_writes_started_finished_slot_wait_and_load_into_each_done_record(
    tmp_path, qenv
):
    repo, _, sha = make_repo(tmp_path, "eta")
    hook = repo / ".git" / "hooks" / "pre-push"
    taken = "echo 'load: gate slot 1/2 taken after 7s (GOH_GATE_SLOTS)'; sleep 0.4"
    hook.write_text(hook.read_text().replace("sleep 0.4", taken))
    assert landq(qenv, "--enqueue", str(repo), sha, "--item", "T.R", "--no-start").returncode == 0
    landq(qenv, "--drain")
    (rec_path,) = (tmp_path / "landq" / "done").glob("*-T.R.json")
    rec = json.loads(rec_path.read_text())
    assert rec["state"] == "landed" and rec["slot_wait_s"] == 7, rec
    started, finished = (dt.datetime.fromisoformat(rec[k]) for k in ("started", "finished"))
    assert started <= finished and (finished - started).total_seconds() >= 0.4, rec
    assert isinstance(rec["load_start"], float) and isinstance(rec["load_end"], float), rec
    att = lr.read_attempts(tmp_path / "landq")[0]  # the report reads what the lander wrote
    assert att.slot_s == 7 and att.gate_s >= 0.4


def test_a_refused_request_still_carries_started_and_finished(tmp_path, qenv):
    repo, _, _ = make_repo(tmp_path, "theta")
    pending = tmp_path / "landq" / "pending"
    pending.mkdir(parents=True)
    req = {"mode": "land", "repo": str(repo), "worktree": str(repo), "sha": "0" * 40, "item": ""}
    (pending / f"{int(T0 * 1e9):020d}-T.S.json").write_text(json.dumps(req))
    landq(qenv, "--drain")
    (rec_path,) = (tmp_path / "landq" / "done").glob("*-T.S.json")
    rec = json.loads(rec_path.read_text())
    assert rec["why"] == "tip moved after enqueue", rec
    assert {"started", "finished", "slot_wait_s"} <= set(rec), rec
