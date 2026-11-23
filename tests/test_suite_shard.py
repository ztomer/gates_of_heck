"""gate.yml's suite shards cover every test exactly once (GOH.CIWALL; tests/_shard.py).

Each hosted shard is its own machine that collects the suite and keeps its K-th part. The parts are
proved here as CI makes them: one process per shard, gate.yml's own shard list, on a fixture with
parametrized tests and xdist groups spanning files -- no test dropped, none run twice, no
group split -- and once more under xdist's loadgroup scheduler, which groups them by the map
tests/_loadgroup.py hands it (the ids stay pytest's).
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

TREE_READS = (".github/workflows/gate.yml", "tests/shard_weights.json")
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


def test_no_job_asks_for_a_runner_after_the_first_wave():
    """GOH.CIWALL2: the merge was a third job, `needs: [gate, suite]`, and v0.139.14's run
    44606206460 waited 7.9 min for a sixth macOS runner (the account runs five at once) before
    6.3 min of setup for a merge of seconds. The gate job finishes first, so it waits on this
    run's suite jobs and merges in place: no job needs another, and the merge reads every shard
    after the wait."""
    doc = ci_shape.parse(GATE_YML.read_text())
    jobs = doc["jobs"]
    assert set(jobs) == {"gate", "suite"}, sorted(jobs)
    assert not [j for j, body in jobs.items() if (body or {}).get("needs")], "a job waits on a job"
    gate = jobs["gate"]
    assert (gate.get("permissions") or {}).get("actions") == "read", gate.get("permissions")
    steps = gate["steps"]

    def at(pred) -> int:
        [n] = [n for n, st in enumerate(steps) if pred(st)]
        return n

    ran = at(lambda st: str(st.get("run", "")).strip() == ci_shape.GATE_RUN)
    wait = at(lambda st: "actions/runs/$GITHUB_RUN_ID/attempts" in str(st.get("run", "")))
    fetch = at(lambda st: str(st.get("uses", "")).startswith("actions/download-artifact@"))
    merge = at(lambda st: "GOH_COV_PARTS_IN" in (st.get("env") or {}))
    assert ran < wait < fetch < merge, (ran, wait, fetch, merge)
    waits = steps[wait]["run"]
    assert 'startswith("suite")' in waits and "completed/success" in waits, waits
    assert steps[fetch]["with"]["pattern"] == "cov-part-shard-*", steps[fetch]


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


class _Item:
    """A collected test as select() reads it: its node id and its xdist group mark (none)."""

    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid

    def iter_markers(self, _name):
        return iter(())


class _Config:
    def __init__(self) -> None:
        self.hook = self
        self.dropped: list = []

    def pytest_deselected(self, items) -> None:
        self.dropped += items


def _loads(table: dict[str, float], ids: list[str], n: int, truth) -> list[float]:
    """Each shard's seconds by `truth` when select() deals `ids` by `table`."""
    out = []
    for k in range(1, n + 1):
        items = [_Item(i) for i in ids]
        _shard.select(_Config(), items, f"{k}/{n}", table)
        out.append(sum(truth[i.nodeid] for i in items))
    return out


def test_the_shards_are_dealt_by_measured_seconds_not_by_count():
    """GOH.CIWALL2: v0.139.13's four shards held 1641-1646 tests each and ran 527 to 1104 s: units
    weighed by COUNT. Each test now weighs its measured seconds (tests/shard_weights.json), so three
    10 s tests and four 1 s ones split 20/14, where a split by count is 22/12."""
    table = {"fast.py": 4.0, "slow.py": 30.0}
    ids = [f"fast.py::t{i}" for i in range(4)] + [f"slow.py::t{i}" for i in range(3)]
    truth = {i: 1.0 if i.startswith("fast") else 10.0 for i in ids}
    assert sorted(_loads(table, ids, 2, truth)) == [14.0, 20.0]


def test_a_listed_slow_test_weighs_its_own_seconds_and_its_file_shares_the_rest():
    table = {"m.py": 26.0, "m.py::slow[a]": 20.0, "m.py::gone": 5.0}
    ids = ["m.py::slow[a]", "m.py::fast[a]", "m.py::fast[b]", "m.py::fast[c]@grp", "new.py::x"]
    got = _shard.costs(ids, table)
    # what the listed tests that were COLLECTED leave (6 s) over the three unlisted ones
    assert got == {"m.py::slow[a]": 20.0, "m.py::fast[a]": 2.0, "m.py::fast[b]": 2.0,
                   "m.py::fast[c]@grp": 2.0, "new.py::x": _shard.UNKNOWN_S}  # fmt: skip
    truth = {"m.py::slow[a]": 20.0, "m.py::fast[a]": 2.0, "m.py::fast[b]": 2.0}
    assert sorted(_loads(table, list(truth), 2, truth)) == [4.0, 20.0]


def test_the_shards_agree_whatever_order_each_collected_in():
    """Every shard is its own machine: the part is a function of the checkout, so one collection
    in another order (the box's own longest-first sort, tests/_schedule.py) deals the same part."""
    table = {"a.py": 7.0, "a.py::t1": 3.3, "b.py": 3.3, "c.py": 0.7}
    ids = [f"{f}::t{i}" for f in ("a.py", "b.py", "c.py") for i in range(5)]
    for k in ("1/3", "2/3", "3/3"):
        fwd, rev = [_Item(i) for i in ids], [_Item(i) for i in reversed(ids)]
        _shard.select(_Config(), fwd, k, table)
        _shard.select(_Config(), rev, k, table)
        assert {i.nodeid for i in fwd} == {i.nodeid for i in rev}, k


def test_the_committed_weights_are_measured_seconds():
    """tests/shard_weights.json is the checkout's own (every shard reads the same bytes), written by
    `python3 tests/_shard.py --refresh` from a full suite's duration table."""
    got = _shard.weights()
    files = [k for k in got if "::" not in k]
    assert len(files) > 100 and all(v > 0 for v in got.values()), len(files)
    assert all(k.startswith("tests/test_") and k.split("::")[0].endswith(".py") for k in got)
    assert all(got[k] >= _shard.SLOW_S and k.split("::")[0] in got for k in got if "::" in k)
    table = {"tests/t.py::a": 1.25, "tests/t.py::b[1]": 3.5, "tests/gone.py::c": 9.0}
    assert _shard.measure(table, lambda f: f != "tests/gone.py") == {
        "tests/t.py": 4.75, "tests/t.py::b[1]": 3.5
    }  # fmt: skip


def _schedule_readers() -> list[str]:
    """Every test file that imports the scheduling modules: they hold module state `take()` sets."""
    tests = REPO_ROOT / "tests"
    return sorted(
        f"tests/{p.name}"
        for p in tests.glob("test_*.py")
        if p.name != Path(__file__).name
        and re.search(
            r"^\s*import (_schedule|_shard)\b", p.read_text(encoding="utf-8"), re.MULTILINE
        )
    )


@pytest.mark.parametrize("part", ["1/1", "1/2", "2/2"])
def test_a_test_gives_the_same_verdict_under_every_shard_env(tmp_path, part):
    """GOH.CIRED013913: hosted CI's first sharded run went red in 3 shards on tests/test_schedule.py
    (`SimpleNamespace` has no `get_closest_marker`). The suite's own run had taken GOH_SUITE_SHARD
    into _schedule's module state, so a test that calls the module's collection hook on fakes was
    SHARDED -- a verdict that depended on which machine ran it. Each file that imports the
    scheduler is run here under `1/1` (every test kept, so every test can fail) and under each
    part of a split, and none may fail. One case per part, so xdist spreads them: as one test the
    three nested runs took 64.9 s under load, over the enqueue's 60 s long pole."""
    files = _schedule_readers()
    assert "tests/test_schedule.py" in files and "tests/test_prestep_fast.py" in files, files
    env = hermetic_env()  # the suite's own plugins (xdist) stay: these files nest a run
    env[_shard.ENV] = part
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-n0", *files],
        cwd=REPO_ROOT, env=env, capture_output=True,
        text=True, timeout=patience(300), stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    assert r.returncode in (0, 5), f"{part}: {r.stdout[-3000:]}{r.stderr[-1000:]}"
