"""The land gate's remote share (GOH.REMOTEGATE, lib/remote_gate.py, tests/_land_remote.py): the
gate's Linux-safe files run on the worker beside the Mac's run and its verdict counts for the land;
a failure there is red, a fallback runs on the Mac, and a sampled shadow that sees the platforms
disagree takes the module out of tests/linux_safe.txt by itself."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))
import _suite_plugins

from lib import durations, remote_gate as rg, remote_lane as rl

JUNIT = (
    '<?xml version="1.0"?><testsuites><testsuite name="pytest" time="1">'
    '<testcase classname="tests.test_a" name="test_one" time="0.1"/>'
    '<testcase classname="tests.test_b" name="test_two" time="0.1"/>'
    "</testsuite></testsuites>"
)
FAILED = JUNIT.replace(
    'name="test_one" time="0.1"/>',
    'name="test_one" time="0.1"><failure message="boom"/></testcase>',
)


@pytest.fixture
def tree(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("GOH_REMOTE_HOST", "fakehost")  # inside the suite, only a named host
    monkeypatch.setenv("GOH_REMOTE_SHARE", "1")  # every Linux-safe file may go
    monkeypatch.setenv("GOH_TEST_DURATIONS", str(tmp_path / "goh.db"))  # the ledger beside
    (tmp_path / "tests").mkdir()
    for name in ("test_a.py", "test_b.py", "test_c.py"):
        (tmp_path / "tests" / name).write_text("def test_x(): pass\n")
    (tmp_path / rl.LINUX_SAFE).write_text("# recorded\ntest_a\ntest_b\n")
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


def _share(tree: Path, rc: int, junit: str = JUNIT, said: str = "") -> rl.Share:
    (tree / "log").write_text(said)
    (tree / "j.xml").write_text(junit)
    return rl.Share(["tests/test_a.py", "tests/test_b.py"], _Done(rc), tree / "j.xml", tree / "log")


def _never(files: list[str]) -> int:
    raise AssertionError(f"the Mac ran {files}")


def test_a_planted_remote_failure_is_red_and_named(tree: Path):
    """A failure on both platforms is red, named; the Mac re-ran only the failed node."""
    ran: list[list[str]] = []
    green, lines = rg.finish(
        tree, _share(tree, 1, FAILED), False, {}, lambda fs: ran.append(fs) or 1
    )
    assert not green and ran == [["tests/test_a.py::test_one"]]
    assert "failed on the Linux worker and on the Mac: tests/test_a.py::test_one" in lines[0]


def test_a_linux_only_failure_leaves_the_list_and_the_macs_green_stands(tree: Path):
    green, lines = rg.finish(tree, _share(tree, 1, FAILED), False, {}, lambda fs: 0)
    assert green and "failed only on the Linux worker" in lines[0], lines
    assert durations.get("linux_unsafe").keys() == {"test_a"}


def test_the_workers_passes_count_for_the_land(tree: Path):
    green, lines = rg.finish(tree, _share(tree, 0), False, {}, _never)
    assert green and "passed on the remote worker" in lines[0] and "ship reruns" in lines[0]
    assert not (tree / "j.xml").exists() and not (tree / "log").exists()


@pytest.mark.parametrize("mac_rc", [0, 1])
def test_a_fallback_runs_the_files_on_the_mac_whose_exit_decides(tree: Path, mac_rc: int):
    ran: list[list[str]] = []
    share = _share(tree, rl.FALLBACK, "", "⚠ fakehost busy: load 9.62 > 6.0 -- the caller runs")
    green, lines = rg.finish(tree, share, False, {}, lambda fs: ran.append(fs) or mac_rc)
    assert ran == [["tests/test_a.py", "tests/test_b.py"]] and green == (mac_rc == 0)
    assert "fell back (fallback: busy)" in lines[0]


def test_a_shadow_disagreement_takes_the_module_out_of_linux_safe(tree: Path):
    """A planted Mac-only failure: the Mac's verdict stands (green here is the shadow's own word,
    the Mac's run decides the session), and test_a leaves the list for every later split."""
    nodes = ["tests/test_a.py", "tests/test_b.py"]
    assert rl.split(tree, nodes)[0] == nodes
    mac = {"tests/test_a.py::test_one": False, "tests/test_b.py::test_two": True}
    green, lines = rg.finish(tree, _share(tree, 0), True, mac, _never)
    assert green and "test_a leaves tests/linux_safe.txt" in lines[0], lines
    assert "passed on Linux" in lines[0]
    assert durations.get("linux_unsafe").keys() == {"test_a"}
    assert rl.split(tree, nodes) == (["tests/test_b.py"], ["tests/test_a.py"])


def test_an_agreeing_shadow_leaves_the_list_alone(tree: Path):
    mac = {"tests/test_a.py::test_one": True, "tests/test_b.py::test_two": True}
    green, lines = rg.finish(tree, _share(tree, 0), True, mac, _never)
    assert green and "agree" in lines[0] and durations.get("linux_unsafe") is None


def test_the_gate_sends_safe_files_with_work_left_after_the_carry(tree: Path, monkeypatch):
    """A file whose every known test the carry deselected is not sent; a file the table never saw
    is; the gate's controller is the suite's runner, so the in-suite guard does not stop it."""
    monkeypatch.setenv("GATES_OF_HECK_SUITE_RUNNER", "1")
    durations = {"tests/test_a.py::t1": 5.0, "tests/test_b.py::t1": 4.0, "tests/test_c.py::t1": 9.0}
    files, mac_s = rg.choose(tree, {"tests/test_a.py::t1"}, durations)
    assert files == ["tests/test_b.py"] and mac_s == 9.0
    (tree / "tests" / "test_d.py").write_text("def test_x(): pass\n")
    (tree / rl.LINUX_SAFE).write_text("test_a\ntest_b\ntest_d\n")
    assert rg.choose(tree, set(), durations)[0] == ["tests/test_a.py", "tests/test_b.py",
                                                     "tests/test_d.py"]  # fmt: skip
    monkeypatch.setenv("GOH_LAND_REMOTE", "off")
    assert rg.choose(tree, set(), durations)[0] == []


def test_only_a_land_gates_whole_plain_run_shares(monkeypatch):
    monkeypatch.delenv("GOH_LAND_REMOTE", raising=False)
    assert rg.off(True, True, False, False) == ""
    assert rg.off(False, True, False, False) and rg.off(True, False, False, False)
    assert "coverage" in rg.off(True, True, True, False)
    assert "shard" in rg.off(True, True, False, True)
    assert "inside the suite" in rg.off(True, True, False, False, top=False)
    monkeypatch.setenv("GOH_LAND_REMOTE", "off")
    assert rg.off(True, True, False, False) == "GOH_LAND_REMOTE=off"


def test_one_gate_in_ten_is_a_shadow(monkeypatch):
    monkeypatch.delenv("GOH_REMOTE_SHADOW", raising=False)
    assert rg.shadowed(lambda n: 0) and not rg.shadowed(lambda n: 3)
    monkeypatch.setenv("GOH_REMOTE_SHADOW", "0")
    assert not rg.shadowed(lambda n: 0)
    monkeypatch.setenv("GOH_REMOTE_SHADOW", "1")
    assert rg.shadowed(lambda n: 3)


def test_the_suite_loads_the_gate_share_plugin():
    assert "_land_remote" in _suite_plugins.pytest_plugins


class _PM:
    def get_plugin(self, name: str) -> None:
        return None


class _Config:
    pluginmanager = _PM()

    class hook:
        dropped: ClassVar[list] = []

        @classmethod
        def pytest_deselected(cls, items: list) -> None:
            cls.dropped.extend(items)


class _Item:
    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid


class _Session:
    def __init__(self, status: int) -> None:
        self.config, self.exitstatus = _Config(), status


@pytest.mark.parametrize(("planted", "status"), [(0, pytest.ExitCode.OK),
                                                 (1, pytest.ExitCode.TESTS_FAILED)])  # fmt: skip
def test_the_plugin_deselects_the_share_and_the_workers_verdict_decides(tree, monkeypatch, planted,
                                                                        status):  # fmt: skip
    """The glue end to end with a planted worker: its files leave the Mac's run, its failure turns
    the session red, its pass leaves it green, and either way the store's record is not `full`."""
    import _land_remote as lr
    import _land_verdicts as lv
    import _schedule
    import _whole_run

    plan = {"skip": set(), "lines": []}
    monkeypatch.setattr(lr, "_state", {})
    monkeypatch.setattr(lr, "ROOT", tree)
    monkeypatch.setitem(lv._KEYS, lv.REUSE, str(tree / "reuse.json"))
    monkeypatch.setattr(lv, "_plan", lambda config: plan)
    monkeypatch.setattr(_whole_run, "whole", lambda config: True)
    monkeypatch.setattr(lr, "_table", lambda: tree / "durations.json")  # the top-level run's
    monkeypatch.setattr(lr, "_run_local", lambda files: 1)  # the Mac fails it too: red
    monkeypatch.setenv("GOH_REMOTE_SHADOW", "0")
    for key in ("GOH_LAND_REMOTE", "GOH_COVERAGE_BIN_DIR", "LLVM_PROFILE_FILE"):
        monkeypatch.delenv(key, raising=False)  # the run's own: a pre-step's coverage run sets them
    monkeypatch.setattr(_schedule, "_shard_spec", None)  # nor a hosted CI shard (remote_gate.off)
    monkeypatch.setattr(rg, "start", lambda root, files, mac_s: _share(
        tree, planted, FAILED if planted else JUNIT))  # fmt: skip
    config = _Config()
    _Config.hook.dropped = []
    items = [_Item("tests/test_a.py::test_one"), _Item("tests/test_c.py::test_x")]
    lr.pytest_collection_modifyitems(config, items)
    assert [i.nodeid for i in items] == ["tests/test_c.py::test_x"]
    session = _Session(pytest.ExitCode.OK)
    lr.pytest_sessionfinish(session)
    assert session.exitstatus == status
    assert plan["other"] == 2  # passed by another verdict: never a full Mac record


class _Hung:
    """A share that never ends on its own: it still runs until it is killed."""

    killed = False

    def poll(self) -> int | None:
        return -9 if self.killed else None

    def wait(self) -> int:
        assert self.killed, "a wait on a share nobody killed"
        return -9

    def kill(self) -> None:
        self.killed = True


def test_a_worker_far_behind_the_mac_is_killed_and_its_files_run_on_the_mac(tree, monkeypatch):
    """A hung share holds the gate GOH_REMOTE_GATE_WAIT_S at most, then the Mac runs its files."""
    monkeypatch.setenv("GOH_REMOTE_GATE_WAIT_S", "0.2")
    share = _share(tree, 0)
    share.proc = _Hung()
    ran: list[list[str]] = []
    green, lines = rg.finish(tree, share, False, {}, lambda fs: ran.append(fs) or 0)
    assert green and ran == [share.nodes] and "fallback: late" in lines[0]
    assert share.proc.killed


def test_a_session_a_test_starts_never_reaches_the_worker(tree, monkeypatch):
    """A nested session (a test's pytest of a tree copy, land keys and all) is handed no duration
    table (tests/_schedule.py `take`): it decides nothing and launches nothing."""
    import _land_remote as lr
    import _land_verdicts as lv
    import _whole_run

    monkeypatch.setattr(lr, "_state", {})
    monkeypatch.setattr(lr, "ROOT", tree)
    monkeypatch.setattr(lr, "_table", lambda: None)
    monkeypatch.setitem(lv._KEYS, lv.REUSE, str(tree / "reuse.json"))
    monkeypatch.setattr(lv, "_plan", lambda config: {"skip": set(), "lines": []})
    monkeypatch.setattr(_whole_run, "whole", lambda config: True)
    monkeypatch.setattr(rg, "start", lambda *a: pytest.fail("a nested session launched a share"))
    assert lr._decide(_Config()) == {"files": [], "share": None, "shadow": False}
