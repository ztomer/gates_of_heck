"""The longest-first order (tests/_schedule.py): longest unit first, a group moves as one, a test
never measured is UNKNOWN_S, and no table means collection order is left alone. Only the run the
runner names the table for writes it, merging (GOH.DURATIONSSTALE)."""

import json
from pathlib import Path
from types import SimpleNamespace

import _schedule


def _items(*ids):
    return [SimpleNamespace(nodeid=i) for i in ids]


CONFIG = SimpleNamespace(rootpath=Path("/checkout"))


def _fresh(monkeypatch) -> None:
    """This suite's own run already holds its read; the cases below are a process that has none."""
    monkeypatch.setattr(_schedule, "_read", {})


def _table(tmp_path, monkeypatch, durations):
    _fresh(monkeypatch)
    table = tmp_path / "test-durations.json"
    table.write_text(json.dumps(durations))
    monkeypatch.setattr(_schedule, "_table", [table])


def test_the_longest_runs_first_and_a_group_moves_as_one(tmp_path, monkeypatch) -> None:
    _table(tmp_path, monkeypatch, {"t.py::a": 1, "t.py::b": 9, "t.py::g1@G": 4, "t.py::g2@G": 4})
    items = _items("t.py::a", "t.py::g1@G", "t.py::b", "t.py::g2@G", "t.py::new")
    _schedule.pytest_collection_modifyitems(CONFIG, items)
    assert [i.nodeid for i in items] == [
        "t.py::b",
        "t.py::g1@G",
        "t.py::g2@G",
        "t.py::a",
        "t.py::new",
    ]


def test_no_table_leaves_collection_order_alone(tmp_path, monkeypatch) -> None:
    _fresh(monkeypatch)
    monkeypatch.setattr(_schedule, "_table", [tmp_path / "none.json"])
    items = _items("t.py::z", "t.py::a")
    _schedule.pytest_collection_modifyitems(CONFIG, items)
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
        "_schedule.take()\n"
        "def pytest_configure(config):\n"
        "    wid = getattr(config, 'workerinput', {}).get('workerid')\n"
        "    if wid:\n"
        "        flip = wid == 'gw1'\n"
        "        table = {f'test_x.py::test_{n}': (i if flip else -i) + 50\n"
        "                 for i, n in enumerate('abcdefgh')}\n"
        f"        with open({str(tmp_path / 'goh' / 'test-durations.json')!r}, 'w') as f:\n"
        "            json.dump(table, f)\n"
        "    config.pluginmanager.register(_schedule, 'goh-longest-first')\n"
        "import pytest\n"
        "@pytest.hookimpl(trylast=True)\n"
        "def pytest_collection_modifyitems(config, items):\n"
        "    wid = getattr(config, 'workerinput', {}).get('workerid', 'ctl')\n"
        f"    with open(os.path.join({str(tmp_path)!r}, wid + '.order'), 'w') as f:\n"
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
        env=hermetic_env(GOH_TEST_DURATIONS=str(tmp_path / "goh" / "test-durations.json")),
        timeout=120,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "8 passed" in r.stdout, r.stdout
    want = "test_c test_f test_a test_b test_d test_e test_g test_h"  # the controller's table
    assert [(tmp_path / f"gw{n}.order").read_text() for n in (0, 1)] == [want, want]


INNER = "def test_inner():\n    pass\n"
OUTER = """import os, subprocess, sys


def test_old():
    pass


def test_b():
    pass


def test_a_fixture_run_inside():
    assert os.environ.get("GOH_TEST_DURATIONS") is None, "a worker inherited the run's table"
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", INNER],
                       capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
"""


def test_only_the_named_run_writes_the_table_and_it_merges(tmp_path) -> None:
    """GOH.DURATIONSSTALE: the suite runs in a throwaway home, so a table found by XDG_CACHE_HOME
    was written into a home deleted at exit and every run read an empty one (the last real write
    was 2026-10-26). The runner names the table; tests/suite_home.py pops it before anything starts
    a child, through `take()`, so the run it was named for -- and no worker or fixture run under it
    -- merges its measurement in. The fixture's conftest is the suite's own wiring: the real
    suite_home, then the plugin."""
    import subprocess
    import sys

    from conftest import REPO_ROOT, hermetic_env
    from timing_bounds import patience

    wiring = (f"import sys\nsys.path.insert(0, {str(REPO_ROOT / 'tests')!r})\n"
              "import suite_home, _schedule\n"
              "def pytest_configure(config):\n"
              "    config.pluginmanager.register(_schedule, 'goh-longest-first')\n")  # fmt: skip
    outer, inner = tmp_path / "outer", tmp_path / "inner"
    for d in (outer, inner):
        d.mkdir()
        (d / "conftest.py").write_text(wiring)
    (inner / "test_y.py").write_text(INNER)
    (outer / "test_x.py").write_text(OUTER.replace("INNER]", f"{str(inner)!r}]"))
    table = tmp_path / "state" / "test-durations.json"
    table.parent.mkdir()
    table.write_text(json.dumps({"test_x.py::test_old": 7.0, "gone.py::test_z": 1.0}))
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "2",
         "-k", "not test_old", str(outer)],
        capture_output=True, text=True, cwd=outer, check=False, stdin=subprocess.DEVNULL,
        env=hermetic_env(GOH_TEST_DURATIONS=str(table)), timeout=patience(120),
    )  # fmt: skip
    assert r.returncode == 0 and "2 passed" in r.stdout, r.stdout + r.stderr
    got = json.loads(table.read_text())
    assert sorted(got) == ["test_x.py::test_a_fixture_run_inside", "test_x.py::test_b",
                           "test_x.py::test_old"], got  # fmt: skip
    assert got["test_x.py::test_old"] == 7.0  # merged, not replaced; gone.py's entry pruned


def test_take_pops_the_table_and_a_run_named_none_owns_none(monkeypatch) -> None:
    monkeypatch.setattr(_schedule, "_table", [])
    environ = {_schedule.ENV: "/x/t.json", "KEEP": "1"}
    assert _schedule.take(environ) == Path("/x/t.json") and environ == {"KEEP": "1"}
    assert _schedule.take(environ) is None and _schedule._load() == {}


def test_a_node_id_naming_the_checkout_is_refused(monkeypatch) -> None:
    """Such a node exists only in the checkout that collected it: carried in a tree's record, no
    full run elsewhere ever judged it, so the tree was never full (GOH.SHIPTREEFULL)."""
    import pytest

    _fresh(monkeypatch)
    items = _items("t.py::a[None]", "t.py::a[/checkout/lib]")
    with pytest.raises(
        pytest.UsageError, match=r"names the checkout /checkout .*a\[/checkout/lib\]"
    ):
        _schedule.pytest_collection_modifyitems(CONFIG, items)
    _schedule.pytest_collection_modifyitems(CONFIG, _items("t.py::a[/elsewhere]"))
