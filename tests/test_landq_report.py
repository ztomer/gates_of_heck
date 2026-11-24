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

import _goh_db_fixture as gdb
import pytest
from conftest import REPO_ROOT, hermetic_env
from test_landq import FAKE_LOOPCTL, LANDQ, landq, make_repo
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import land_records
import landq_report as lr
import landq_state

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
    land_records.write(q, stem, rec)  # its goh.db row, as the lander writes it
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
    if new_only:  # the records B, D and F (and their logs' births) are not filed
        put(q, 0, "A", "alpha", "landed", 660, started=iso(60), slot_wait_s=100, load_start=4.0)
        put(q, 2000, "C", "alpha", "landed", 2300, started=iso(2000), slot_wait_s=0,
            load_start=8.0)  # fmt: skip
        put(q, -86400, "E", "alpha", "landed", -86000, started=iso(-86300), slot_wait_s=0)
        return born
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
    assert alpha["cause"] == {"unclassified": 1}  # B's log says nothing of why (lib/land_cause.py)


def test_a_red_no_rule_reads_is_a_reported_fault_of_the_classifier_naming_its_log(tmp_path):
    """GOH.REDOTHER: `unclassified` is not a bucket; the report lists each one with its log, and
    a gate step with no test names its step."""
    planted = put(tmp_path, 0, "P", "alpha", "red", 300, log="something no rule has seen\n")
    step = (
        "✗ [1/10] FAILED (exit status 1): ./gates/rust_gate.sh\n✗ land.sh: the gate failed on a\n"
    )
    put(tmp_path, 400, "S", "alpha", "red", 700, log=step)
    rep = lr.build(lr.read_attempts(tmp_path), "2026-11-17")
    assert rep["totals"]["cause"] == {"unclassified": 1, "gate-step": 1}
    assert rep["unclassified"] == [{"item": "P", "repo": "alpha", "day": "2026-11-17",
                                    "log": str(planted)}]  # fmt: skip
    assert rep["gate_steps"] == {"./gates/rust_gate.sh": 1}
    text = lr.render(rep)
    assert f"⚠ unclassified red P (alpha 2026-11-17): read {planted}" in text, text
    assert "gate steps: ./gates/rust_gate.sh 1" in text, text
    clean = lr.render(lr.build(lr.read_attempts(tmp_path / "none"), ""))
    assert "unclassified" not in clean and "gate steps" not in clean


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
    land_records.write(tmp_path, f"{int((T0 + 500) * 1e9):020d}-G",
                       {"repo": "/x/alpha", "state": "landed", "log": str(first),
                        "at": iso(900)[:19] + "Z"})  # fmt: skip
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


def cli(tmp_path: Path, *args: str, db: Path | None = None) -> subprocess.CompletedProcess[str]:
    """`--report` on queue TMP_PATH; DB names its goh.db (else the suite home's)."""
    named = {"GOH_DB": str(db)} if db else {}
    return subprocess.run(
        [sys.executable, str(LANDQ), "--report", *args],
        env=hermetic_env(GOH_LANDQ_DIR=str(tmp_path), **named),
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


def test_an_empty_queue_prints_an_empty_table_not_a_traceback(tmp_path, monkeypatch):
    """An empty queue is an EMPTY goh.db (GOH.STATEDB): the test's own, made and holding no
    record. Without one the test read the suite home's, there or not by the order tests ran."""
    db = gdb.made(monkeypatch, tmp_path)
    got = cli(tmp_path, "--json", db=db)
    assert got.returncode == 0, got.stderr
    assert json.loads(got.stdout)["rows"] == []


def test_no_goh_db_is_unknown_in_one_line_never_an_empty_table(tmp_path):
    """No goh.db is unknown, never zero lands (GOH.STATEDB db-plan (d)): exit 2, one line."""
    got = cli(tmp_path, "--json", db=tmp_path / "goh.db")
    assert (got.returncode, got.stdout) == (2, ""), got
    assert got.stderr.strip().splitlines() == [
        "✗ goh land --report: the lands are unknown: there is no goh.db"
    ]
    assert "Traceback" not in got.stderr and not (tmp_path / "goh.db").exists()


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
    ((_, rec),) = [x for x in land_records.records(tmp_path / "landq") if x[0].endswith("-T.R")]
    assert rec["state"] == "landed" and rec["slot_wait_s"] == 7, rec
    started, finished = (dt.datetime.fromisoformat(rec[k]) for k in ("started", "finished"))
    assert started <= finished and (finished - started).total_seconds() >= 0.4, rec
    assert isinstance(rec["load_start"], float) and isinstance(rec["load_end"], float), rec
    att = lr.read_attempts(tmp_path / "landq")[0]  # the report reads what the lander wrote
    assert att.slot_s == 7 and att.gate_s >= 0.4


def test_a_refused_request_still_carries_started_and_finished(tmp_path, qenv):
    repo, _, _ = make_repo(tmp_path, "theta")
    req = {"mode": "land", "repo": str(repo), "worktree": str(repo), "sha": "0" * 40, "item": ""}
    landq_state.put(tmp_path / "landq", f"{int(T0 * 1e9):020d}-T.S", req)
    landq(qenv, "--drain")
    ((_, rec),) = [x for x in land_records.records(tmp_path / "landq") if x[0].endswith("-T.S")]
    assert rec["why"] == "tip moved after enqueue", rec
    assert {"started", "finished", "slot_wait_s"} <= set(rec), rec


def test_the_remote_shares_fallbacks_are_counted_by_cause(tmp_path):
    """GOH.REMOTEMUX: each land record's `pre_run.remote` (lib/remote_lane.py) is counted: the
    shares sent, the fallbacks by cause, and the shares that left a broken ssh multiplexer for a
    fresh connection (the mux fault, now recovered instead of sent back to the Mac)."""

    def pre(outcome: str, cause: str | None = None, fresh: bool = False) -> dict:
        return {"pre_run": {"run_s": 1, "remote": {"tests": 9, "outcome": outcome,
                                                   "cause": cause, "fresh": fresh}}}  # fmt: skip

    put(tmp_path, 0, "A", "alpha", "landed", 600, started=iso(10), **pre("remote"))
    put(tmp_path, 10, "B", "alpha", "landed", 700, started=iso(20), **pre("remote", fresh=True))
    put(tmp_path, 20, "C", "alpha", "landed", 800, started=iso(30), **pre("fallback", "busy"))
    put(tmp_path, 30, "D", "alpha", "landed", 900, started=iso(40), **pre("fallback", "busy"))
    put(tmp_path, 40, "E", "alpha", "landed", 950, started=iso(50), **pre("fallback", "mux", True))
    put(tmp_path, 50, "F", "alpha", "landed", 990, started=iso(60))  # no share sent
    rep = lr.build(lr.read_attempts(tmp_path), "")
    want = {"shares": 5, "fallbacks": {"busy": 2, "mux": 1}, "fresh": 2}
    assert rep["rows"][0]["remote"] == want and rep["totals"]["remote"] == want
    assert "remote shares: 5, fell back 3 (busy 2, mux 1), 2 on a fresh ssh connection" in (
        lr.render(rep)
    )
