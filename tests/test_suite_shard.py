"""gate.yml's suite shards cover every test exactly once (GOH.CIWALL; tests/_shard.py).

Each hosted shard is its own machine that collects the suite and keeps its K-th part. The parts are
proved here as CI makes them: one process per shard, gate.yml's own shard list, on a fixture with
parametrized tests and xdist groups spanning files -- no test dropped, none run twice, no
group split -- and once more under xdist's loadgroup scheduler, which renames grouped tests as
it collects.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import _shard
import ci_shape
import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

TREE_READS = (".github/workflows/gate.yml",)
GATE_YML = REPO_ROOT / ci_shape.WORKFLOW

CONFTEST = f"""\
import sys
sys.path.insert(0, {str(REPO_ROOT / "tests")!r})
import _schedule

_schedule.take()  # as tests/suite_home.py does first: the shard is the controller's to read


def pytest_configure(config):
    config.addinivalue_line("markers", "xdist_group(name): one worker")
    config.pluginmanager.register(_schedule, "goh-longest-first")
"""
FILES = {
    "test_a.py": "import pytest\n"
    + "".join(f"def test_a{i}():\n    pass\n" for i in range(7))
    + "@pytest.mark.parametrize('x', range(5))\ndef test_p(x):\n    pass\n",
    "test_b.py": "import pytest\n"
    + "".join(
        f"@pytest.mark.xdist_group('g{i % 2}')\ndef test_b{i}():\n    pass\n" for i in range(4)
    )
    + "class TestC:\n"
    + "".join(f"    def test_c{i}(self):\n        pass\n" for i in range(3)),
    "test_c.py": "import pytest\n"
    + "".join(f"@pytest.mark.xdist_group('g0')\ndef test_g{i}():\n    pass\n" for i in range(3))
    + "".join(f"def test_d{i}():\n    pass\n" for i in range(4)),
}
GROUPED = {
    "g0": {
        "test_b.py::test_b0",
        "test_b.py::test_b2",
        *(f"test_c.py::test_g{i}" for i in range(3)),
    },
    "g1": {"test_b.py::test_b1", "test_b.py::test_b3"},
}


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "suite"
    root.mkdir()
    (root / "conftest.py").write_text(CONFTEST)
    for name, text in FILES.items():
        (root / name).write_text(text)
    return root


def _ran(root: Path, *args: str, shard: str = "") -> set[str]:
    """The node ids one pytest process RAN (passed), `@group` suffixes dropped."""
    env = hermetic_env(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTEST_PLUGINS", None)  # the suite's own (tools/pytest.sh); a run names its plugins
    if shard:
        env[_shard.ENV] = shard
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA", "-q", *args],
        cwd=root, env=env, capture_output=True,
        text=True, timeout=patience(120), stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    assert r.returncode in (0, 5), r.stdout + r.stderr  # 5: a shard with nothing to run
    return {m.group(1).split("@")[0] for m in re.finditer(r"^PASSED (\S+)", r.stdout, re.MULTILINE)}


def _shards() -> list[str]:
    parts = ci_shape.partition(GATE_YML)
    n = len(parts)
    assert n >= 2, f"gate.yml shards the suite {n} way: nothing is sharded"
    assert parts == [f"{k}/{n}" for k in range(1, n + 1)], parts
    # and the gate's own step makes the one part the shards do not: the workspace's targets
    assert ci_shape.gate_part(GATE_YML) == "workspace"
    return parts


def test_gate_ymls_shards_cover_every_test_exactly_once(tmp_path):
    root = _tree(tmp_path)
    whole = _ran(root)
    assert len(whole) == 7 + 5 + 4 + 3 + 3 + 4, sorted(whole)  # the fixture's calibration
    ran = [_ran(root, shard=part) for part in _shards()]
    union = set().union(*ran)
    assert union == whole, f"dropped: {sorted(whole - union)}"
    assert sum(map(len, ran)) == len(whole), "a test ran in two shards"
    for name, members in GROUPED.items():
        holding = [k for k, got in enumerate(ran) if got & members]
        assert len(holding) == 1 and members <= ran[holding[0]], (name, holding)


def test_a_shard_under_xdist_loadgroup_runs_the_same_part(tmp_path):
    pytest.importorskip("xdist")
    root = _tree(tmp_path)
    for part in ("1/2", "2/2"):
        plain = _ran(root, shard=part)
        assert plain, part
        under = _ran(root, "-p", "xdist", "-n", "2", "--dist", "loadgroup", shard=part)
        assert under == plain, part


def test_a_spec_is_k_of_n():
    assert _shard.parse("2/4") == (2, 4)
    for bad in ("0/4", "5/4", "4", "a/b", "1/0", ""):
        with pytest.raises(pytest.UsageError, match="1 <= K <= N"):
            _shard.parse(bad)


def test_units_are_dealt_heaviest_first_to_the_lightest_shard():
    got = _shard.assign({"@big": 5, "a": 1, "b": 1, "c": 1, "d": 1, "e": 1}, 2)
    assert got["@big"] == 1 and sorted(got.values()).count(2) == 5
    assert _shard.assign({"x": 1}, 3) == {"x": 1}  # a shard may hold nothing
    assert _shard.unit("t.py::f[a@b]", None) == "t.py::f[a@b]"  # an `@` inside a parameter id
    assert _shard.unit("t.py::f@g", None) == "t.py::f" and _shard.unit("t.py::f", "g") == "@g"
