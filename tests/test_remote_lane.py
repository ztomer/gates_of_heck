"""The land pre-step's remote share (GOH.REMOTEWIRE, lib/remote_lane.py): the Linux-safe tests on
the worker beside the Mac's, their verdicts joined, and the Mac running them itself whenever the
worker cannot -- the land never fails because of the worker."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))
from lib import affected_verdict as av, durations, remote_lane as rl

TREE_READS = ("tests/linux_safe.txt", "tests/mac_skips.txt")  # goh's own recorded lists

JUNIT = (
    '<?xml version="1.0"?><testsuites><testsuite name="pytest">'
    '<testcase classname="tests.test_a" name="test_one" time="0.1"/>'
    '<testcase classname="tests.test_a.TestC" name="test_two[x]" time="0.1"/>'
    '<testcase classname="tests.test_a" name="test_skip"><skipped message="no tool"/></testcase>'
    "</testsuite></testsuites>"
)


@pytest.fixture
def tree(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("GOH_REMOTE_HOST", "fakehost")  # inside the suite, only a named host
    monkeypatch.setenv("GOH_REMOTE_SHARE", "1")  # every Linux-safe node may go, unless a test says
    monkeypatch.setenv("GOH_TEST_DURATIONS", str(tmp_path / "goh.db"))  # the rates beside it
    (tmp_path / "tests").mkdir()
    for name in ("test_a.py", "test_b.py", "test_c.py"):
        (tmp_path / "tests" / name).write_text("def test_x(): pass\n")
    (tmp_path / rl.LINUX_SAFE).write_text("# recorded\ntest_a\ntest_b\n")
    return tmp_path


def test_only_files_recorded_linux_safe_go_to_the_worker(tree: Path, monkeypatch):
    nodes = ["tests/test_a.py", "tests/test_b.py::test_x", "tests/test_c.py"]
    assert rl.split(tree, nodes) == (["tests/test_a.py", "tests/test_b.py::test_x"],
                                     ["tests/test_c.py"])  # fmt: skip
    monkeypatch.setenv("GOH_LAND_REMOTE", "off")
    assert rl.split(tree, nodes) == ([], nodes)
    monkeypatch.delenv("GOH_LAND_REMOTE")
    monkeypatch.delenv("GOH_REMOTE_HOST")
    if os.environ.get("GATES_OF_HECK_SUITE_RUNNER"):
        assert rl.split(tree, nodes) == ([], nodes)  # a running suite never reaches the real host
    (tree / rl.LINUX_SAFE).unlink()
    assert rl.split(tree, nodes) == ([], nodes)  # a tree that recorded no list sends nothing


def test_the_worker_takes_the_costliest_safe_files_up_to_its_share(tree: Path, monkeypatch):
    """Both sides should end together: the worker's seconds stay within GOH_REMOTE_SHARE of the
    set's, the costliest safe files first; an unknown test costs 1 s."""
    (tree / rl.LINUX_SAFE).write_text("test_a\ntest_b\n")
    durations = {"tests/test_a.py::t1": 20.0, "tests/test_a.py::t2": 10.0,
                 "tests/test_b.py::t1": 25.0, "tests/test_c.py::t1": 45.0}  # fmt: skip
    nodes = ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]
    monkeypatch.setenv("GOH_REMOTE_SHARE", "0.3")  # 30 of the set's 100 s
    assert rl.split(tree, nodes, durations) == (["tests/test_a.py"],
                                                ["tests/test_b.py", "tests/test_c.py"])  # fmt: skip
    monkeypatch.setenv("GOH_REMOTE_SHARE", "0.55")  # 55 s: a (30) then b (25)
    assert rl.split(tree, nodes, durations)[0] == ["tests/test_a.py", "tests/test_b.py"]
    assert rl.cost(["tests/test_d.py"], durations) == {"tests/test_d.py": 1.0}


def test_unset_share_balances_both_sides_by_their_measured_rates(tree: Path, monkeypatch):
    """GOH.REMOTESHARE: with no fixed share, a safe file goes while the worker's estimated wall
    (its overhead plus its measured seconds over its width) stays within the Mac's remaining wall
    (the Mac's seconds, the judges' included, over the Mac's measured width)."""
    monkeypatch.delenv("GOH_REMOTE_SHARE")
    (tree / rl.LINUX_SAFE).write_text("test_a\ntest_b\n")
    durations = {"tests/test_a.py::t1": 40.0, "tests/test_b.py::t1": 30.0,
                 "tests/test_c.py::t1": 30.0}  # fmt: skip
    nodes = ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]
    rates = rl.Rates(mac_width=4.0, worker_width=2.0, overhead_s=5.0, ratio=1.0,
                     tests={"tests/test_a.py::t1": 20.0})  # fmt: skip
    # a: 5 + 20/2 = 15 <= (100 - 40)/4 = 15: goes; b: 15 + 30/2 = 30 > (60 - 30)/4: stays
    assert rl.split(tree, nodes, durations, rates) == (
        ["tests/test_a.py"],
        ["tests/test_b.py", "tests/test_c.py"],
    )
    # the judges' 100 Mac seconds leave the Mac longer: b goes too (30 <= (160 - 30)/4)
    assert rl.split(tree, nodes, durations, rates, mac_else=100.0)[0] == nodes[:2]
    # a worker never measured on a file takes the Mac's seconds times the measured ratio
    slow = rl.Rates(mac_width=4.0, worker_width=2.0, overhead_s=5.0, ratio=3.0, tests={})
    assert rl.split(tree, nodes, durations, slow) == ([], nodes)  # a: 5 + 120/2 > 15


def test_a_finished_share_measures_both_sides_for_the_next_split(tree: Path, monkeypatch):
    """The worker's per-test seconds, its width (case seconds over the suite's wall) and its
    overhead come from its junit; the Mac's width from its estimated seconds over its wall."""
    junit = ('<?xml version="1.0"?><testsuites><testsuite name="pytest" time="10">'
             '<testcase classname="tests.test_a" name="t1" time="12"/>'
             '<testcase classname="tests.test_a" name="t2" time="8"/>'
             "</testsuite></testsuites>")  # fmt: skip
    durations.record({"dur": {"tests/test_a.py::t1": 6.0, "tests/test_a.py::t2": 4.0}})
    share = _share(tree, 0, junit)
    share.started, share.mac_s = 1000.0, 80.0
    os.utime(share.junit, (1030.0, 1030.0))  # the worker's junit landed 30 s after the start
    # the Mac's side ended 40 s in
    assert rl.end(tree, share, "", av.merge_records, lambda ns: 0, clock=lambda: 1040.0) == 0
    got = rl.load_rates()
    assert got.tests == {"tests/test_a.py::t1": 12.0, "tests/test_a.py::t2": 8.0}
    assert got.worker_width == pytest.approx((2.6 + 20 / 10) / 2)  # smoothed with the default
    assert got.mac_width == pytest.approx((8.0 + 80 / 40) / 2)
    assert got.overhead_s == pytest.approx((20.0 + 30 - 10) / 2)
    assert got.ratio == pytest.approx((1.0 + 20 / 10) / 2)


def test_a_junit_case_is_named_by_its_pytest_node_id(tree: Path):
    assert rl.passed(tree, JUNIT) == ["tests/test_a.py::test_one",
                                      "tests/test_a.py::TestC::test_two[x]"]  # fmt: skip


class _Done:
    """A shard that has already exited `rc`: `end` reads its exit code, log and junit."""

    def __init__(self, rc: int) -> None:
        self.rc = rc

    def wait(self) -> int:
        return self.rc

    def kill(self) -> None:
        pass


def _share(tree: Path, rc: int, junit: str = JUNIT) -> rl.Share:
    (tree / "log").write_text("remote said\n")
    (tree / "j.xml").write_text(junit)
    return rl.Share(["tests/test_a.py"], _Done(rc), tree / "j.xml", tree / "log")


def _record(path: Path, passed: dict[str, bool]) -> None:
    path.write_text(json.dumps({"identity": "t", "sha": "s", "passed": passed}))


def test_a_green_worker_joins_its_passes_to_the_record(tree: Path):
    record = tree / "record.json"
    _record(record, {"tests/test_c.py::test_x": False})
    calls: list[list[str]] = []
    rc = rl.end(tree, _share(tree, 0), str(record), av.merge_records,
                lambda ns: calls.append(ns) or 0)  # fmt: skip
    assert rc == 0 and calls == []
    got = json.loads(record.read_text())
    # GOH.REMOTESHARE: a Linux verdict is kept apart from the Mac's passes, so the gate's reuse
    # (tests/_land_verdicts.py, lib/pre_reuse.py `suite`) runs them on the Mac again
    assert got["passed"] == {"tests/test_c.py::test_x": False}
    assert got["remote"] == ["tests/test_a.py::TestC::test_two[x]", "tests/test_a.py::test_one"]
    assert not (tree / "j.xml").exists() and not (tree / "log").exists()


FAILED = JUNIT.replace(
    'name="test_one" time="0.1"/>',
    'name="test_one" time="0.1"><failure message="boom"/></testcase>',
)


@pytest.mark.parametrize("mac_rc", [0, 1])
def test_a_remote_failure_is_re_run_on_the_mac_whose_verdict_stands(tree: Path, capsys, mac_rc):
    ran: list[list[str]] = []
    rc = rl.end(tree, _share(tree, 1, FAILED), "", av.merge_records,
                lambda ns: ran.append(ns) or mac_rc)  # fmt: skip
    assert ran == [["tests/test_a.py::test_one"]] and rc == mac_rc
    got = capsys.readouterr()
    assert ("failed only on the worker" in got.out + got.err) == (mac_rc == 0)


def test_a_fallback_runs_the_share_on_the_mac_and_merges_both_records(tree: Path, capsys):
    record = tree / "record.json"
    _record(record, {"tests/test_c.py::test_x": False})

    def mac(nodes: list[str]) -> int:  # the Mac's pytest writes the record whole, as pytest.sh does
        assert not record.exists()
        _record(record, {f"{n}::test_x": False for n in nodes})
        return 0

    assert rl.end(tree, _share(tree, rl.FALLBACK), str(record), av.merge_records, mac) == 0
    assert set(json.loads(record.read_text())["passed"]) == {"tests/test_c.py::test_x",
                                                             "tests/test_a.py::test_x"}  # fmt: skip
    assert "fell back" in capsys.readouterr().out


def test_an_unreachable_worker_falls_back_and_the_pre_step_still_passes(tmp_path: Path, monkeypatch,
                                                                        capsys):  # fmt: skip
    """The real shard against a host that never answers (an ssh that exits 255): exit 3, the
    Mac runs the share, the verdict is the Mac's green."""
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "ssh").write_text("#!/bin/sh\nexit 255\n")
    (fake / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GOH_REMOTE_HOST", "unreachable.invalid")
    nodes = ["tests/test_remote_lane.py"]
    monkeypatch.setattr(rl, "split", lambda root, ns, *_a, **_k: (list(ns), []))
    local, share = rl.begin(REPO_ROOT, nodes)
    assert local == [] and share is not None
    ran: list[list[str]] = []
    rc = rl.end(REPO_ROOT, share, "", av.merge_records, lambda ns: ran.append(ns) or 0)
    assert rc == 0 and ran == [nodes]
    out = capsys.readouterr().out
    assert "unreachable" in out and "fell back (exit 3)" in out


def test_the_lists_are_recorded_from_one_full_run_on_each_side(tree: Path):
    """A file is Linux-safe only when the worker failed none of it and skipped exactly what the
    Mac skipped; the Mac skip set is named as junit names it, the shard's parity key."""
    a, b, c = "tests/test_a.py", "tests/test_b.py", "tests/test_c.py"
    linux = {f"{a}::t1": "passed", f"{a}::C::t2": "skipped", f"{b}::t1": "failed",
             f"{c}::t1": "skipped"}  # fmt: skip
    mac = {f"{a}::t1": "passed", f"{a}::C::t2": "skipped", f"{b}::t1": "passed",
           f"{c}::t1": "passed"}  # fmt: skip
    safe, skips = rl.record(tree, linux, mac)
    assert safe == [a]  # b failed on Linux; c skipped there what the Mac ran
    assert skips == [f"{a}::C::t2"]
    assert rl.junit_name(f"{a}::C::t2") == "tests.test_a.C::t2"


def test_a_mac_run_of_some_files_judges_those_exactly_and_the_rest_by_no_worker_skip(tree: Path):
    """GOH.REMOTESHARE: the Mac junit need cover only the files the worker skipped in; a file it
    does not cover is safe only when the worker skipped nothing there."""
    a, b, c = "tests/test_a.py", "tests/test_b.py", "tests/test_c.py"
    linux = {f"{a}::t1": "skipped", f"{b}::t1": "passed", f"{c}::t1": "skipped"}
    mac = {f"{a}::t1": "skipped"}  # the Mac ran a only
    assert rl.record(tree, linux, mac) == ([a, b], [f"{a}::t1"])


def test_the_gate_s_suite_reuse_refuses_a_record_with_worker_passes(tmp_path: Path, monkeypatch):
    """The ship and the land gate rerun the worker's share on the Mac, and say so."""
    from lib import pre_reuse

    # Run under the coverage step's instrumented suite, suite() refuses for that reason first.
    for key in ("GOH_COVERAGE_BIN_DIR", "LLVM_PROFILE_FILE"):
        monkeypatch.delenv(key, raising=False)

    rec = tmp_path / "rec.json"
    rec.write_text(json.dumps({"identity": "t", "passed": {}, "selection": "affected",
                               "base": "b" * 40, "remote": ["tests/test_a.py::t"]}))  # fmt: skip
    ok, why, _ = pre_reuse.suite(tmp_path, rec, "b" * 40)
    assert not ok and "1 test passed on the Linux worker" in why


def test_goh_s_recorded_lists_name_only_what_exists():
    """A stale entry fails: a deleted or renamed test file leaves the list, a recorded skip names a
    file the list keeps (close-the-class: a ratchet whose stale rows are red)."""
    safe = rl.listed(REPO_ROOT, rl.LINUX_SAFE)
    assert safe, f"{rl.LINUX_SAFE} is recorded"
    assert sorted(f for f in safe if not (REPO_ROOT / "tests" / f"{f}.py").is_file()) == []
    dotted = {f"tests.{f}" for f in safe}
    skips = rl.listed(REPO_ROOT, rl.MAC_SKIPS)
    assert sorted(s for s in skips if not any(s.startswith((d + ".", d + "::"))
                                              for d in dotted)) == []  # fmt: skip


def test_with_no_other_mac_run_one_node_stays_on_the_mac_for_the_record(tree: Path, monkeypatch):
    """tools/land.sh refuses a green pre-step with no verdict record, which only the Mac's pytest
    writes: a share that would take every node leaves one behind."""
    started: list[list[str]] = []

    class Proc:
        def __init__(self, argv, **_kw):
            started.append(argv)

    monkeypatch.setattr(rl.subprocess, "Popen", Proc)
    local, share = rl.begin(tree, ["tests/test_a.py", "tests/test_b.py"], mac_runs=False)
    assert local == ["tests/test_a.py"] and share is not None and share.nodes == ["tests/test_b.py"]
    assert started[0][-1] == "tests/test_b.py"
    rl._cleanup(share)
    local, share = rl.begin(tree, ["tests/test_a.py"], mac_runs=False)
    assert local == ["tests/test_a.py"] and share is None


@pytest.mark.parametrize(
    ("said", "cause"),
    [
        ("⚠ media-server busy: load 9.62 > 6.0 -- the caller runs the shard locally", "busy"),
        (("mm_send_fd: sendmsg(2): Message too long\nmux_client_request_session: send fds failed\n"
          "⚠ the remote run did not finish (ssh exit 255) -- the caller runs"), "mux"),
        ("⚠ the remote run did not finish (ssh exit 255) -- the caller runs", "dropped"),
        ("⚠ media-server unreachable (ssh exit 255) -- the caller runs", "unreachable"),
        ("⚠ refused: the worker is off goh's pins: python 3.14.4 (Mac 3.15.2) -- the", "off-pin"),
        ("⚠ refused: the remote skips differ from the Mac's: t -- the caller", "skips"),
        ("⚠ /mnt/x: 12 GB free, under the 100 GB floor the host's transcodes keep", "scratch"),
        ("⚠ the remote run wrote no junit (pytest exit 4) -- the caller", "no-junit"),
        ("something else entirely", "other"),
    ],
)  # fmt: skip
def test_a_fallback_is_named_by_its_cause(said: str, cause: str):
    """GOH.REMOTEMUX: the land record counts fallbacks by cause, so --report tells a busy worker
    from a broken multiplexer from a worker off goh's pins."""
    assert rl.cause(said) == cause


def test_each_share_s_outcome_reaches_the_land_record(tree: Path, monkeypatch):
    """The share's outcome is written beside the pre-step's figures (GOH_LAND_PRE_TOOK, which
    lib/land_prestep.py reads into the land record's `pre_run.remote`)."""
    took = tree / "took.json"
    monkeypatch.setenv("GOH_LAND_PRE_TOOK", str(took))
    share = _share(tree, rl.FALLBACK)
    share.log.write_text("⚠ media-server busy: load 9.62 > 6.0 -- the caller runs the shard\n")
    rl.end(tree, share, "", av.merge_records, lambda ns: 0)
    got = json.loads(Path(f"{took}.remote").read_text())  # its saving: test_remote_saved
    assert {k: got[k] for k in ("tests", "outcome", "cause", "fresh")} == {
        "tests": 1, "outcome": "fallback", "cause": "busy", "fresh": False}  # fmt: skip
    share = _share(tree, 0)
    share.log.write_text("⚠ the shared ssh connection failed (mux_client...): a fresh connection "
                         "instead\n✓ remote fakehost: 2 passed\n")  # fmt: skip
    rl.end(tree, share, "", av.merge_records, lambda ns: 0)
    got = json.loads(Path(f"{took}.remote").read_text())
    assert {k: got[k] for k in ("tests", "outcome", "cause", "fresh")} == {
        "tests": 1, "outcome": "remote", "cause": None, "fresh": True}  # fmt: skip


@pytest.mark.parametrize("mac_rc", [0, 1])
def test_a_planted_failing_shard_names_its_nodes_in_the_pre_step_log(tree: Path, capsys, mac_rc):
    """GOH.REMOTEREDSILENT: the worker's failures are named where the pre-step's log reads them,
    whatever the Mac's re-run says."""
    rl.end(tree, _share(tree, 1, FAILED), "", av.merge_records, lambda ns: mac_rc)
    got = capsys.readouterr()
    assert "1 failed on the worker: tests/test_a.py::test_one" in got.out + got.err


def test_a_shard_that_exits_1_naming_no_failure_says_so(tree: Path, capsys):
    """An exit 1 with no failed case in its junit (the shard itself broke) is no test failure:
    it is said as the shard's own exit, its last lines above, and the Mac runs the share."""
    ran: list[list[str]] = []
    assert (
        rl.end(tree, _share(tree, 1, ""), "", av.merge_records, lambda ns: ran.append(ns) or 0) == 0
    )
    out = capsys.readouterr().out
    assert ran == [["tests/test_a.py"]] and "failed on the worker" not in out, out
    assert "the remote shard exited 1 naming no failed test" in out, out


def test_a_share_stopped_by_the_mac_s_red_says_so(tree: Path, capsys):
    """The Mac's run went red first: the share is stopped, and the log says what became of it."""
    rl.cancel(_share(tree, 0))
    assert "remote share of 1 test stopped: the Mac's run went red first" in capsys.readouterr().out
