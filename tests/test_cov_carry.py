"""The coverage profile carry and the owner's three terms for it (GOH.COVCARRY, D-0011 option 4).

Owner, 2026-11-17: an instrumented land may reuse a binary-running test's stored profile when its
diff leaves the instrumented binary unchanged, but only with all three of: (1) every land's floor
judged on a complete merged profile; (2) a fresh full instrumented run on main's tip before every
ship -- the idle lander's full run, required by the release's full-suite check; (3) that run the
shadow of the carry, compared per crate (lib/reuse_audit.py compare), a disagreement turning the
carry off. Pinned here: each term, and the carry's own seam (lib/prof_carry.py).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import _land_verdicts as lv
import pytest
from conftest import REPO_ROOT, git, hermetic_env
from test_verdict_carry import SESSION_CONFTEST, _gate

sys.path.insert(0, str(REPO_ROOT / "lib"))
import gate_identity
import idle_suite
import reuse
import ship_suite


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl"))
    monkeypatch.delenv("GOH_REUSE_TTL_S", raising=False)
    monkeypatch.delenv("GOH_REUSE_NOW", raising=False)
    return tmp_path / "store"


# -- term 2: a ship stands on a fresh INSTRUMENTED full run ---------------------------------------


def test_a_plain_full_run_is_no_full_suite_verdict_for_a_ship(store, repo) -> None:
    sha = git(repo, "rev-parse", "HEAD").strip()
    tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
    tools = gate_identity.suite_tools()
    reuse.record_suite(repo, tree, tools, {"t.py::a": False}, set(), True, "gate")
    rec, why = ship_suite.full_suite(repo, sha)
    assert rec is None and "fresh INSTRUMENTED full run" in why, why
    reuse.record_suite(repo, tree, tools, {"t.py::a": True}, set(), True, "idle", instrumented=True)
    assert ship_suite.full_suite(repo, sha)[0] is not None
    reuse.record_suite(repo, tree, tools, {}, {"t.py::a"}, False, "gate")  # a later failure
    assert ship_suite.full_suite(repo, sha)[0] is None


def test_an_instrumented_land_that_carried_records_it_drove_the_instrumented_binary(
    store, repo
) -> None:
    """GOH.PROFKEYMISS: `instrumented` was true only for a FULL instrumented run, so a land-gate
    run that measured every test it ran against the instrumented goh, carrying the rest, read
    `instrumented: False` (seen in a carry record: 2641 carried, 6399 measured). The ship's own
    question -- was the FULL judgement instrumented -- is `full_instrumented`, and a partial
    instrumented run on a tree a plain run judged whole never answers it."""
    sha = git(repo, "rev-parse", "HEAD").strip()
    tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
    tools = gate_identity.suite_tools()
    reuse.record_suite(repo, tree, tools, {"t.py::a": True}, set(), False, "land gate", carried=5,
                       instrumented=True, measured={"t.py::a": True})  # fmt: skip
    rec = reuse.lookup_suite(repo, tree, tools)[0]
    assert rec["instrumented"] and not rec["full_instrumented"], rec
    reuse.record_suite(repo, tree, tools, {"t.py::a": True}, set(), True, "gate")  # plain, whole
    rec = reuse.lookup_suite(repo, tree, tools)[0]
    assert rec["full"] and rec["instrumented"] and not rec["full_instrumented"], rec
    rec, why = ship_suite.full_suite(repo, sha)
    assert rec is None and "fresh INSTRUMENTED full run" in why, why
    reuse.record_suite(repo, tree, tools, {"t.py::a": True}, set(), True, "idle", instrumented=True)
    assert ship_suite.full_suite(repo, sha)[0] is not None
    old = {"passed": {"t.py::a": True}, "full": True, "instrumented": True}  # before the split
    reuse.record(reuse.CARRY, reuse.carry_fields(repo, tree, tools), "green", "idle", old)
    assert ship_suite.full_suite(repo, sha)[0] is not None


def test_the_suite_records_whether_it_drove_the_instrumented_binary(tmp_path, store) -> None:
    d = tmp_path / "s"
    d.mkdir()
    (d / "conftest.py").write_text(SESSION_CONFTEST)
    (d / "test_s.py").write_text("def test_pass():\n    pass\n")
    plain = _gate(d, store, TREE="TP")
    assert plain.returncode == 0, plain.stdout
    inst = _gate(d, store, TREE="TI", GOH_COVERAGE_BIN_DIR=str(tmp_path / "bins"),
                 LLVM_PROFILE_FILE=str(tmp_path / "goh-%p.profraw"))  # fmt: skip
    assert inst.returncode == 0, inst.stdout
    assert not reuse.lookup_suite(REPO_ROOT, "TP", "TOOLS")[0]["instrumented"]
    rec, _ = reuse.lookup_suite(REPO_ROOT, "TI", "TOOLS")
    assert rec["full"] and rec["instrumented"] and rec["full_instrumented"], rec


def test_the_idle_full_run_is_the_instrumented_coverage_run(tmp_path) -> None:
    """The idle lander's default command runs the tip's coverage gate in Rust mode with the
    external suite and the floor its .gatesrc declares -- a stand-in gate says what it was asked."""
    (tmp_path / "gates").mkdir()
    (tmp_path / "gates" / "coverage_gate.sh").write_text(
        'printf "%s\\n" "$GOH_PROFCARRY_AUDIT" "$@"\n'  # fresh: it compares, never claims
    )
    (tmp_path / ".gatesrc").write_text("GOH_COV_FLOOR_RUST=93\nGOH_COV_RUST_EXTERNAL='suite x'\n")
    argv = idle_suite.default_argv()
    assert argv[-3:-1] == ["bash", "-c"], argv
    r = subprocess.run(argv[-3:], cwd=tmp_path, capture_output=True, text=True, check=False,
                       stdin=subprocess.DEVNULL, env=hermetic_env())  # fmt: skip
    assert r.returncode == 0, r.stderr
    want = ["fresh", "--lang", "rust", "--external", "suite x", "--floor", "93", "."]
    assert r.stdout.split("\n")[:-1] == want, r.stdout


# -- term 1: the floor is judged on every run's profile; only a MEASURED node is carried ----------


def _llvm_bin() -> Path | None:
    r = subprocess.run(["rustc", "--print", "target-libdir"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    bins = Path(r.stdout.strip()).parent / "bin"
    return bins if r.returncode == 0 and (bins / "llvm-profdata").exists() else None


def test_under_coverage_only_a_node_measured_running_no_binary_is_carried() -> None:
    passed = {"t.py::marked": True, "t.py::unmarked": False, "t.py::seen": False}
    measured = {"t.py::marked": True, "t.py::unmarked": True, "t.py::seen": False}
    assert lv.carried(passed, set(), coverage=True, binary=False, measured=measured) == {
        "t.py::seen"}  # fmt: skip
    assert lv.carried(passed, set(), coverage=True, binary=False) == set()  # nothing measured
    assert lv.carried(passed, set(), coverage=False, binary=False) == set(passed)  # plain


PROG = """fn main() {
    let a: Vec<String> = std::env::args().collect();
    if a.len() > 1 {
        println!("ran");
    } else {
        println!("planted: no test takes this branch");
    }
}
"""
RUNS_IT = """import subprocess, sys
def test_runs_it():
    subprocess.run([{exe!r}, "x"], check=True, capture_output=True)

def test_pure():
    pass
"""


@pytest.mark.skipif(_llvm_bin() is None, reason="rustc's llvm-tools component not installed")
def test_an_unmarked_test_that_ran_the_binary_is_measured_and_its_profile_reaches_the_report(
    tmp_path: Path, store: Path
) -> None:
    llvm = _llvm_bin()
    assert llvm is not None
    (tmp_path / "prog.rs").write_text(PROG)
    exe = tmp_path / "prog"
    subprocess.run(["rustc", "-C", "instrument-coverage", "-o", str(exe), "prog.rs"],
                   cwd=tmp_path, check=True, capture_output=True,
                   stdin=subprocess.DEVNULL)  # fmt: skip
    d, prof = tmp_path / "s", tmp_path / "profiles"
    d.mkdir()
    prof.mkdir()
    (d / "conftest.py").write_text(SESSION_CONFTEST)  # the recorder rides with lv
    (d / "test_r.py").write_text(RUNS_IT.format(exe=str(exe)))
    r = _gate(d, store, TREE="TM", GOH_COVERAGE_BIN_DIR=str(tmp_path),
              LLVM_PROFILE_FILE=str(prof / "goh-%p.profraw"))  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    rec, _ = reuse.lookup_suite(REPO_ROOT, "TM", "TOOLS")
    assert rec["passed"] == {"test_r.py::test_runs_it": True, "test_r.py::test_pure": False}, rec
    assert rec["measured"] == rec["passed"], rec  # unmarked, yet measured running the binary
    merged = sorted(p.name for p in prof.glob("*.profraw"))
    assert len(merged) == 1 and merged[0].startswith("t-"), merged  # one indexed profile, no raw
    assert not list((prof / "t").glob("*/*/*.profraw")), "a per-test raw profile was left behind"
    # The report's own merge (cargo llvm-cov globs *.profraw into llvm-profdata) counts it, and the
    # planted branch no test takes still reads 0: the floor still sees an uncovered line.
    whole = tmp_path / "whole.profdata"
    subprocess.run([str(llvm / "llvm-profdata"), "merge", "-sparse", "-o", str(whole),
                    *map(str, prof.glob("*.profraw"))], check=True, capture_output=True,
                   stdin=subprocess.DEVNULL)  # fmt: skip
    out = subprocess.run([str(llvm / "llvm-cov"), "export", "-format=lcov", str(exe),
                          f"-instr-profile={whole}"], check=True, capture_output=True, text=True,
                         stdin=subprocess.DEVNULL).stdout  # fmt: skip
    da = dict(ln[3:].split(",")[:2] for ln in out.splitlines() if ln.startswith("DA:"))
    assert da["4"] != "0" and da["6"] == "0", da  # `ran` counted; the planted branch uncovered
