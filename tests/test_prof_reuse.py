"""The ship takes a binary test's stored profile by its exact key (GOH.COVPROFILEREUSE).

v0.140.1's ship ran its instrumented pytest for 881 s and read 0 stored profiles while 9 of the 10
ships before it changed no Rust input (tests/_prof_reuse.py). Pinned here on a real instrumented
binary, through the ship's own @tree run: an exact key reuses the profile and the report still
counts the line it covers; a changed binary, test input or tool version runs the test; the shadow's
compare agrees on a true profile and turns the path off, filing a fault, on a planted one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from test_cov_carry import PROG, RUNS_IT, _llvm_bin
from test_prof_store import _build, _ran_line, _swap
from test_verdict_carry import SESSION_CONFTEST

sys.path.insert(0, str(REPO_ROOT / "lib"))
import prof_store
import reuse
import reuse_audit

RUNS = "test_r.py::test_runs_it"
llvm = pytest.mark.skipif(_llvm_bin() is None, reason="rustc's llvm-tools not installed")

# The ship's session: the digest of a test's inputs and the tools are the environment's to name,
# so a test can move each alone (the real digest is pinned by test_the_digest_* below).
CONFTEST = SESSION_CONFTEST.replace(
    "def pytest_configure",
    """import _prof_reuse as pr
pr.digests = lambda root, files: {f: os.environ.get("DIGEST", "D0") for f in files}
pr.tools = lambda: os.environ.get("CTOOLS", "t0")
if os.environ.get("EVERY_PR"):
    reuse.PATHS["profreuse"] = dataclasses.replace(reuse.PATHS["profreuse"],
                                                   every=int(os.environ["EVERY_PR"]))

def pytest_configure""",
)


@pytest.fixture
def ship(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bins = tmp_path / "bins"
    exe = _build(bins, PROG)
    d = tmp_path / "s"
    d.mkdir()
    (d / "conftest.py").write_text(CONFTEST)
    (d / "test_r.py").write_text(RUNS_IT.format(exe=str(exe)))
    store = tmp_path / "store"
    monkeypatch.setenv("GOH_REUSE_DIR", str(store))
    runs = iter(range(100))

    def run(tree: str, **env: str) -> tuple[subprocess.CompletedProcess, Path, dict]:
        prof, out = tmp_path / f"p{next(runs)}", tmp_path / "counts.json"
        prof.mkdir()
        out.unlink(missing_ok=True)
        child = hermetic_env(drop_git=True, GOH_REUSE_DIR=str(store), TREE=tree, CHOSEN="",
                             GOH_LOOPCTL=str(d / "no-loopctl"), GOH_LAND_VERDICTS_REUSE="@tree",
                             GOH_LAND_VERDICTS_RECORD=os.devnull, GOH_PROF_REUSE_OUT=str(out),
                             GOH_COVERAGE_BIN_DIR=str(bins),
                             LLVM_PROFILE_FILE=str(prof / "goh-%p.profraw"), **env)  # fmt: skip
        r = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA"],
                           cwd=d, capture_output=True, text=True, env=child,
                           stdin=subprocess.DEVNULL, check=False)  # fmt: skip
        prof_store._ids.clear()
        counts = json.loads(out.read_text())["binaries"][0] if out.is_file() else {}
        return r, prof, counts

    return run, bins, exe, store


@llvm
def test_an_exact_key_reuses_the_profile_and_the_report_still_counts_its_line(ship, tmp_path):
    run, bins, exe, _ = ship
    first, _, counts = run("T0")
    assert first.returncode == 0 and f"PASSED {RUNS}" in first.stdout, first.stdout
    assert counts == {"bin": prof_store.bin_id(bins), "reused": 0, "fresh": 1, "audit": "none"}
    second, prof, counts = run("T1")  # another tree: the ship after, nothing it reads changed
    assert second.returncode == 0, second.stdout + second.stderr
    assert f"PASSED {RUNS}" not in second.stdout, second.stdout
    assert "1 binary tests deselected, their stored profiles in the report" in second.stdout
    assert counts["reused"] == 1 and counts["fresh"] == 0 and counts["audit"] == "reuse", counts
    assert _ran_line(exe, prof, tmp_path) != "0", "the floor lost the reused run"
    rec, _ = reuse.lookup_suite(REPO_ROOT, "T1", "TOOLS")
    assert rec["full"] and rec["full_instrumented"], rec  # the release kit's full instrumented run
    assert rec["passed"][RUNS] is True and rec["measured"][RUNS] is True, rec


@llvm
@pytest.mark.parametrize("change", ["binary", "test input", "tool version"])
def test_a_changed_binary_test_input_or_tool_version_runs_the_test(ship, tmp_path, change):
    run, _, exe, _ = ship
    assert run("T0")[0].returncode == 0
    env = {"DIGEST": "D1"} if change == "test input" else {}
    env |= {"CTOOLS": "t1"} if change == "tool version" else {}
    if change == "binary":
        other = tmp_path / "other"
        _build(other, PROG.replace('"ran"', '"ran again"'))
        _swap(other / "prog", exe)  # the same path, other bytes
    moved, prof, counts = run("T1", **env)
    assert moved.returncode == 0 and f"PASSED {RUNS}" in moved.stdout, moved.stdout
    assert counts["reused"] == 0 and counts["fresh"] == 1, counts
    assert _ran_line(exe, prof, tmp_path) != "0"


def _plant(store: Path, bins: Path, tmp_path: Path) -> None:
    """Every stored profile replaced by a run of the binary down the OTHER branch."""
    llvm_bin = _llvm_bin()
    assert llvm_bin is not None
    raw = tmp_path / "planted"
    raw.mkdir()
    subprocess.run([str(bins / "prog")], env=hermetic_env(LLVM_PROFILE_FILE=str(raw / "p.profraw")),
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)  # fmt: skip
    wrong = tmp_path / "wrong.profdata"
    subprocess.run([str(llvm_bin / "llvm-profdata"), "merge", "--sparse", "-o", str(wrong),
                    str(raw / "p.profraw")], check=True, capture_output=True,
                   stdin=subprocess.DEVNULL)  # fmt: skip
    stored = list(store.glob("profstore/ship-*/*.profdata"))
    assert stored, "nothing stored to plant over"
    for f in stored:
        f.unlink()
        f.write_bytes(wrong.read_bytes())


@llvm
@pytest.mark.parametrize("planted", [False, True], ids=["true-profile", "planted-mismatch"])
def test_the_shadow_compares_reused_with_fresh_and_a_mismatch_files_a_fault(
    ship, tmp_path, planted
):
    run, bins, _, store = ship
    assert run("T0")[0].returncode == 0
    if planted:
        _plant(store, bins, tmp_path)
    shadow, _, counts = run("T1", EVERY_PR="1")
    assert f"PASSED {RUNS}" in shadow.stdout, shadow.stdout  # the shadow runs it anyway
    assert counts["audit"] == "shadow" and counts["reused"] == 0, counts
    state = reuse_audit.state("profreuse")
    if not planted:
        assert "the shadow's 1 fresh runs agree" in shadow.stdout, shadow.stdout
        assert state["state"] == "on", state
        return
    assert "profile reuse: DISAGREE" in shadow.stdout, shadow.stdout
    assert state["state"] == "off" and state.get("fault"), state
    after, _, counts = run("T2")  # off: every later ship measures
    assert f"PASSED {RUNS}" in after.stdout and counts["audit"] == "off", counts


def _tree(root: Path) -> None:
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "conftest.py").write_text("")
    (root / "tests" / "_helper.py").write_text("X = 1\n")
    (root / "tests" / "test_a.py").write_text("import _helper\n\ndef test_x():\n    pass\n")
    (root / "tests" / "test_b.py").write_text("def test_y():\n    pass\n")
    (root / "tests" / "test_c.py").write_text("def test_every_file():\n    pass\n")
    (root / "README").write_text("prose\n")


def test_the_digest_moves_with_the_test_file_and_what_it_imports_only(tmp_path: Path) -> None:
    import _prof_reuse

    root = tmp_path / "r"
    _tree(root)
    files = ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]
    first = _prof_reuse.digests(root, files)
    assert first["tests/test_c.py"] is None  # a tree reader: what it reads is the tree
    assert first["tests/test_a.py"] and first["tests/test_b.py"]
    (root / "README").write_text("other prose\n")
    assert _prof_reuse.digests(root, files) == first  # calibration: an unread file moves nothing
    (root / "tests" / "_helper.py").write_text("X = 2\n")
    moved = _prof_reuse.digests(root, files)
    assert moved["tests/test_a.py"] != first["tests/test_a.py"]  # its import changed
    assert moved["tests/test_b.py"] == first["tests/test_b.py"]
    (root / "tests" / "test_b.py").write_text("def test_y():\n    assert True\n")
    assert _prof_reuse.digests(root, files)["tests/test_b.py"] != first["tests/test_b.py"]


def test_the_suite_keeps_the_ships_counts_file() -> None:
    """tests/suite_home.py drops every caller GOH_* key it does not name: the first measured ship
    run of this change wrote no counts, the plugin never saw where (GOH_PROF_REUSE_OUT)."""
    import suite_home

    assert "GOH_PROF_REUSE_OUT" in suite_home.CALLER_INPUTS
