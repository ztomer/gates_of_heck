"""`--dist loadgroup` keeps an xdist_group on one worker (GOH.XDISTGROUP; tests/_loadgroup.py).

Every xdist_group marker in this suite was inert under pytest 9.2 + xdist 3.8.0: xdist's rename of
a grouped test never landed, so the scheduler saw no groups. Here a planted suite, run as the
suite runs (`-n 3 --dist loadgroup`, the suite's own plugins through tests/_schedule.py), must put
its two grouped tests on ONE worker -- and, the instrument's calibration, its two ungrouped tests
on two, so "one worker" can never be a run that used one worker for everything.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

# tests/_loadgroup.py is imported by the tests that read it, never here: the planted run must be
# able to go red on a tree without the plugin (the red run of GOH.XDISTGROUP did exactly that).

pytestmark = pytest.mark.xdist_group("loadgroup-self")  # test_this_run_groups_me reads its own

CONFTEST = f"""\
import sys
sys.path.insert(0, {str(REPO_ROOT / "tests")!r})
import _schedule

_schedule.take()


def pytest_configure(config):
    config.addinivalue_line("markers", "xdist_group(name): one worker")
    config.pluginmanager.register(_schedule, "goh-longest-first")
"""
PLANTED = """\
import os
from pathlib import Path

import pytest

OUT = Path(os.environ["PLANTED_OUT"])


def _here(name):
    (OUT / name).write_text(os.environ["PYTEST_XDIST_WORKER"])


@pytest.mark.xdist_group("pair")
def test_grouped_one():
    _here("grouped_one")


@pytest.mark.xdist_group("pair")
def test_grouped_two():
    _here("grouped_two")


def test_free_one():
    _here("free_one")


def test_free_two():
    _here("free_two")
"""


def _workers(tmp_path: Path) -> dict[str, str]:
    """{test: the worker that ran it} for one `-n 3 --dist loadgroup` run of the planted suite."""
    root, out = tmp_path / "suite", tmp_path / "out"
    root.mkdir()
    out.mkdir()
    (root / "conftest.py").write_text(CONFTEST)
    (root / "test_pair.py").write_text(PLANTED)
    env = hermetic_env(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PLANTED_OUT=str(out))
    env.pop("PYTEST_PLUGINS", None)
    argv = [sys.executable, "-m", "pytest", "-p", "xdist", "-p", "no:cacheprovider", "-q"]
    r = subprocess.run(
        [*argv, "-n", "3", "--dist", "loadgroup"],
        cwd=root, env=env, capture_output=True,
        text=True, timeout=patience(120), stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    return {p.name: p.read_text() for p in out.iterdir()}


def test_an_xdist_group_runs_on_one_worker_under_loadgroup(tmp_path):
    pytest.importorskip("xdist")
    ran = _workers(tmp_path)
    assert len(ran) == 4, ran
    assert ran["free_one"] != ran["free_two"], f"the run used one worker for all: {ran}"
    assert ran["grouped_one"] == ran["grouped_two"], f"the group was split: {ran}"


def test_this_run_groups_me(request):
    """The suite's own run: under xdist loadgroup, this worker's group map names this test."""
    import _loadgroup

    worker = getattr(request.config, "workerinput", None)
    if worker is None or not getattr(request.config.option, "loadgroup", False):  # xdist's own
        pytest.skip("not an xdist loadgroup run")
    groups = _loadgroup.read_map(worker["goh_loadgroup_map"], worker["workerid"])
    assert groups.get(request.node.nodeid) == "loadgroup-self", groups.get(request.node.nodeid)


def test_the_plugin_is_deleted_once_xdist_sends_groups_itself():
    """A ratchet: upstream ffa67feff7 sends group names itself; on the release that carries it,
    tests/_loadgroup.py stands down and must go, with this test (keep the planted one)."""
    pytest.importorskip("xdist")
    import _loadgroup

    assert not _loadgroup.upstream_sends_groups(), (
        "xdist now passes group_names itself: delete tests/_loadgroup.py's scheduler and map"
    )


def test_a_group_is_every_mark_sorted_and_joined_as_xdist_names_it():
    import _loadgroup

    class Mark:
        def __init__(self, *args, **kwargs) -> None:
            self.args, self.kwargs = args, kwargs

    class Item:
        def __init__(self, *marks) -> None:
            self.marks = marks

        def iter_markers(self, _name):
            return iter(self.marks)

    assert _loadgroup.group(Item()) is None
    assert _loadgroup.group(Item(Mark("b"), Mark(name="a"))) == "a_b"
    assert _loadgroup.group(Item(Mark())) == "default"


def test_every_workers_map_is_one_file_of_the_run(tmp_path):
    """GOH.FSEVQUIET: a directory, and a temp and a rename per worker, were 24 events a run."""
    from concurrent.futures import ThreadPoolExecutor

    import _loadgroup

    one = tmp_path / "map"
    maps = {f"gw{i}": {f"t.py::t{i}[{n}]": "g" for n in range(500)} for i in range(12)}
    with ThreadPoolExecutor(12) as pool:
        list(pool.map(lambda w: _loadgroup.add_map(one, w, maps[w]), maps))
    assert {w: _loadgroup.read_map(one, w) for w in maps} == maps
    assert [p.name for p in tmp_path.iterdir()] == ["map"]
    with pytest.raises(KeyError):
        _loadgroup.read_map(one, "gw99")
