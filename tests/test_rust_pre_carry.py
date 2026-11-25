"""A Rust pre-step's verdict names what it measured, and its retry carries (GOH.RUSTPRECARRY).

The pre-land set's kept verdict (lib/affected_verdict.py) named neither whether the run measured the
change's Rust lines nor the base they are judged from: a lane's plain `--run` handed its green to
the enqueue's `--run --rust-cov`, which then measured nothing. And a measured run carried nothing:
a retry after a red ran every test of the Rust change again (OLDFORMS, 1050 s then 1054 s).
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

import _land_trees
import pytest
from conftest import REPO_ROOT, git, hermetic_env
from test_cov_carry import PROG, _llvm_bin
from test_prof_store import _build
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests as at
import body_tables
import gate_identity
import reuse
import suite_home

from lib import land_rustcov, prof_store

av = at.av
SET = {"tests/test_a.py": "changed"}
SUITE = 'CALLER_INPUTS = ("GOH_PY", "GOH_LAND_VERDICTS_RECORD")\n'
PLAN = ("b" * 40, "CMD")


def _commit(repo: Path, msg: str, *parents: str) -> str:
    """A commit of the checkout's own tree on PARENTS, HEAD moved to it."""
    tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
    args = [a for p in parents for a in ("-p", p)]
    sha = git(repo, "commit-tree", tree, *args, "-m", msg).strip()
    git(repo, "update-ref", "HEAD", sha)
    return sha


@pytest.fixture
def lane(repo: Path, tmp_path: Path, monkeypatch):
    """A clean checkout whose set is run by the command line, its tests a counter."""
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("GOH_PY", sys.executable)
    monkeypatch.setenv(av.RECORD, str(tmp_path / "record.json"))
    for k in ("GATES_OF_HECK_SUITE_RUNNER", "PYTEST_ADDOPTS", av.REUSE, av.SHAPE):
        monkeypatch.delenv(k, raising=False)
    for rel, text in {"tests/suite_home.py": SUITE, "gates/verdict_free_keys.txt": ""}.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "lane")
    monkeypatch.setattr(at, "affected", lambda *a, **k: dict(SET))
    monkeypatch.setattr(at, "changed_paths", lambda *a: ["crates/x.rs"])
    monkeypatch.setattr(at, "canary", lambda *a: 0)
    monkeypatch.setattr(at.fast_tests, "run", lambda *a: 0)
    monkeypatch.setattr(at.land_rustcov, "plan", lambda *a: PLAN)
    monkeypatch.setattr(av._lib("pre_owed"), "run", lambda *a: 0)
    return repo


@pytest.fixture
def ran(lane: Path, monkeypatch) -> list:
    """What each run that was not taken from the store measured: its plan."""
    seen: list = []

    def run(root, changed, chosen, base=None, rust=None) -> int:
        seen.append(rust)
        rec = {"identity": gate_identity.suite(root), "sha": git(root, "rev-parse", "HEAD").strip(),
               "passed": dict.fromkeys(chosen, False)}  # fmt: skip
        Path(os.environ[av.RECORD]).write_text(json.dumps(rec))
        return 0

    monkeypatch.setattr(at, "run", run)
    return seen


def _run(root: Path, *args: str, base: str = "HEAD") -> int:
    return at.main(["--run", "--root", str(root), "--base", base, *args])


# -- the key ---------------------------------------------------------------------------------------


def test_a_plain_green_is_no_verdict_of_a_run_that_measures(lane, ran) -> None:
    assert _run(lane) == 0
    assert _run(lane, "--rust-cov") == 0
    assert ran == [None, PLAN], "a run that measured no Rust line stood for one that owes it"


def test_a_flag_that_measures_nothing_is_the_same_run(lane, ran, monkeypatch) -> None:
    monkeypatch.setattr(at.land_rustcov, "plan", lambda *a: None)  # no .rs file changed
    assert _run(lane) == 0
    assert _run(lane, "--rust-cov") == 0
    assert ran == [None], "the enqueue ran again a set whose flag asks nothing more"


def test_another_merge_base_is_another_verdict_and_a_moved_main_is_not(lane, ran) -> None:
    root = git(lane, "rev-parse", "HEAD").strip()
    one = _commit(lane, "one", root)
    git(lane, "branch", "-f", "trunk", root)
    tip = _commit(lane, "tip", one)
    assert _run(lane, "--rust-cov", base="trunk") == 0
    git(lane, "update-ref", "refs/heads/trunk", _commit(lane, "main moved", root))
    git(lane, "update-ref", "HEAD", tip)
    assert _run(lane, "--rust-cov", base="trunk") == 0
    assert len(ran) == 1, "main moved, the change did not: the same lines are judged"
    git(lane, "update-ref", "refs/heads/trunk", one)
    assert _run(lane, "--rust-cov", base="trunk") == 0
    assert len(ran) == 2, "a verdict on the lines of another diff was taken (same tree, same set)"


def test_a_narrowed_lane_run_is_no_verdict_of_the_whole_set(lane, ran, monkeypatch) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k one")
    assert _run(lane) == 0
    monkeypatch.delenv("PYTEST_ADDOPTS")
    assert _run(lane) == 0
    assert len(ran) == 2, "a -k run of part of the set stood for the whole of it"


def test_every_paths_key_fields_have_a_column_in_the_audit() -> None:
    """A field with none is Refused when the audit writes its evidence (lib/body_columns.py)."""
    known = {p.removeprefix("fields.") for p in body_tables._KEYED}
    odd = {p.name: sorted(set(p.fields) - known) for p in reuse.PATHS.values()
           if p.shadow and set(p.fields) - known}  # fmt: skip
    assert not odd, odd


# -- a stopped run, a partial one ------------------------------------------------------------------


@pytest.mark.parametrize("rc", [1, 2, 75, 124, 137, -9])
def test_a_stopped_measured_run_is_never_a_verdict(lane, ran, monkeypatch, rc) -> None:
    """Its suite may have ended and written a whole record before the diff was judged."""
    whole = at.run
    monkeypatch.setattr(at, "run", lambda *a: whole(*a) or rc)
    assert _run(lane, "--rust-cov") == rc
    assert not av.stands(os.environ[av.RECORD])
    assert _run(lane, "--rust-cov") == rc
    assert len(ran) == 2, "a measured run that did not end green was kept"


def test_the_measured_run_is_never_a_part_the_callers_environment_names() -> None:
    """GOH_COV_PART ends green once its part is exported, its diff never judged."""
    argv = land_rustcov.argv(REPO_ROOT, PLAN, ["tests/test_a.py"])
    for key in ("GOH_COV_PART", "GOH_COV_PARTS_IN"):
        assert argv[argv.index(key) - 1] == "-u", argv
    assert argv.index("GOH_COV_PARTS_IN") < argv.index("bash")


def test_the_profile_carrys_kill_switch_reaches_the_suite() -> None:
    assert prof_store.KILL in suite_home.CALLER_INPUTS
    assert not any(prof_store.KILL in a for a in land_rustcov.argv(REPO_ROOT, PLAN, []))


# -- the retry carries -----------------------------------------------------------------------------


def _ledger(monkeypatch, *rows: dict, diffs: dict[str, list[str]] | None = None) -> None:
    monkeypatch.setattr(prof_store, "ledger", lambda: list(rows))
    monkeypatch.setattr(prof_store, "live", lambda r: r["kept"] and r["key"] != "pruned")
    monkeypatch.setattr(land_rustcov, "_paths", lambda _r, tree: (diffs or {}).get(tree))
    monkeypatch.setattr(
        land_rustcov.binary_runners,
        "build_input",
        lambda _r, ch: next((p for p in ch if p.startswith("crates/")), None),
    )


def _row(tree: str, key: str = "k", kept: int = 9) -> dict:
    return {"tree": tree, "key": key, "kept": kept}


def test_a_retry_names_the_nearest_tree_measured_with_its_binary(monkeypatch) -> None:
    _ledger(monkeypatch, _row("far"), _row("near"), _row("rust"), _row("none", kept=0),
            _row("old", key="pruned"), _row("gone"),
            diffs={"far": ["a", "b"], "near": ["a"], "rust": ["crates/x.rs"], "none": [],
                   "old": []})  # fmt: skip
    assert land_rustcov.kin(REPO_ROOT) == "near"
    _ledger(monkeypatch, _row("rust"), diffs={"rust": ["crates/x.rs"]})
    assert land_rustcov.kin(REPO_ROOT) == ""


def test_a_tree_named_beside_the_record_is_a_source_of_the_pre_steps_run_alone(tmp_path) -> None:
    record, tree = str(tmp_path / "record.json"), "a" * 40
    keys = {"GOH_LAND_VERDICTS_RECORD": record}
    with land_rustcov.named(record, tree):
        assert _land_trees.sources(os.devnull, keys) == [tree]
        assert _land_trees.sources(str(tmp_path / "gate.json"), keys) == []  # the land's gate
    assert not list(tmp_path.iterdir()), "the name outlived its run"
    with land_rustcov.named("", tree), land_rustcov.named(record, ""):
        assert _land_trees.sources(os.devnull, keys) == []
    assert land_rustcov.KIN in av.SIDES  # a killed run's is swept with the rest


class _Run:
    """The pre-step's own `run`, its commands read and not started."""

    def __init__(self, mp: pytest.MonkeyPatch, record: Path, kin: str, binary: set[str]) -> None:
        self.argv: list[list[str]] = []
        self.kin: list[list[str]] = []
        self.sent: list[str] = []
        self.share = type("Share", (), {"mac_s": 5.0})()

        def ran(_label, argv) -> int:
            self.argv.append(argv)
            self.kin.append(_land_trees.sources(os.devnull, {av.RECORD: str(record)}))
            record.write_text(json.dumps({"identity": "i", "sha": "s", "passed": {}}))
            return 0

        def begin(_root, nodes, _mac, _judges):
            self.sent = list(nodes)
            return [], self.share if nodes else None

        mp.setattr(at, "timed", ran)
        mp.setattr(at.fast_tests, "collects", lambda *_a: 0)
        mp.setattr(at, "phases", lambda _r, c: (["j"], [n for n in c if n != "j"]))
        mp.setattr(at.land_rustcov, "kin", lambda _r: kin)
        mp.setattr(at.land_rustcov, "selected", lambda _r, ns: [n for n in ns if n in binary])
        mp.setattr(at.remote_lane, "begin", begin)
        mp.setattr(at.remote_lane, "end", lambda *_a: 0)
        mp.setattr(at.bin_reach_audit, "after", lambda *_a: 0)
        mp.setattr(at.pre_cpu, "counted", contextlib.nullcontext)
        mp.setenv(av.RECORD, str(record))
        mp.setenv("GATES_OF_HECK_SUITE_NESTED_OK", "1")
        mp.delenv(av.REUSE, raising=False)


CHOSEN = {"j": "judge", "bin": "runs goh", "plain": "changed"}


def test_the_measured_run_carries_and_names_the_tree_it_retries(tmp_path, monkeypatch) -> None:
    run = _Run(monkeypatch, tmp_path / "record.json", "a" * 40, {"bin"})
    assert at.run(REPO_ROOT, ["crates/x.rs"], CHOSEN, None, PLAN) == 0
    plain, measured = run.argv
    carry = f"{av.REUSE}={os.devnull}"
    assert carry in plain and carry in measured, measured
    assert run.kin == [[], ["a" * 40]], "only the measured run names the tree it carries from"
    ext = measured[measured.index("--external") + 1]
    assert ext.split()[2:] == ["bin", "plain"], "a retry sends the worker what it would carry"
    assert run.sent == []


def test_a_first_measured_run_shares_what_covers_no_rust_line(tmp_path, monkeypatch) -> None:
    run = _Run(monkeypatch, tmp_path / "record.json", "", {"bin"})
    assert at.run(REPO_ROOT, ["crates/x.rs"], CHOSEN, None, PLAN) == 0
    ext = run.argv[1][run.argv[1].index("--external") + 1]
    assert ext.split()[2:] == ["bin"] and run.sent == ["plain"], (ext, run.sent)
    assert run.share.mac_s == 0.0, "an instrumented run's wall is no pace of the plain Mac's"


# -- ... on a real instrumented binary -------------------------------------------------------------

RETRY_CONFTEST = f"""
import os, sys
sys.path[:0] = [{str(REPO_ROOT)!r} + "/" + d for d in ("tests", "tools", "lib")]
import _land_verdicts as lv
lv.identity = lambda root=None: "ID"
lv.here = lambda root=None: (os.environ["TREE"], "TOOLS")
lv.trees = lambda root, pre: []  # a rebased retry: the tree it was red on is no ancestor
lv.named = lambda root, shas: [(s, "T0") for s in shas]
lv.diff = lambda root, sha: ["test_red.py"]
lv.choose = lambda root, changed, *a, **k: (set(changed), "")

def pytest_configure(config):
    config.pluginmanager.register(lv, "goh-land-verdicts")
"""
RUN = "import subprocess\nrun = lambda: subprocess.run([{exe!r}, 'go'], check=True, stdin=-3)\n"
# The red node runs the binary, then fails; its fix runs none (the fewer file events, GOH.FSCHURN2).
RED = "def test_red():\n    {body}\n"


@pytest.fixture(scope="module")
def bins(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The instrumented binary, built once for the module's runs (read only by each)."""
    out = tmp_path_factory.mktemp("rustprog") / "bins"
    _build(out, PROG)
    return out


@pytest.mark.skipif(_llvm_bin() is None, reason="rustc's llvm-tools not installed")
@pytest.mark.parametrize("kin", ["a" * 40, ""])
def test_a_retry_after_one_red_node_runs_that_nodes_file_alone(tmp_path, bins, kin) -> None:
    d, store, exe = tmp_path / "s", tmp_path / "store", bins / "prog"
    d.mkdir()
    head = RUN.format(exe=str(exe))
    (d / "conftest.py").write_text(RETRY_CONFTEST)
    (d / "test_many.py").write_text(head + "def test_one():\n    run()\n")

    def session(tree: str, ok: bool, named: str) -> subprocess.CompletedProcess[str]:
        (d / "test_red.py").write_text(
            head + RED.format(body="pass" if ok else "run()\n    assert False")
        )
        (tmp_path / tree).mkdir()
        record = str(tmp_path / f"{tree}.json")
        env = hermetic_env(drop_git=True, GOH_REUSE_DIR=str(store), TREE=tree,
                           GOH_LOOPCTL=str(d / "no-loopctl"), GOH_COVERAGE_BIN_DIR=str(bins),
                           LLVM_PROFILE_FILE=str(tmp_path / tree / "goh-%p.profraw"),
                           GOH_LAND_VERDICTS_RECORD=record,
                           GOH_LAND_VERDICTS_REUSE=os.devnull)  # fmt: skip
        with land_rustcov.named(record, named):
            return subprocess.run(
                [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA"], cwd=d, env=env,
                capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
                timeout=patience(120),
            )  # fmt: skip

    first = session("T0", False, "")
    assert first.returncode == 1 and first.stdout.count("PASSED ") == 1, first.stdout
    profiles = list((store / "profstore").glob("*/*.profdata"))
    assert len(profiles) == 1, "what the red node ran before it failed was kept as its profile"
    second = session("T1", True, kin)
    assert second.returncode == 0, second.stdout + second.stderr
    ran = second.stdout.count("PASSED ")
    assert ran == (1 if kin else 2), second.stdout
    carried = list((tmp_path / "T1").glob("t-carried-*.profraw"))
    assert len(carried) == (1 if kin else 0), "a carried test's lines are not in the report"
    assert len(list((tmp_path / "T1").glob("t-*.profraw"))) == 1
