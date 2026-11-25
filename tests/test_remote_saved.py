"""What the Linux worker saved (GOH.REMOTESAVED, lib/remote_saved.py): every share -- the enqueue's
pre-step, the lander's pre-land, the land gate's suite -- leaves one figure (its host, what it was
sent, both sides' walls, the Mac-only estimate and the saving), the land record carries them, and
`goh land --report` sums them per day. The arithmetic below is by hand."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(1, str(REPO_ROOT / "lib"))
import land_records
import landq_remote
import landq_report
from test_landq_report import iso, put

from lib import affected_verdict as av, remote_gate as rg, remote_lane as rl, remote_saved as rs

JUNIT = (
    '<?xml version="1.0"?><testsuites><testsuite name="pytest" time="1">'
    '<testcase classname="tests.test_a" name="test_one" time="0.1"/>'
    "</testsuite></testsuites>"
)
FAILED = JUNIT.replace('time="0.1"/>', 'time="0.1"><failure message="boom"/></testcase>')
SAID = "→ remote-shard: host worker-b\n✓ remote worker-b: 1 passed\n"


@pytest.fixture
def tree(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("GOH_REMOTE_HOST", "fakehost")
    monkeypatch.setenv("GOH_TEST_DURATIONS", str(tmp_path / "goh.db"))
    monkeypatch.delenv("GOH_LAND_PRE_TOOK", raising=False)
    monkeypatch.delenv("GOH_LAND_PRE_ONLY", raising=False)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_one(): pass\n")
    (tmp_path / rl.LINUX_SAFE).write_text("test_a\n")
    return tmp_path


class _Done:
    def __init__(self, rc: int) -> None:
        self.rc = rc

    def wait(self) -> int:
        return self.rc

    def poll(self) -> int:
        return self.rc

    def kill(self) -> None:
        pass


class _Clock:
    """The Mac's run ends at 100 s, the worker's at 130 s; a re-run on the Mac takes 40 s."""

    def __init__(self, *ticks: float) -> None:
        self.ticks = list(ticks)

    def __call__(self) -> float:
        return self.ticks.pop(0)


def _share(tree: Path, rc: int, junit: str = JUNIT, said: str = SAID) -> rl.Share:
    (tree / "log").write_text(said)
    (tree / "j.xml").write_text(junit)
    share = rl.Share(["tests/test_a.py"], _Done(rc), tree / "j.xml", tree / "log", started=1000.0)
    share.est_s = 90.0  # the Mac-only estimate's extra: what the Mac would have spent on the share
    return share


def test_the_saving_is_the_mac_only_estimate_less_what_the_step_took():
    """Mac side 100 s, worker 130 s, the share worth 90 s of Mac: Mac-only 190 s, took 130 s."""
    fig = rs.figure("pre-land", SAID, 5, "remote", 1000.0, 1100.0, 1130.0, 90.0, 0.0)
    assert fig == {"where": "pre-land", "host": "worker-b", "tests": 5, "outcome": "remote",
                   "worker_s": 130.0, "mac_s": 100.0, "mac_est_s": 90.0, "rerun_s": 0.0,
                   "saved_s": 60.0, "t0": 1000.0}  # fmt: skip
    # a fallback: the worker gave up at 5 s and the Mac ran the share itself (85 s): nothing saved
    # but the 5 s it did not cost, against the estimate it re-ran
    late = rs.figure("gate", "", 5, "fallback", 1000.0, 1100.0, 1005.0, 90.0, 85.0)
    assert late["saved_s"] == 5.0 and late["host"] == "?"
    # a worker 200 s behind the Mac costs time: 100 + 90 - 300, negative
    behind = rs.figure("gate", SAID, 5, "remote", 1000.0, 1100.0, 1300.0, 90.0, 0.0)
    assert behind["saved_s"] == -110


def test_the_pre_land_share_prints_its_figure_into_the_land_s_log(tree: Path, capsys):
    """The lander's pre-land (no GOH_LAND_PRE_ONLY) says its figure where the land's log is."""
    rl.end(tree, _share(tree, 0), "", av.merge_records, lambda ns: 0, clock=_Clock(1100.0, 1130.0))
    figs = rs.of_log(capsys.readouterr().out)
    assert [(f["where"], f["host"], f["tests"], f["saved_s"]) for f in figs] == [
        ("pre-land", "worker-b", 1, 60.0)
    ]


def test_a_failure_given_back_counts_the_mac_s_re_run(tree: Path, capsys):
    rl.end(tree, _share(tree, 1, FAILED), "", av.merge_records, lambda ns: 0,
           clock=_Clock(1100.0, 1130.0, 1130.0, 1170.0))  # fmt: skip
    (fig,) = rs.of_log(capsys.readouterr().out)
    assert fig["outcome"] == "failed" and fig["rerun_s"] == 40.0 and fig["saved_s"] == 20.0


def test_the_enqueue_s_share_keeps_its_figure_in_the_record_s_pre_run(tree: Path, monkeypatch):
    took = tree / "took.json"
    monkeypatch.setenv("GOH_LAND_PRE_TOOK", str(took))
    monkeypatch.setenv("GOH_LAND_PRE_ONLY", "1")
    rl.end(tree, _share(tree, 0), "", av.merge_records, lambda ns: 0, clock=_Clock(1100.0, 1130.0))
    got = json.loads(Path(f"{took}.remote").read_text())
    assert got["outcome"] == "remote" and got["where"] == "enqueue" and got["saved_s"] == 60.0
    assert got["host"] == "worker-b" and got["mac_est_s"] == 90.0


def test_the_gate_s_share_says_its_figure_and_the_glue_keeps_it(tree: Path, monkeypatch):
    """The gate's suite share (lib/remote_gate.py `finish`) says its figure among its lines; the
    glue keeps those lines beside the verdict record, which tools/land.sh copies into the log."""
    monkeypatch.setattr(rg, "measure", lambda *a: None)
    green, lines = rg.finish(tree, _share(tree, 0), False, {}, lambda fs: 0,
                             _Clock(1100.0, 1130.0))  # fmt: skip
    assert green
    figs = rs.of_log("\n".join(lines))
    assert [(f["where"], f["saved_s"]) for f in figs] == [("gate", 60.0)]
    shares = tree / "rec.json.shares"
    rs.keep(lines, str(shares))
    assert rs.of_log(shares.read_text()) == figs


def test_the_land_record_carries_every_share_its_log_says(tmp_path: Path):
    """The lander reads the land's log into `remote_shares`; --report sums them per day, the
    enqueue's `pre_run.remote` with them, each share once across a batch's two records."""
    pre = rs.figure("pre-land", SAID, 3, "remote", 10.0, 110.0, 130.0, 90.0, 0.0)  # 100+90-120 = 70
    gate = rs.figure("gate", "", 2, "remote", 20.0, 320.0, 300.0, 120.0, 0.0)  # 300+120-300 = 120
    enq = rs.figure("enqueue", SAID, 4, "remote", 5.0, 65.0, 50.0, 30.0, 0.0)  # 60+30-60 = 30
    log = f"noise\n{rs.line(pre)}\n  {rs.line(gate)}\nremote share: {{not json\n"
    assert landq_remote.shares({"log": "/nonexistent"}) == {}
    a = put(tmp_path, 0, "A", "alpha", "landed", 600, started=iso(10), log=log,
            pre_run={"run_s": 1, "remote": enq})  # fmt: skip
    fields = landq_remote.shares({"log": str(a)})
    assert fields == {"remote_shares": [pre, gate]}
    for run, rec in land_records.records(tmp_path):  # the lander's write, as tools/landq.py does
        land_records.write(tmp_path, run, {**rec, **fields})
    b = put(tmp_path, 1, "B", "alpha", "landed", 600, started=iso(10), log=log, **fields)  # a batch
    assert b.exists()
    rep = landq_report.build(landq_report.read_attempts(tmp_path), "")
    saved = rep["rows"][0]["remote"]["saved"]
    assert saved["shares"] == 3 and saved["saved_s"] == 220.0
    assert saved["hosts"] == {"?": 1, "worker-b": 2}
    assert saved["where"] == {"enqueue": 1, "gate": 1, "pre-land": 1}
    assert rep["totals"]["remote"]["saved"]["saved_s"] == 220.0
    assert "worker saved 3.7 min over 3 shares" in landq_report.render(rep)


def test_the_real_shard_names_its_host_in_the_figure(tmp_path: Path, monkeypatch, capsys):
    """tools/remote_shard.py says which worker it is before anything can fall back: a share to a
    host that never answers is still that host's, in its figure."""
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "ssh").write_text("#!/bin/sh\nexit 255\n")
    (fake / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GOH_REMOTE_HOST", "unreachable.invalid")
    monkeypatch.delenv("GOH_LAND_PRE_TOOK", raising=False)
    monkeypatch.delenv("GOH_LAND_PRE_ONLY", raising=False)
    nodes = ["tests/test_remote_saved.py"]
    share = rl.launch(REPO_ROOT, nodes, 0.0)
    rl.end(REPO_ROOT, share, "", av.merge_records, lambda ns: 0)
    (fig,) = rs.of_log(capsys.readouterr().out)
    assert fig["host"] == "unreachable.invalid" and fig["outcome"] == "fallback", fig


def test_a_worker_done_before_the_mac_is_timed_by_its_junit(tree: Path, capsys):
    """The first measured share (2026-11-24, 248 tests): the worker had finished long before the
    Mac, and its wall read the Mac's (1140.2 s both) because the wait returned at once. Its own
    end is its junit's write: 1000 + 70 here, while the Mac ends at 1100."""
    share = _share(tree, 0)
    os.utime(share.junit, (1070.0, 1070.0))
    rl.end(tree, share, "", av.merge_records, lambda ns: 0, clock=_Clock(1100.0, 1100.5))
    (fig,) = rs.of_log(capsys.readouterr().out)
    assert fig["worker_s"] == 70.0 and fig["mac_s"] == 100.0 and fig["saved_s"] == 90.0, fig
