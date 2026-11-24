"""Portable tests' Linux verdicts count at the gate (GOH.REMOTETRUST, lib/remote_trust.py): a test
is platform_agnostic or macos_only, the gate's suite takes the worker's passes of the first and runs
the second on the Mac, and the post-merge full Mac run reclassifies a planted Linux-only pass."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))
from lib import durations, idle_red, pre_reuse, remote_lane as rl, remote_trust as rt


@pytest.fixture
def tree(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("GOH_TEST_DURATIONS", str(tmp_path / "goh.db"))  # the ledger beside
    monkeypatch.setenv("GOH_REMOTE_HOST", "fakehost")  # inside the suite, only a named host
    monkeypatch.setenv("GOH_REMOTE_SHARE", "1")  # every Linux-safe node may go
    for key in ("GOH_COVERAGE_BIN_DIR", "LLVM_PROFILE_FILE"):  # suite() refuses those first
        monkeypatch.delenv(key, raising=False)
    (tmp_path / "tests").mkdir()
    for name in ("test_a.py", "test_b.py", "test_mac.py"):
        (tmp_path / "tests" / name).write_text("def test_x(): pass\n")
    (tmp_path / rl.LINUX_SAFE).write_text("# recorded\ntest_a\ntest_b\n")
    return tmp_path


def test_every_test_is_platform_agnostic_or_macos_only(tree: Path):
    assert rt.platform(tree, "tests/test_a.py::test_x") == rt.AGNOSTIC
    assert rt.platform(tree, "tests/test_mac.py::test_x") == rt.MAC_ONLY
    assert rt.platform(tree, "crates/x.rs") == rt.MAC_ONLY  # not a test module: never trusted
    durations.update("linux_unsafe", lambda was: {"test_b": "a shadow saw them disagree"})
    assert rt.split(tree, ["tests/test_b.py::t", "tests/test_a.py::t"]) == (
        ["tests/test_a.py::t"], ["tests/test_b.py::t"])  # fmt: skip


def _suite(tree: Path, remote: list[str]) -> tuple[bool, str, str]:
    rec = tree / "rec.json"
    rec.write_text(json.dumps({"identity": "t", "passed": {}, "selection": "affected",
                               "base": "b" * 40, "remote": remote}))  # fmt: skip
    return pre_reuse.suite(tree, rec, "b" * 40)


def test_the_gate_takes_platform_agnostic_worker_passes(tree: Path):
    """Agnostic passes no longer stop the reuse: the next check decides (here, a tmp dir that is
    no commit's tree); a macos_only pass still sends the suite to the Mac, named."""
    ok, why, _ = _suite(tree, ["tests/test_a.py::test_x", "tests/test_b.py::test_x"])
    assert not ok and "Linux worker" not in why and "not its commit's tree" in why, why
    ok, why, _ = _suite(tree, ["tests/test_a.py::test_x", "tests/test_mac.py::test_x"])
    assert not ok and "macos_only tests passed on the Linux worker: 1," in why, why
    assert "tests/test_mac.py::test_x" in why


def test_the_post_merge_mac_sweep_reclassifies_a_planted_linux_only_pass(tree: Path):
    """A test the gate trusted from Linux (test_a) fails in the idle full Mac run: its module joins
    the ledger, macos_only from then on, and the gate never takes its Linux pass again."""
    assert rl.split(tree, ["tests/test_a.py::test_x"])[0] == ["tests/test_a.py::test_x"]
    log = tree / "idle.log"
    log.write_text("FAILED tests/test_a.py::test_x - assert 0\nFAILED tests/test_mac.py::test_x\n")
    said = idle_red.sweep(tree, "c" * 40, log)
    assert "test_a" in said and "test_mac" not in said, said
    assert "test_a" in rl.unsafe() and rt.platform(tree, "tests/test_a.py::test_x") == rt.MAC_ONLY
    ok, why, _ = _suite(tree, ["tests/test_a.py::test_x"])
    assert not ok and "macos_only" in why
    assert rl.split(tree, ["tests/test_a.py::test_x"])[0] == []  # nor is it sent again
    assert idle_red.sweep(tree, "c" * 40, log) == ""  # once: it is macos_only already
