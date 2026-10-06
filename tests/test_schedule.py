"""The longest-first order (tests/_schedule.py): longest unit first, a group moves as one, a test
never measured is UNKNOWN_S, and no table means collection order is left alone."""

import json
from types import SimpleNamespace

import _schedule


def _items(*ids):
    return [SimpleNamespace(nodeid=i) for i in ids]


def _table(tmp_path, monkeypatch, durations):
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
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    items = _items("t.py::z", "t.py::a")
    _schedule.pytest_collection_modifyitems(items)
    assert [i.nodeid for i in items] == ["t.py::z", "t.py::a"]
