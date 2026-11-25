"""The pre-step's Mac run hands units of its queue to the Linux worker (GOH.REMOTECARRY,
tests/_worker_feed.py): how much, which, and never a unit in neither share."""

from __future__ import annotations

import os
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import _worker_feed as wf
import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))
from lib import remote_feed as rf

ASK = {"n": 1, "safe": ["test_a"], "pool_s": 0.0, "after_s": 0.0, "k": 0.5, "ratio": 1.0,
       "overhead_s": 10.0, "mac_width": 2.0, "least_s": 20.0, "part": 1.0}  # fmt: skip


def test_what_the_worker_may_take_ends_both_sides_together():
    took = wf.want(ASK, 400.0, 2.0)  # the Mac has 200 s left: 190 s of room at 0.5 + 0.5
    assert took == 190.0
    assert (400.0 - took) / 2.0 == pytest.approx(10.0 + took * 0.5)
    assert wf.want(ASK, 50.0, 2.0) == 0.0  # 15 s sooner is not worth a share
    after = wf.want({**ASK, "after_s": 100.0}, 300.0, 2.0)
    assert after == took + wf.SESSION_S  # the rest to come has a bring-up of its own


def test_the_units_taken_are_the_costliest_safe_ones_that_end_in_time():
    queue = {"tests/test_a.py::long": 90.0, "tests/test_m.py::mac": 50.0,
             **{f"tests/test_a.py::t[{i}]": float(i) for i in range(1, 9)}}  # fmt: skip
    safe = {"test_a"}
    got = wf.pick(queue, safe, 14.0, 60.0, keep=2)
    assert got == ["tests/test_a.py::t[8]", "tests/test_a.py::t[7]"]
    assert len(wf.pick(queue, safe, 999.0, 60.0, keep=7)) == 3  # the Mac's workers keep theirs
    assert wf.pick(queue, safe, 0.0, 60.0, keep=0) == []


def test_a_share_s_ids_fit_one_word_of_the_worker_s_command():
    """Linux refuses a single argument over 128 KiB, and the ids travel in one."""
    queue = {f"tests/test_a.py::t[{i:0100d}]": 1.0 for i in range(2000)}
    got = wf.pick(queue, {"test_a"}, 1e6, 60.0, keep=0)
    assert len(got) > 400 and len("\n".join(got)) <= wf.MOST_BYTES < 131072 / 2


def _sched(n: int = 30) -> SimpleNamespace:
    ids = [f"tests/test_a.py::t[{i}]" for i in range(n)]
    return SimpleNamespace(workqueue=OrderedDict((i, {i: False}) for i in ids), assigned_work={},
                           nodes=["gw0", "gw1"], ids=ids)  # fmt: skip


def _ask(tmp_path: Path, monkeypatch, **over) -> None:
    monkeypatch.setattr(wf, "_dir", str(tmp_path))
    monkeypatch.setattr(wf, "_state", {})
    monkeypatch.setitem(wf._schedule._read, wf._schedule._KEY, {})
    rf._put(tmp_path / "ask.json", {**ASK, "overhead_s": 0.0, "least_s": 1.0, **over})


def test_a_unit_leaves_the_queue_only_after_its_reply_is_on_disk(tmp_path: Path, monkeypatch):
    _ask(tmp_path, monkeypatch)
    sched, put = _sched(), wf.put

    def dies(*_a):
        raise OSError("disk")

    monkeypatch.setattr(wf, "put", dies)
    with pytest.raises(OSError):
        wf.offer(sched, "x")
    assert list(sched.workqueue) == sched.ids  # died before: in the Mac's alone
    wf._state.clear()

    def dies_after(path, doc):
        put(path, doc)
        raise OSError("killed")

    monkeypatch.setattr(wf, "put", dies_after)
    with pytest.raises(OSError):
        wf.offer(sched, "x")
    told = rf._read(tmp_path / "reply-1.json") or {}
    assert told["nodes"] and set(told["nodes"]) <= set(sched.workqueue)  # died between: in both


def test_a_taken_unit_is_in_the_reply_or_in_the_queue(tmp_path: Path, monkeypatch):
    _ask(tmp_path, monkeypatch)
    sched = _sched()
    wf.offer(sched, "x")
    told = rf._read(tmp_path / "reply-1.json") or {}
    assert told["want_s"] == pytest.approx(15.0) and len(told["nodes"]) == 15
    assert sorted([*told["nodes"], *sched.workqueue]) == sorted(sched.ids)
    wf._state.clear()
    wf.offer(sched, "x")  # answered once
    assert len(sched.workqueue) == 15


def test_the_pool_s_seconds_are_not_taken_from_the_queue(tmp_path: Path, monkeypatch):
    _ask(tmp_path, monkeypatch, pool_s=500.0)
    sched = _sched()
    wf.offer(sched, "x")
    assert (rf._read(tmp_path / "reply-1.json") or {})["nodes"] == [] and len(sched.workqueue) == 30


def test_a_real_xdist_run_hands_its_units_over_and_ends_green(tmp_path: Path):
    """The scheduler's queue shortened under a running session: every unit left runs, the session
    ends, and what it handed over it did not run."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "feed").mkdir()
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "conftest.py").write_text(
        f"import sys; sys.path.insert(0, {str(REPO_ROOT / 'tests')!r})\n"
        "import _loadgroup, _worker_feed\n_worker_feed.POLL_S = 0.0\n"
        "def pytest_configure(config):\n"
        "    config.pluginmanager.register(_loadgroup, 'goh-loadgroup')\n"
        "    config.pluginmanager.register(_worker_feed, 'feed')\n"
    )
    (tmp_path / "tests" / "test_a.py").write_text(
        "import pytest\n@pytest.mark.parametrize('i', range(40))\ndef test_x(i): pass\n"
    )
    rf._put(tmp_path / "feed" / "ask.json", {**ASK, "overhead_s": 0.0, "least_s": 0.0, "k": 0.1})
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GOH_", "PYTEST_"))}
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider",
         "--disable-plugin-autoload", "-p", "xdist.plugin", "-n", "2", "--dist", "loadgroup"],
        cwd=tmp_path, env={**env, wf.ENV: str(tmp_path / "feed")}, capture_output=True, text=True,
        check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    told = rf._read(tmp_path / "feed" / "reply-1.json") or {}
    ran_here = {w for ln in r.stdout.splitlines() if "PASSED" in ln for w in ln.split()
                if w.startswith("tests/")}  # fmt: skip
    every = {f"tests/test_a.py::test_x[{i}]" for i in range(40)}
    assert r.returncode == 0, r.stdout
    assert told["nodes"] and not ran_here & set(told["nodes"])
    assert ran_here | set(told["nodes"]) == every
    assert (rf._read(tmp_path / "feed" / "plan.json") or {}) == {"skip": []}
