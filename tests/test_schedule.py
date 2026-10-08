"""The longest-first order (tests/_schedule.py): longest unit first, a group moves as one, a test
never measured is UNKNOWN_S, and no table means collection order is left alone."""

import json
from types import SimpleNamespace

import _schedule


def _items(*ids):
    return [SimpleNamespace(nodeid=i) for i in ids]


def _fresh(monkeypatch) -> None:
    """This suite's own run already holds its read; the cases below are a process that has none."""
    monkeypatch.setattr(_schedule, "_read", {})


def _table(tmp_path, monkeypatch, durations):
    _fresh(monkeypatch)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    (tmp_path / "goh").mkdir(exist_ok=True)
    (tmp_path / "goh" / "test-durations.json").write_text(json.dumps(durations))


def test_the_longest_runs_first_and_a_group_moves_as_one(tmp_path, monkeypatch) -> None:
    _table(tmp_path, monkeypatch, {"t.py::a": 1, "t.py::b": 9, "t.py::g1@G": 4, "t.py::g2@G": 4})
    items = _items("t.py::a", "t.py::g1@G", "t.py::b", "t.py::g2@G", "t.py::new")
    _schedule.pytest_collection_modifyitems(items)
    assert [i.nodeid for i in items] == [
        "t.py::b",
        "t.py::g1@G",
        "t.py::g2@G",
        "t.py::a",
        "t.py::new",
    ]


def test_no_table_leaves_collection_order_alone(tmp_path, monkeypatch) -> None:
    _fresh(monkeypatch)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    items = _items("t.py::z", "t.py::a")
    _schedule.pytest_collection_modifyitems(items)
    assert [i.nodeid for i in items] == ["t.py::z", "t.py::a"]


def test_every_worker_sorts_by_the_controllers_one_read(tmp_path) -> None:
    """The table is shared by every suite on the box, and another session's suite rewrites it at
    its end. Workers that each read it collect in different orders the moment it changes between
    their starts, and xdist refuses the run ("Different tests were collected between gw2 and
    gw7", a land gate at load 33, 2026-10-08). Here each worker rewrites the table its own way
    before collecting: only a table read once, by the controller, gives them one order."""
    import subprocess
    import sys

    from conftest import REPO_ROOT, hermetic_env

    (tmp_path / "conftest.py").write_text(
        "import json, os, sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'tests')!r})\n"
        "import _schedule\n"
        "def pytest_configure(config):\n"
        "    wid = getattr(config, 'workerinput', {}).get('workerid')\n"
        "    if wid:\n"
        "        flip = wid == 'gw1'\n"
        "        table = {f'test_x.py::test_{n}': (i if flip else -i) + 50\n"
        "                 for i, n in enumerate('abcdefgh')}\n"
        "        path = os.path.join(os.environ['XDG_CACHE_HOME'], 'goh', 'test-durations.json')\n"
        "        with open(path, 'w') as f:\n"
        "            json.dump(table, f)\n"
        "    config.pluginmanager.register(_schedule, 'goh-longest-first')\n"
        "import pytest\n"
        "@pytest.hookimpl(trylast=True)\n"
        "def pytest_collection_modifyitems(config, items):\n"
        "    wid = getattr(config, 'workerinput', {}).get('workerid', 'ctl')\n"
        "    with open(os.path.join(os.environ['XDG_CACHE_HOME'], wid + '.order'), 'w') as f:\n"
        "        f.write(' '.join(i.name for i in items))\n"
    )
    (tmp_path / "test_x.py").write_text("".join(f"def test_{n}():\n    pass\n" for n in "abcdefgh"))
    (tmp_path / "goh").mkdir()
    controllers = {"test_x.py::test_c": 9, "test_x.py::test_f": 5}
    (tmp_path / "goh" / "test-durations.json").write_text(json.dumps(controllers))
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "2", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=hermetic_env(XDG_CACHE_HOME=str(tmp_path)),
        timeout=120,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "8 passed" in r.stdout, r.stdout
    want = "test_c test_f test_a test_b test_d test_e test_g test_h"  # the controller's table
    assert [(tmp_path / f"gw{n}.order").read_text() for n in (0, 1)] == [want, want]
