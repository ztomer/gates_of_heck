"""A plain land whose gate tree IS its pre-step's runs the suite once (GOH.SUITEONCE).

GOH.LANDPROFILE (2026-11-18) measured the suite run up to three times per land: the enqueue
pre-step (449-480 s), the lander's pre-land (reused since GOH.PRELANDTWICE) and the gate's own
pytest (plain: 239 s, 1334 cpu-s). When the gate judges the very commit tree the pre-step judged
green (lib/gate_identity.py `suite`: the tree and the tools), against the main the pre-step chose
from, every test is judged already: the ones the diff chooses passed in the pre-step on THESE
bytes, and the rest passed on main's tree, whose suite record the store holds
(lib/pre_reuse.py `suite`). The gate's tools/pytest.sh then takes that verdict instead of a run,
says whose, and the land record names it (`suite_reused`). Anything else runs the suite: another
tree (a rebase, a file a test reads), another base, no record of main's tree, a test main passed
that is neither judged here nor carried, the coverage step's instrumented run, or the audited
`carry` path off.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git
from test_landq import env  # noqa: F401  -- the queue's fixture
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import gate_identity
import pre_reuse
import reuse

# The tree's selection: a changed path chooses the test file named for it; nothing is suite-wide.
TABLE = """\
class Closure:
    def __init__(self, root): pass
    def base(self): return set()
def readers(root, changed, tests=None):
    return {}
def affected(root, changed):
    return {"tests/test_" + p.split(".")[0] + ".py": "changed" for p in changed}
"""
A, B = "tests/test_a.py::test_a", "tests/test_b.py::test_b"


def _commit(repo: Path, msg: str, **files: str) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", msg)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def land(repo: Path, tmp_path: Path, monkeypatch):
    """main (both tests passed on its tree, in the store) and a lane changing `b.txt`, whose
    pre-step record passed test_b on the lane's tree, chosen against main."""
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "store"))
    for k in ("GOH_COVERAGE_BIN_DIR", "LLVM_PROFILE_FILE"):
        monkeypatch.delenv(k, raising=False)
    files = {"tools/affected_tests.py": TABLE, "a.txt": "a\n", "b.txt": "b\n",
             "tests/test_a.py": "", "tests/test_b.py": ""}  # fmt: skip
    base = _commit(repo, "main", **files)
    tools = gate_identity.suite_tools()
    reuse.record_suite(repo, git(repo, "rev-parse", "HEAD^{tree}").strip(), tools,
                       {A: False, B: False}, set(), True, "land gate")  # fmt: skip
    tip = _commit(repo, "lane", **{"b.txt": "the lane's\n"})
    rec = tmp_path / "verdicts"
    rec.write_text(json.dumps({"identity": gate_identity.suite(repo), "sha": tip,
                               "passed": {B: False}, "base": base,
                               "selection": "affected"}))  # fmt: skip
    return repo, rec, base, tip


def test_the_gate_takes_the_pre_steps_verdict_on_the_tree_it_judged(land):
    repo, rec, base, tip = land
    ok, why, vid = pre_reuse.suite(repo, rec, base)
    assert ok, why
    assert vid == f"{tip[:12]}:{json.loads(rec.read_text())['identity'][:12]}"
    # The tip's tree now has its own record, every test in it: the next land carries from it.
    got, _ = reuse.lookup_suite(repo, git(repo, "rev-parse", "HEAD^{tree}").strip(),
                                gate_identity.suite_tools())  # fmt: skip
    assert got is not None and set(got["passed"]) == {A, B} and not got["full"]


def test_a_tree_that_differs_in_a_file_a_test_reads_runs_the_suite(land):
    repo, rec, base, _ = land
    _commit(repo, "after the pre-step", **{"a.txt": "moved under the verdict\n"})
    ok, why, _ = pre_reuse.suite(repo, rec, base)
    assert not ok and "another tree" in why, why


def test_a_working_tree_that_is_not_the_commit_runs_the_suite(land):
    repo, rec, base, _ = land
    (repo / "b.txt").write_text("edited, uncommitted\n")
    ok, why, _ = pre_reuse.suite(repo, rec, base)
    assert not ok and "not its commit's tree" in why, why


def test_another_base_or_no_record_of_mains_tree_runs_the_suite(land, tmp_path, monkeypatch):
    repo, rec, base, tip = land
    ok, why, _ = pre_reuse.suite(repo, rec, tip)
    assert not ok and "chose against" in why, why
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "empty-store"))
    ok, why, _ = pre_reuse.suite(repo, rec, base)
    assert not ok and "no suite record of main" in why, why


def test_a_test_main_passed_that_nothing_judged_here_runs_the_suite(land):
    repo, rec, base, _ = land
    # The pre-step chose test_b but its record does not hold it (a skip is no verdict).
    rec.write_text(json.dumps({**json.loads(rec.read_text()), "passed": {}}))
    ok, why, _ = pre_reuse.suite(repo, rec, base)
    assert not ok and "neither judged" in why and B in why, why


def test_a_record_of_no_whole_selection_or_coverage_runs_the_suite(land, monkeypatch):
    repo, rec, base, _ = land
    narrow = {**json.loads(rec.read_text()), "selection": None}
    rec.write_text(json.dumps(narrow))
    ok, why, _ = pre_reuse.suite(repo, rec, base)
    assert not ok and "not the pre-step's whole selection" in why, why
    rec.write_text(json.dumps({**narrow, "selection": "affected"}))
    monkeypatch.setenv("GOH_COVERAGE_BIN_DIR", "/x")
    monkeypatch.setenv("LLVM_PROFILE_FILE", "/x/%p.profraw")
    ok, why, _ = pre_reuse.suite(repo, rec, base)
    assert not ok and "instrumented" in why, why


def _runner(tmp_path: Path, answer: int, reuse_file: Path | None) -> tuple[str, list[str]]:
    """tools/pytest.sh with a stub interpreter: `pre_reuse.py` answers ANSWER, `-m pytest` is
    logged. (its output, the calls the stub saw)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    seen = tmp_path / "seen"
    stub = tmp_path / "python3"
    stub.write_text(
        "#!/bin/bash\n"
        f'case "$*" in *timing_ledger.py*|*worker_share.py*) exec "{sys.executable}" "$@" ;; esac\n'
        f'case "$*" in *pre_reuse.py*) echo "$*" >> "{seen}"; echo "the stub judged"; '
        f"echo 0123456789ab:feedfacecafe; exit {answer} ;; esac\n"
        f'if [ "$1 $2" = "-m pytest" ] && [ "$3" != --version ]; then echo pytest >> "{seen}"; fi\n'
        "exit 0\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    drop = ("GOH_GATE_WORKERS", "GOH_GATE_SLOT", "GOH_LAND_VERDICTS_REUSE", "LLVM_PROFILE_FILE")
    run_env = {k: v for k, v in os.environ.items() if k not in drop}
    run_env.update(GOH_PY=str(stub), GOH_PY_OK=str(stub), GATES_OF_HECK_SUITE_NESTED_OK="1",
               GOH_SUITE_CORES="2", GOH_TIMING_DIR=str(tmp_path / "timing"))  # fmt: skip
    if reuse_file is not None:
        run_env["GOH_LAND_VERDICTS_REUSE"] = str(reuse_file)
    done = subprocess.run(["bash", str(REPO_ROOT / "tools" / "pytest.sh")], env=run_env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
                          timeout=patience(60))  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
    calls = seen.read_text().splitlines() if seen.is_file() else []
    return done.stdout + done.stderr, calls


def _record(tmp_path: Path) -> Path:
    rec = tmp_path / "verdicts"
    rec.write_text("{}")
    (tmp_path / "verdicts.base").write_text("f" * 40 + "\n")
    return rec


def test_the_gates_runner_runs_no_pytest_on_a_reused_verdict(tmp_path):
    out, calls = _runner(tmp_path, 0, _record(tmp_path))
    assert len(calls) == 1 and "--suite" in calls[0] and "f" * 40 in calls[0], calls
    assert "pytest" not in calls, out
    assert (tmp_path / "verdicts.reused").read_text().split() == ["0123456789ab:feedfacecafe"]
    assert "not run again" in out, out


def test_the_gates_runner_runs_pytest_when_the_verdict_does_not_hold(tmp_path):
    out, calls = _runner(tmp_path, 1, _record(tmp_path))
    assert calls[-1] == "pytest" and not (tmp_path / "verdicts.reused").exists(), (calls, out)
    _, calls = _runner(tmp_path / "plain", 0, None)
    assert calls == ["pytest"], calls  # no land's record: nothing to ask


def test_the_pre_step_stamps_its_whole_selection_and_its_base(repo: Path, tmp_path: Path):
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import affected_tests
    finally:
        sys.path.pop(0)
    head = git(repo, "rev-parse", "HEAD").strip()
    assert affected_tests._base_sha(repo, "HEAD") == head
    assert affected_tests._base_sha(repo, "no-such-rev") is None
    rec = tmp_path / "rec.json"
    rec.write_text(json.dumps({"identity": "ID", "passed": {}}))
    affected_tests._stamp(str(rec), None)  # a narrowed run (--changed, --readers-only): unstamped
    assert "selection" not in json.loads(rec.read_text())
    affected_tests._stamp(str(rec), head)
    got = json.loads(rec.read_text())
    assert (got["base"], got["selection"]) == (head, "affected"), got


def test_a_tree_that_changed_during_the_run_records_no_identity(tmp_path: Path):
    """The pre-step runs in the lane's live worktree (GOH.LANDSNAPSHOT): its record names the tree
    only when the run ended on the bytes it started on."""
    from test_land_verdicts import SESSION_CONFTEST, SESSION_TESTS, _session

    moving = SESSION_CONFTEST.replace(
        'lv.identity = lambda root=None: "ID"',
        "import itertools\n_n = itertools.count()\nlv.identity = lambda root=None: f'ID{next(_n)}'",
    )
    assert moving != SESSION_CONFTEST
    (tmp_path / "conftest.py").write_text(moving)
    (tmp_path / "test_s.py").write_text(SESSION_TESTS)
    rec = tmp_path / "rec.json"
    _session(tmp_path, GOH_LAND_VERDICTS_RECORD=str(rec))
    got = json.loads(rec.read_text())
    assert got["identity"] is None and got["passed"], got


def test_the_land_record_names_the_verdict_the_gates_suite_took(repo, tmp_path, env):  # noqa: F811
    """tools/land.sh hands the gate its base beside the record (`.base`) and names the verdict id
    the gate's suite left (`.reused`) in the land's ledger row, `suite_reused`."""
    from test_landq import _land_wt, landq, recorded

    sys.path.insert(0, str(REPO_ROOT / "lib"))
    import timing_ledger

    wt, _ = _land_wt(repo, tmp_path)
    base = git(repo, "rev-parse", "HEAD").strip()
    gate = ('test "$(cat "$GOH_LAND_VERDICTS_REUSE.base")" = ' + base +
            ' && echo 0123456789ab:feedfacecafe > "$GOH_LAND_VERDICTS_REUSE.reused"')  # fmt: skip
    env = {**env, "GOH_LAND_GATE": gate, "GOH_TIMING_DIR": str(tmp_path / "timing"),
           "GOH_LAND_PRE": 'echo "{}" > "$GOH_LAND_VERDICTS_RECORD"'}  # fmt: skip
    done = landq(env, "--enqueue", "--land", str(wt), "--item", "T.T", "--no-start")
    assert done.returncode == 0, done.stdout + done.stderr
    assert landq(env, "--drain").returncode == 0
    got = recorded(env)["T.T"]
    assert got["state"] == "landed", Path(got["log"]).read_text()
    rows = [
        r for r in timing_ledger.read(tmp_path / "timing" / "ledger.jsonl") if r["kind"] == "land"
    ]
    assert [r.get("suite_reused") for r in rows] == ["0123456789ab:feedfacecafe"], rows
