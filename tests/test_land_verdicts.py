"""A green land runs each test once, and its pre-step runs only the tests that read the tree.

GOH.LANDPREDUP. GOH.TREETESTSFIRST's pre-step ran ~200 whole test files (213-565 s) before the land
lock, then the full gate ran them all again. Here: a tree-reading file contributes its reading
tests only (tools/affected_tests.py `reader_nodes`), the pre-step's suite records what it passed
and the gate's suite skips it on the same tree and tools (tests/_land_verdicts.py), the coverage
run keeping every test that ran the binary, and tools/land.sh hands the one record from the first
to the second.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import _land_verdicts as lv
import pytest
from conftest import REPO_ROOT, commit_all, git, hermetic_env
from test_affected_tests import _declare, _land, _tree
from test_land import _main, _worktree

sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "lib"))
import affected_tests as at
import gate_identity

MIXED = """
import pytest

def test_no_stray_marks():
    pass

def test_parses_a_flag():
    pass

class TestShapes:
    def test_every_shape_is_named(self):
        pass

    def test_one_shape(self):
        pass
"""

CHAINED = """
import pytest
SCRIPTS = sorted(REPO_ROOT.rglob("*.sh"))

def _names():
    return [p.name for p in SCRIPTS]

@pytest.fixture
def names():
    return _names()

def test_each_script_has_a_header(names):
    pass

@pytest.mark.parametrize("p", sorted((REPO_ROOT / "hooks").iterdir()))
def test_each_hook_runs(p):
    pass

def test_a_pure_function():
    pass
"""


def test_a_tree_reader_contributes_only_its_reading_tests(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"tests/test_r.py": MIXED})
    assert sorted(at.affected(root, [])) == [
        "tests/test_r.py::TestShapes::test_every_shape_is_named",
        "tests/test_r.py::test_no_stray_marks",
    ]


def test_a_reading_chain_through_names_fixtures_and_decorators_is_followed(tmp_path) -> None:
    root = _tree(tmp_path, {"tests/test_r.py": CHAINED})
    assert sorted(at.affected(root, [])) == [
        "tests/test_r.py::test_each_hook_runs",
        "tests/test_r.py::test_each_script_has_a_header",
    ]


def test_a_test_using_a_walking_helper_is_a_reading_test(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "tests/_walker.py": "import os\ndef files():\n    return os.walk('.')\n",
            "tests/test_r.py": "from _walker import files\n\ndef test_a():\n    files()\n\n"
            "def test_b():\n    pass\n",
        },
    )
    assert list(at.affected(root, [])) == ["tests/test_r.py::test_a"]


@pytest.mark.parametrize(
    "text",
    [
        "for p in REPO_ROOT.rglob('*.sh'):\n    pass\n\ndef test_x():\n    pass\n",  # binds nothing
        "out = git('ls-files')\n",  # a reader with no test to name
        "def test_every_x(:\n",  # does not parse
    ],
)
def test_a_reader_whose_reading_tests_cannot_be_told_apart_runs_whole(tmp_path, text) -> None:
    root = _tree(tmp_path, {"tests/test_r.py": text})
    assert list(at.affected(root, [])) == ["tests/test_r.py"]


def test_a_reader_the_diff_chooses_runs_whole(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"tests/test_r.py": MIXED + '\nX = "tools/load.py"\n'})
    assert list(at.affected(root, ["tools/load.py"])) == ["tests/test_r.py"]


def test_the_real_pre_set_runs_a_slow_readers_reading_tests_not_the_file() -> None:
    """test_bench_lock.py was 101 s of the 879 s the whole-file pre-set summed (2026-11-16)."""
    chosen = at.affected(REPO_ROOT, [])
    assert "tests/test_bench_lock.py" not in chosen
    assert "tests/test_bench_lock_joins.py" not in chosen
    assert "tests/test_bench_lock_joins.py::test_every_gate_joins_through_goh_init" in chosen
    for always in at.ALWAYS:
        assert always in chosen, always


def test_every_node_the_pre_set_names_is_a_test_pytest_collects() -> None:
    """A node id naming no test is a pytest usage error, and the pre-step red for it."""
    nodes = [n for n in at.affected(REPO_ROOT, []) if "::" in n]
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "-p", "no:xdist", *nodes],
        cwd=REPO_ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
        env=hermetic_env(drop_git=True, GATES_OF_HECK_SUITE_NESTED_OK="1"),
    )  # fmt: skip
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "not found" not in r.stdout + r.stderr, r.stdout[-3000:]


# -- the suite identity ----------------------------------------------------------------------


def test_the_suite_identity_is_the_clean_tree_and_none_when_it_is_changed(repo: Path) -> None:
    first = gate_identity.suite(repo)
    assert first and len(first) == 64
    assert gate_identity.suite(repo) == first
    (repo / "stray.txt").write_text("x\n")  # untracked: the bytes are not the tree's
    assert gate_identity.suite(repo) is None
    commit_all(repo, "stray")
    assert gate_identity.suite(repo) not in (None, first)


def test_a_directory_that_is_no_checkout_has_no_suite_identity(tmp_path: Path) -> None:
    assert gate_identity.suite(tmp_path) is None


# -- record and reuse ------------------------------------------------------------------------


def _record(tmp_path: Path, identity: str | None, passed: dict[str, bool]) -> str:
    p = tmp_path / "rec.json"
    p.write_text(json.dumps({"identity": identity, "passed": passed}))
    return str(p)


def test_a_record_of_this_tree_lets_its_passed_tests_be_skipped(tmp_path: Path) -> None:
    rec = _record(tmp_path, "ID", {"t.py::a": False, "t.py::b": True})
    assert lv.reusable(rec, "ID", coverage=False)[0] == {"t.py::a", "t.py::b"}


def test_under_coverage_a_test_that_ran_the_binary_is_never_skipped(tmp_path: Path) -> None:
    rec = _record(tmp_path, "ID", {"t.py::a": False, "t.py::b": True})
    nodes, why = lv.reusable(rec, "ID", coverage=True)
    assert nodes == {"t.py::a"} and "1 that ran the binary" in why


@pytest.mark.parametrize(
    ("env", "want"),
    [
        ({"GOH_COVERAGE_BIN_DIR": "/cov/bin", "LLVM_PROFILE_FILE": "/cov/p.profraw"}, True),
        ({"GOH_COVERAGE_BIN_DIR": "/plain/bin"}, False),  # GOH.RUSTCOVSKIP's plain run
        ({}, False),
    ],
)
def test_only_the_instrumented_run_keeps_the_binary_tests(monkeypatch, env, want) -> None:
    """The plain run of a reused Rust measurement names its binary directory and no profile: no
    coverage is counted there, so the binary tests the pre-step passed are skipped like the rest."""
    for k in ("GOH_COVERAGE_BIN_DIR", "LLVM_PROFILE_FILE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert lv.instrumented() is want


@pytest.mark.parametrize(
    ("ident", "here", "why"),
    [
        ("OTHER", "ID", "another tree"),
        ("ID", None, "no identity"),
        (None, None, "no identity"),
    ],
)
def test_a_record_of_another_tree_is_not_reused(tmp_path, ident, here, why) -> None:
    nodes, said = lv.reusable(_record(tmp_path, ident, {"t.py::a": False}), here, coverage=False)
    assert nodes == set() and why in said


def test_a_missing_or_malformed_record_is_not_reused(tmp_path: Path) -> None:
    assert lv.reusable(str(tmp_path / "none.json"), "ID", coverage=False)[0] == set()
    (tmp_path / "bad.json").write_text('{"identity": "ID", "passed": [1]}')
    assert lv.reusable(str(tmp_path / "bad.json"), "ID", coverage=False)[0] == set()


SESSION_CONFTEST = f"""
import sys
sys.path.insert(0, {str(REPO_ROOT / "tests")!r})
import _land_verdicts as lv
lv.identity = lambda root=None: "ID"

def pytest_configure(config):
    config.pluginmanager.register(lv, "goh-land-verdicts")
"""

SESSION_TESTS = """
import pytest
import _land_verdicts as lv

def test_pass():
    pass

def test_fail():
    assert False

def test_skip():
    pytest.skip("no verdict")

def test_ran_the_binary(request):
    lv.mark_binary(request.node)

@pytest.fixture
def broken():
    yield
    raise RuntimeError("teardown")

def test_teardown_fails(broken):
    pass
"""


def _session(d: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA", *args, "test_s.py"],
        cwd=d, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
        env=hermetic_env(drop_git=True, **env),
    )  # fmt: skip


@pytest.mark.parametrize("workers", [[], ["-n", "2"]], ids=["serial", "xdist"])
def test_the_pre_steps_passes_are_recorded_and_the_gates_suite_skips_them(
    tmp_path: Path, workers: list[str]
) -> None:
    (tmp_path / "conftest.py").write_text(SESSION_CONFTEST)
    (tmp_path / "test_s.py").write_text(SESSION_TESTS)
    rec = tmp_path / "rec.json"
    _session(tmp_path, *workers, GOH_LAND_VERDICTS_RECORD=str(rec))
    got = json.loads(rec.read_text())
    assert got.pop("sha")  # the commit the pre-step judged: the gate carries from its tree
    assert got == {
        "identity": "ID",
        "passed": {"test_s.py::test_pass": False, "test_s.py::test_ran_the_binary": True},
    }
    plain = _session(tmp_path, *workers, GOH_LAND_VERDICTS_REUSE=str(rec))
    assert "2 tests passed by the pre-land run are reused" in plain.stdout, plain.stdout
    assert "PASSED test_s.py::test_pass" not in plain.stdout, plain.stdout
    assert "FAILED test_s.py::test_fail" in plain.stdout, plain.stdout
    # xdist's controller prints no "deselected" count for its workers: the outcomes say it
    assert "PASSED test_s.py::test_ran_the_binary" not in plain.stdout, plain.stdout
    assert "1 failed, 1 passed, 1 skipped" in plain.stdout, plain.stdout
    # The plain run of a reused Rust measurement (GOH.RUSTCOVSKIP) names a binary dir, no profile.
    # The key is PRESENT and empty: this suite itself runs inside the instrumented part on a land,
    # and conftest's profile carry re-adds an absent LLVM_PROFILE_FILE to every child's env.
    bins = _session(
        tmp_path, *workers, GOH_LAND_VERDICTS_REUSE=str(rec), GOH_COVERAGE_BIN_DIR="x",
        LLVM_PROFILE_FILE="",
    )  # fmt: skip
    assert "PASSED test_s.py::test_ran_the_binary" not in bins.stdout, bins.stdout
    cov = _session(
        tmp_path, *workers, GOH_LAND_VERDICTS_REUSE=str(rec), GOH_COVERAGE_BIN_DIR="x",
        LLVM_PROFILE_FILE=str(tmp_path / "p.profraw"),
    )  # fmt: skip
    assert "PASSED test_s.py::test_ran_the_binary" in cov.stdout, cov.stdout
    # Unmarked is no proof it left no profile: only a pass an instrumented run MEASURED running no
    # binary is skipped under coverage, and a plain pre-step measures nothing (GOH.COVCARRY).
    assert "PASSED test_s.py::test_pass" in cov.stdout, cov.stdout
    assert "1 same-tree passes rerun under coverage" in cov.stdout, cov.stdout
    assert "1 failed, 3 passed, 1 skipped" in cov.stdout, cov.stdout


@pytest.mark.parametrize("workers", [[], ["-n", "2"]], ids=["serial", "xdist"])
def test_a_run_whose_every_node_is_reused_exits_green_and_an_empty_one_does_not(
    tmp_path: Path, workers: list[str]
) -> None:
    """GOH.CARRYALL: the v0.130.0 release gate reused or carried every node, pytest exited 5 ("no
    tests ran") and the land failed. All deselected by the plan is green; nothing collected and
    nothing deselected is still 5."""
    (tmp_path / "conftest.py").write_text(SESSION_CONFTEST)
    (tmp_path / "test_s.py").write_text("def test_pass():\n    pass\n")
    rec = tmp_path / "rec.json"
    assert _session(tmp_path, GOH_LAND_VERDICTS_RECORD=str(rec)).returncode == 0
    reused = _session(tmp_path, *workers, GOH_LAND_VERDICTS_REUSE=str(rec))
    assert "1 tests passed by the pre-land run are reused" in reused.stdout + reused.stderr
    assert reused.returncode == 0, reused.stdout + reused.stderr
    (tmp_path / "test_s.py").write_text("X = 1\n")
    empty = _session(tmp_path, *workers, GOH_LAND_VERDICTS_REUSE=str(rec))
    assert empty.returncode == 5, empty.stdout + empty.stderr


def test_a_test_asking_for_the_binary_is_marked(goh, request) -> None:
    assert (lv.BIN_MARK, True) in request.node.user_properties


def test_a_test_reaching_the_binary_through_the_helper_is_marked(request) -> None:
    from conftest import native_goh_path

    native_goh_path()
    assert (lv.BIN_MARK, True) in request.node.user_properties


# -- land.sh hands the record over -----------------------------------------------------------


def test_land_hands_the_enqueues_record_to_its_gate_never_the_pre_land_beside_it(
    repo, tmp_path
) -> None:
    """The pre-land runs BESIDE the gate (GOH.GATEPARALLEL): it records into a file of its own,
    and the gate's suite reads the enqueue's kept record (GOH_LAND_PRE_FROM), never a file the
    branch beside it is still writing. Both are removed with the land."""
    wt = _worktree(repo, tmp_path)
    seen = tmp_path / "seen"
    _declare(
        wt,
        "GOH_LAND_PRE='printf recorded > \"$GOH_LAND_VERDICTS_RECORD\"'\n"
        f'GOH_LAND_GATE=\'cat "$GOH_LAND_VERDICTS_REUSE" > {seen}; '
        f'echo " $GOH_LAND_VERDICTS_REUSE" >> {seen}\'\n',
    )
    tip = git(wt, "rev-parse", "HEAD").strip()
    keep = tmp_path / "keep"
    keep.write_text(tip + "\n")
    Path(f"{keep}.record").write_text("kept")
    r = _land(wt, GOH_LAND_PRE_FROM=str(keep))
    assert r.returncode == 0, r.stdout + r.stderr
    text, path = seen.read_text().split(" ", 1)
    assert text == "kept" and _main(repo) == tip, r.stdout + r.stderr
    assert not Path(path.strip()).exists(), "the land's record outlived it"
    # A land with no kept verdict hands its gate nothing to reuse: the pre-land's record is its own.
    seen.unlink()
    (wt / "g.txt").write_text("g\n")
    git(wt, "add", "g.txt")
    git(wt, "commit", "-q", "--no-verify", "-m", "more")
    r = _land(wt)
    assert r.returncode == 0, r.stdout + r.stderr
    assert seen.read_text().split(" ", 1)[0] == "", r.stdout + r.stderr


def test_a_green_pre_step_that_wrote_no_record_is_a_fault(repo, tmp_path) -> None:
    """tests/suite_home.py dropped GOH_LAND_VERDICTS_RECORD from the suite for days, and every land
    went green with no record: reuse and carry were inert and nothing said so (GOH.VERDICTCARRY2).
    In a tree whose suite records (tests/_land_verdicts.py), a green pre-step with no record, or an
    empty one, lands nothing and names the fault; a tree with no recorder owes none."""
    wt = _worktree(repo, tmp_path)
    seen = tmp_path / "seen"
    (wt / "tests").mkdir(exist_ok=True)
    (wt / "tests" / "_land_verdicts.py").write_text("# the recorder\n")
    git(wt, "add", "tests")
    for pre in ("true", ': > "$GOH_LAND_VERDICTS_RECORD"'):
        _declare(wt, f"GOH_LAND_PRE='{pre}'\nGOH_LAND_GATE='echo gate >> {seen}'\n")
        before = _main(repo)
        r = _land(wt)
        out = r.stdout + r.stderr
        assert r.returncode == 1, out
        assert "wrote no verdict record" in r.stderr and "nothing landed" in r.stderr, out
        assert _main(repo) == before, out  # the gate beside it may have run: nothing landed
    _declare(wt, "GOH_LAND_PRE='printf x > \"$GOH_LAND_VERDICTS_RECORD\"'\nGOH_LAND_GATE='true'\n")
    r = _land(wt)
    assert r.returncode == 0, r.stdout + r.stderr
    # Calibration: the same empty pre-step, in a tree with no recorder, lands.
    git(wt, "rm", "-q", "tests/_land_verdicts.py")
    _declare(wt, "GOH_LAND_PRE='true'\nGOH_LAND_GATE='true'\n")
    r = _land(wt)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_land_with_no_pre_step_gives_its_gate_no_record(repo, tmp_path) -> None:
    wt = _worktree(repo, tmp_path)
    seen = tmp_path / "seen"
    _declare(wt, f"GOH_LAND_GATE='printf \"[$GOH_LAND_VERDICTS_REUSE]\" > {seen}'\n")
    r = _land(wt)
    assert r.returncode == 0 and seen.read_text() == "[]", r.stdout + r.stderr
