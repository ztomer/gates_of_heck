"""Each test's work is counted, recorded and held under a ceiling (GOH.TESTWORK, lib/test_work.py).

A planted session (tests/_test_work.py loaded alone, judging) proves each guard by its fault: a test
over each of the five dimensions is red, naming the test, the dimension and the entry that would
let it stand; the same tests named are green; a file's entry excuses no NEW test. The rest are the
pure parts, the rows in memory, and the list in this tree, which only shrinks.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import test_work
from _kv_fixture import Held
from _test_work import classify
from conftest import REPO_ROOT, hermetic_env
from test_test_work_new import new_tests
from timing_bounds import patience

TREE_READS = ("tests/test_work_ceiling.json", "tests/")  # the list names files there
CONF = test_work.config()
NAMED = 504  # the entries of tests/test_work_ceiling.json `named`, only ever lowered
NEW = {"cpu_s": 0.1, "procs": 5, "git_repos": 1, "cargo_builds": 0, "tree_walks": 0}

PLANTED = """import hashlib, os, subprocess

def test_procs():
    for _ in range(6):
        subprocess.run(["true"], check=True)

def test_git(tmp_path):
    for name in ("a", "b"):
        subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", str(tmp_path / name)],
                       check=True)

def test_cargo():
    subprocess.run(["cargo", "+stable", "build", "-q"], executable="/usr/bin/true", check=True)

def test_walk(tmp_path):
    for mark in ("gates/structural.sh", "tests/conftest.py", "lib/goh_db.py"):  # a goh tree's
        (tmp_path / mark).parent.mkdir(exist_ok=True)
        (tmp_path / mark).touch()
    (tmp_path / "a" / "b").mkdir(parents=True)
    assert len(list(os.walk(tmp_path))) == 6
    small = tmp_path / "a"  # a fixture repository's walk is no walk of the tree
    (small / ".git").mkdir()
    assert len(list(os.walk(small))) == 3

def test_cpu():  # ~0.17 CPU s on an M-series core, one call: no loop to spin
    hashlib.pbkdf2_hmac("sha256", b"x", b"y", 2_000_000)

def test_shared(three_procs):
    pass
"""
CONFTEST = """import subprocess, pytest

@pytest.fixture(scope="session")
def three_procs():
    for _ in range(3):
        subprocess.run(["true"], check=True)
"""
FAULTY = ("test_procs", "procs"), ("test_git", "git_repos"), ("test_cargo", "cargo_builds"), (
    "test_walk", "tree_walks"), ("test_cpu", "cpu_s")  # fmt: skip


def _session(tmp_path: Path, conf: dict, mode: str = "judge") -> tuple[str, dict]:
    (tmp_path / "test_planted.py").write_text(PLANTED)
    (tmp_path / "conftest.py").write_text(CONFTEST)
    out = tmp_path / "rows.json"
    env = hermetic_env(
        GATES_OF_HECK_TEST_WORK=mode, GATES_OF_HECK_TEST_WORK_CEILING=json.dumps(conf),
        GATES_OF_HECK_TEST_WORK_OUT=str(out),
        PYTHONPATH=f"{REPO_ROOT / 'tests'}:{REPO_ROOT / 'lib'}",
    )  # fmt: skip
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist", "-p",
         "_test_work", "-W", "ignore", "--rootdir", str(tmp_path), "test_planted.py"],
        cwd=tmp_path, env=env, check=False, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=patience(120),
    )  # fmt: skip
    assert "6 passed" in r.stdout, r.stdout[-2000:]
    return r.stdout, {**json.loads(out.read_text()), "rc": r.returncode}


def test_a_test_over_each_default_is_red_naming_the_fault_and_the_exception_form(tmp_path) -> None:
    out, rep = _session(tmp_path, {"new": NEW, "named": {}})
    assert rep["rc"] == 1, out[-2000:]
    rows = rep["rows"]
    for test, dim in FAULTY:
        node = f"test_planted.py::{test}"
        line = next((ln for ln in out.splitlines() if f"{node} {dim} " in ln), "")
        assert line.startswith("✗ test work (GOH.TESTWORK):"), (test, out[-2000:])
        assert f'named` as "{node}": {{"{dim}": ' in line and '"why": "<its reason>"' in line, line
        assert rows[node][dim] > NEW[dim], (node, rows[node])
    assert rows["test_planted.py::test_git"]["git_repos"] == 2
    assert rows["test_planted.py::test_walk"]["tree_walks"] == 1, rows  # the fixture's is none
    assert rows["test_planted.py::test_shared"]["procs"] == 0, rows  # the session fixture's ...
    assert rows[test_work.SHARED]["procs"] == 3, rows  # ... is the run's shared setup
    lines = [ln for ln in out.splitlines() if ln.startswith("✗")]
    assert len(lines) == len(FAULTY), lines  # the quiet test and the shared setup: no fault


MOST = {"cpu_s": 60.0, "procs": 9, "git_repos": 2, "cargo_builds": 1, "tree_walks": 1}
NAMED_FILE = {"test_planted.py": {**MOST, "why": "planted"}}


def test_the_tests_of_a_named_file_stand(tmp_path) -> None:
    out, rep = _session(tmp_path, {"new": NEW, "named": NAMED_FILE})
    assert rep["rc"] == 0 and "✗" not in out, out[-2000:]


def test_a_files_entry_excuses_no_new_test(tmp_path) -> None:
    out, rep = _session(tmp_path, {"new": NEW, "named": NAMED_FILE}, mode="new")
    assert rep["rc"] == 1 and out.count("✗ test work") == len(FAULTY), out[-2000:]


# argv words classify() reads: no repository is made (tests/git_init_lint.py)
MAKES = ("init", "clone")


def test_a_spawns_argv_says_git_repositories_and_cargo_builds() -> None:
    assert classify(["git", "-C", "/x", "-c", "a=b", MAKES[0], "-q"]) == (1, 0)
    assert classify(["/usr/bin/git", MAKES[1], "src", "dst"]) == (1, 0)
    assert classify(["git", "status"]) == (0, 0)
    assert classify(["git", "-C", MAKES[0], "log"]) == (0, 0)  # -C takes a value
    line = f"cd x && git {MAKES[0]} -q && cargo +nightly build --release"
    assert classify(["sh", "-c", line]) == (1, 1)
    assert classify(["bash", "-ec", "cargo metadata; cargo --version"]) == (0, 0)
    assert classify(["cargo", "--locked", "test"]) == (0, 1) and classify("cargo") == (0, 0)


def _row(**counts) -> dict:
    return {**dict.fromkeys(test_work.DIMS, 0), **counts}


def test_a_runs_tests_are_judged_on_the_dimensions_it_names_and_a_new_one_on_all() -> None:
    conf = {"new": NEW, "named": {}, "judged_each_run": ["procs"]}
    assert test_work.faults({"f.py::a": _row(cpu_s=9.0)}, conf) == []
    assert len(test_work.faults({"f.py::a": _row(cpu_s=9.0)}, conf, new_only=True)) == 1
    assert len(test_work.faults({"f.py::a": _row(procs=6)}, conf)) == 1


def test_a_cold_test_or_the_shared_setup_is_not_judged_and_a_node_entry_wins() -> None:
    conf = {"new": NEW, "named": {"f.py": {"procs": 50}, "f.py::t": {"procs": 7}}}
    assert test_work.faults({"f.py::t[1]": _row(procs=8, cold=True)}, conf) == []
    assert test_work.faults({test_work.SHARED: _row(procs=900)}, conf) == []
    assert test_work.faults({"f.py::u": _row(procs=50)}, conf) == []
    assert len(test_work.faults({"f.py::t[1]": _row(procs=8)}, conf)) == 1
    assert len(test_work.faults({"f.py::u": _row(procs=6)}, conf, new_only=True)) == 1
    assert test_work.faults({"f.py::t[2]": _row(procs=7)}, conf, new_only=True) == []


def test_an_entry_read_within_new_by_stale_runs_whole_runs_is_stale() -> None:
    conf = {"new": NEW, "stale_runs": 3, "named": {"f.py": {"procs": 50, "cpu_s": 9.0, "why": "x"}}}
    quiet = {"f.py::a": _row(procs=5, cpu_s=0.05)}
    lines = test_work.stale(quiet, conf, {"f.py": {"procs": [3, 5], "cpu_s": [0.0, 0.05]}})
    assert len(lines) == 2 and "remove cpu_s" in lines[0] and "remove procs" in lines[1], lines
    assert test_work.stale(quiet, conf) == []  # one run is no verdict
    assert test_work.stale(quiet, conf, {"f.py": {"procs": [5], "cpu_s": [0.0]}}) == []
    assert test_work.stale(quiet, conf, {"f.py": {"procs": [6, 1], "cpu_s": [0.051, 0]}}) == []
    loud = {"f.py::a": _row(procs=6, cpu_s=0.051)}
    assert test_work.stale(loud, conf, {"f.py": {"procs": [0, 0], "cpu_s": [0, 0]}}) == []
    assert test_work.stale({"f.py::a": _row(cold=True)}, conf) == []  # a cold run says nothing
    assert test_work.stale({"g.py::a": _row()}, conf) == []  # no test of it ran


def test_the_rows_are_recorded_and_read_back_idle_and_loaded(monkeypatch) -> None:
    Held().lay(monkeypatch)
    rows = {"f.py::a": _row(procs=9, cpu_s=2.5), "f.py::b": _row(git_repos=3),
            "g.py::a": _row(procs=99, cold=True), test_work.SHARED: _row(procs=40)}  # fmt: skip
    assert test_work.record("r1", rows, True, 1.0, 2.0, cores=16)
    assert test_work.record("r2", {"f.py::a": _row(procs=12)}, True, 17.0, 40.0, cores=16)
    assert test_work.record("r3", {"f.py::a": _row(procs=80)}, False, 1.0, 1.0)  # not whole
    read = test_work.readings()
    assert set(read) == {"f.py"}, read  # the cold and the shared rows are no reading
    assert read["f.py"]["idle"]["procs"] == 9 and read["f.py"]["loaded"]["procs"] == 12
    assert read["f.py"]["runs"] == {"idle": 1, "loaded": 1}
    entry = test_work.seed(read, {**CONF, "new": NEW})["f.py"]
    assert entry["procs"] == 18 and entry["git_repos"] == 5 and entry["cpu_s"] == 5.0, entry
    assert entry["why"].startswith("unreviewed")
    assert test_work.seed(read, {**CONF, "new": NEW}, min_runs=3) == {}
    named = {"new": NEW, "named": {"f.py": {"procs": 20}}, "stale_runs": 3}
    got = test_work.history(named)  # the newest two whole runs, newest first
    assert got["f.py"]["procs"] == [12, 9] and got["f.py"]["cpu_s"] == [0.0, 2.5], got


def test_a_commits_new_tests_are_the_functions_head_did_not_have() -> None:
    head = "def test_a(): pass\nclass TestB:\n    def test_c(self): pass\n"
    staged = head + "def test_d(): pass\nclass TestB2:\n    def test_e(self): pass\ndef f(): pass\n"
    assert new_tests(head, staged) == ["TestB2::test_e", "test_d"]
    assert new_tests(None, head) == ["TestB::test_c", "test_a"]


def test_the_list_names_only_files_that_stand_and_only_shrinks() -> None:
    named, new = CONF["named"], CONF["new"]
    assert set(new) == set(test_work.DIMS) and set(CONF["judged_each_run"]) <= set(new)
    assert len(named) <= NAMED, f"{len(named)} entries: the list only shrinks (NAMED)"
    gone = sorted(k for k in named if not (REPO_ROOT / k.split("::")[0]).is_file())
    assert gone == [], f"entries for files that are gone, remove them: {gone}"
    for key, entry in named.items():
        assert entry.get("why"), (key, "an exception names its reason")
        dims = [d for d in test_work.DIMS if d in entry]
        assert dims and all(entry[d] > new[d] for d in dims), (key, "an entry buys nothing")
