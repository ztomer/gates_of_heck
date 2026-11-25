"""A test's verdict is reused while what it reads is unchanged, and only then (GOH.READKEYVERDICT).

A green verdict is a function of the test and the files it reads. The carry
(tests/_land_verdicts.py) keeps a pass across a diff the selector does not choose it by
(tools/affected_tests.py, whose rules -- names, imports, reach, `TREE_READS`, `JUDGE_READS` -- are
each test's declared read set); the pre-step's own run now carries too (lib/affected_verdict.py
`carrying`), so a run after a fix runs only the fix's readers. A test that READS a file none of
those rules sees is watched as it runs (tests/_read_audit.py) and is refused, not carried, when
that file changes. Pinned here: the read set as stored, the refusal, and two fixture sessions in
the pre-step's own shape -- a change to one test's read reruns exactly that test, and an
undeclared read that changed reruns its test red instead of carrying it green.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import _read_audit as ra
import pytest
from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "lib"))
import affected_verdict as av
import reuse

# -- the read set as stored, and what it refuses -----------------------------------------------


def test_a_node_whose_file_read_a_changed_path_is_refused_and_one_that_did_not_is_not() -> None:
    reads = {"tests/test_a.py": ["lib/a.txt"], "tests/test_b.py": ["lib/b.txt"]}
    nodes = {"tests/test_a.py::one", "tests/test_b.py::two"}
    assert ra.refused(nodes, reads, ["lib/a.txt"]) == {
        "tests/test_a.py::one": ("tests/test_a.py", "lib/a.txt")
    }
    assert ra.refused(nodes, reads, ["lib/c.txt"]) == {}


def test_a_file_the_record_never_watched_is_refused_and_a_watched_empty_one_is_not() -> None:
    reads = {"tests/test_a.py": []}
    got = ra.refused({"tests/test_a.py::one", "tests/test_z.py::two"}, reads, ["x"])
    assert got == {"tests/test_z.py::two": ("tests/test_z.py", "")}


def test_a_listed_directory_covers_every_path_under_it() -> None:
    assert ra.hits("tests/fixtures/", "tests/fixtures/a/b.txt")
    assert not ra.hits("tests/fixtures/", "tests/fixtures_other.txt")
    assert not ra.hits("tests/fixtures.txt", "tests/fixtures.txt.bak")


def test_past_its_span_a_files_reads_are_kept_as_their_directories_coarser_never_finer() -> None:
    many = {f"lib/deep/f{i}.py" for i in range(ra.read_sets.SPAN + 1)} | {"README.md"}
    kept = ra.kept(many)
    assert kept == ["README.md", "lib/deep/"], kept
    assert all(any(ra.hits(k, p) for k in kept) for p in many)  # every read still covered


def test_the_hook_notes_a_file_or_a_listing_under_the_root_and_no_fd_relative_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """shutil.rmtree opens each directory it removes by NAME, relative to its parent's fd, which
    the `open` event does not carry: read from the cwd, those names were "reads" of the checkout
    (the first real run recorded `1TB` and `cache.noindex` for tests/test_cache_tier.py)."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "a.txt").write_text("a")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(ra._state, "roots", (str(tmp_path),))
    monkeypatch.setitem(ra._state, "current", got := set())
    ra._note("open", (str(tmp_path / "lib" / "a.txt"), "r", 0))
    ra._note("os.scandir", (str(tmp_path / "lib"),))
    ra._note("open", ("gone", None, os.O_RDONLY))  # a name relative to a directory fd
    ra._note("open", ("lib", None, os.O_RDONLY))  # ... that a directory here happens to share
    ra._note("open", ("lib/a.txt", "r", 0))  # a file, from the cwd
    ra._note("open", (str(tmp_path.parent / "elsewhere.txt"), "r", 0))
    ra._note("open", (3, "r", 0))
    assert got == {"lib/a.txt", "lib/"}, got


def test_a_recording_run_carries_and_a_run_without_a_record_does_not(monkeypatch) -> None:
    monkeypatch.delenv("GOH_LAND_VERDICTS_REUSE", raising=False)
    assert av.carrying("/x/rec.json") == ["env", f"GOH_LAND_VERDICTS_REUSE={os.devnull}"]
    assert av.carrying("") == [] and av.carrying(os.devnull) == []
    monkeypatch.setenv("GOH_LAND_VERDICTS_REUSE", "/x/gate.json")  # a gate's own: kept
    assert av.carrying("/x/rec.json") == []


# -- the pre-step's own run, end to end (a fixture session with the plugin's seams) -----------


SESSION_CONFTEST = f"""
import os, sys
sys.path.insert(0, {str(REPO_ROOT / "tests")!r})
sys.path.insert(0, {str(REPO_ROOT / "lib")!r})
import _land_verdicts as lv
lv.identity = lambda root=None: "ID-" + os.environ.get("TREE", "T0")
lv.here = lambda root=None: (os.environ.get("TREE", "T0"), "TOOLS")
lv.trees = lambda root, pre: [("c0" * 20, t) for t in os.environ.get("FROM", "T0").split()]
lv.diff = lambda root, sha: os.environ.get("DIFF", "").split()
lv.choose = lambda root, changed, *a, **k: (set(os.environ.get("CHOSEN", "").split()), "")

def pytest_configure(config):
    config.pluginmanager.register(lv, "goh-land-verdicts")
"""
READER = """
from pathlib import Path

def test_reads():
    assert (Path(__file__).parent / "{data}").read_text() == "good"
"""


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    store = tmp_path / "store"
    monkeypatch.setenv("GOH_REUSE_DIR", str(store))
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl"))
    for k in ("GOH_REUSE_TTL_S", "GOH_REUSE_NOW", "GOH_LAND_VERDICTS_REUSE"):
        monkeypatch.delenv(k, raising=False)
    d = tmp_path / "s"
    d.mkdir()
    (d / "conftest.py").write_text(SESSION_CONFTEST)
    for name in ("a", "b", "u"):
        (d / f"test_{name}.py").write_text(READER.format(data=f"data_{name}.txt"))
        (d / f"data_{name}.txt").write_text("good")
    return d


def _suite(d: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA", *args],
        cwd=d, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
        env=hermetic_env(drop_git=True, GOH_REUSE_DIR=os.environ["GOH_REUSE_DIR"],
                         GOH_LOOPCTL=os.environ["GOH_LOOPCTL"], **env),
    )  # fmt: skip


def _pre_step(d: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    """The pre-step's suite: its record, and the REUSE word `carrying` gives a recording run."""
    got = av.carrying(str(d / "rec.json"))
    words = dict([got[1].split("=", 1)]) if got else {}
    return _suite(d, *args, GOH_LAND_VERDICTS_RECORD=str(d / "rec.json"), **words, **env)


@pytest.mark.parametrize("workers", [[], ["-n", "2"]], ids=["serial", "xdist"])
def test_a_change_to_one_tests_read_reruns_exactly_that_test(session: Path, workers) -> None:
    first = _pre_step(session, *workers, TREE="T0")
    assert first.returncode == 0 and "3 passed" in first.stdout, first.stdout
    rec, _ = reuse.lookup_suite(REPO_ROOT, "T0", "TOOLS")
    assert rec["reads"] == {f"test_{n}.py": [f"data_{n}.txt"] for n in "abu"}, rec
    (session / "data_a.txt").write_text("broken")
    again = _pre_step(session, *workers, TREE="T1", DIFF="data_a.txt", CHOSEN="test_a.py")
    out = again.stdout
    assert f"2 deselected, carried from {'c0' * 4}" in out, out
    assert "FAILED test_a.py::test_reads" in out and again.returncode == 1, out
    assert "test_b.py::test_reads" not in out and "test_u.py::test_reads" not in out, out


@pytest.mark.parametrize("workers", [[], ["-n", "2"]], ids=["serial", "xdist"])
def test_an_undeclared_read_that_changed_is_rerun_red_not_carried_green(session, workers) -> None:
    assert _pre_step(session, *workers, TREE="T0").returncode == 0
    (session / "data_u.txt").write_text("broken")
    # The selector chooses nothing for data_u.txt: no rule of it declares test_u.py's read.
    again = _pre_step(session, *workers, TREE="T1", DIFF="data_u.txt", CHOSEN="")
    out = again.stdout
    assert ("1 not carried from c0c0c0c0: test_u.py read data_u.txt, which the diff changes and "
            "no rule of the selector declares") in out, out  # fmt: skip
    assert f"2 deselected, carried from {'c0' * 4}" in out, out
    assert "FAILED test_u.py::test_reads" in out and again.returncode == 1, out


PAIR = """
from pathlib import Path

HERE = Path(__file__).parent

def test_one():
    assert (HERE / "data_one.txt").read_text() == "good"

def test_two():
    assert (HERE / "data_two.txt").read_text() == "good"
"""


def test_a_partial_run_never_drops_the_reads_of_the_siblings_a_carry_takes(session: Path) -> None:
    """GOH.READUNION, the whole chain: main's record, a pre-step that runs ONE test of a file, a
    gate on another tree that carries the file from main, and the next land, which edits what only
    the carried sibling reads. The record of the gate's tree once named test_one's read alone
    (the pre-step's file won), so the edit carried test_two green."""
    (session / "test_p.py").write_text(PAIR)
    for name in ("one", "two"):
        (session / f"data_{name}.txt").write_text("good")
    assert _pre_step(session, TREE="T0").returncode == 0
    one = _pre_step(session, "test_p.py::test_one", TREE="T1", FROM="T9", CHOSEN="test_p.py")
    assert one.returncode == 0, one.stdout
    gate = _suite(session, GOH_LAND_VERDICTS_REUSE=str(session / "rec.json"), TREE="T2", DIFF="x")
    assert "5 deselected, carried from c0c0c0c0" in gate.stdout, gate.stdout
    both = {"test_p.py": ["data_one.txt", "data_two.txt"]}
    rec, _ = reuse.lookup_suite(REPO_ROOT, "T2", "TOOLS")
    (session / "data_two.txt").write_text("broken")
    later = _pre_step(session, TREE="T3", FROM="T2", DIFF="data_two.txt").stdout
    assert "FAILED test_p.py::test_two" in later and "data_two.txt" in later, later
    assert rec["reads"] == {**{f"test_{n}.py": [f"data_{n}.txt"] for n in "abu"}, **both}, rec
