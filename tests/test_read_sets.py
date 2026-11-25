"""The reads of a joined record are never fewer than its passes made (GOH.READUNION).

lib/read_sets.py, and each place that joins two runs' records through it: the carry (the whole chain
is tests/test_read_audit.py's), the tree's own record, the judges' merge, the enqueue's owed run,
the suite-once record and the Linux worker's share. Every one once kept one run's list for a file
another run had judged part of, or a file nobody watched as if somebody had. In memory.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import _read_audit as ra
import _shard_run
import pytest
from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "lib"))
import affected_verdict as av
import pre_owed
import pre_reuse
import read_sets as rs
import reuse
import reuse_audit

sys.path.insert(0, str(REPO_ROOT))
from lib import remote_gate as rg, remote_lane as rl

F, G = "tests/test_f.py", "tests/test_g.py"
ONE, TWO, GEE = f"{F}::one", f"{F}::two", f"{G}::gee"


def rec(nodes: list[str], reads: dict[str, list[str]], **more) -> dict:
    return {"identity": "I", "sha": "S", "passed": dict.fromkeys(nodes, False), "reads": reads,
            "reads_v": rs.VERSION, **more}  # fmt: skip


# -- the join ------------------------------------------------------------------------------------


def test_two_runs_of_one_file_join_their_reads_whichever_comes_first() -> None:
    a, b = ([ONE], {F: ["a"]}), ([TWO], {F: ["b"]})
    assert rs.joined([ONE, TWO], [a, b]) == rs.joined([ONE, TWO], [b, a]) == {F: ["a", "b"]}


def test_a_file_with_one_unwatched_pass_is_left_out_and_heals_when_it_is_watched() -> None:
    blind = ([TWO], {})
    assert rs.joined([ONE, TWO], [([ONE], {F: ["a"]}), blind]) == {}
    assert rs.joined([ONE, TWO], [([ONE], {F: ["a"]})]) == {}  # a pass no part answers for
    assert rs.joined([ONE, TWO], [([ONE, TWO], {F: ["a"]}), blind]) == {F: ["a"]}


def test_a_part_speaks_only_for_the_passes_it_made() -> None:
    parts = [([ONE], {F: ["a"], G: ["old"]}), ([GEE], {G: []})]
    assert rs.joined([ONE, GEE], parts) == {F: ["a"], G: []}


def test_a_record_of_an_older_join_is_believed_for_nothing_and_every_carry_of_it_is_refused():
    old = {"passed": {ONE: False}, "reads": {F: ["a"]}}
    lines: list[str] = []
    assert rs.of(old) == {} and rs.of(rec([ONE], {F: ["a"]})) == {F: ["a"]}
    assert ra.undeclared(lines, old, {ONE}, ["x"], "c" * 40) == {ONE} and "older goh" in lines[0]
    assert ra.undeclared([], {"passed": {ONE: False}}, {ONE}, ["x"], "c" * 40) == {ONE}
    assert ra.undeclared([], rec([ONE], {F: ["a"]}), {ONE}, ["x"], "c" * 40) == set()


# -- each place that joins ----------------------------------------------------------------------


@pytest.fixture
def store(monkeypatch) -> dict[str, dict]:
    """lib/reuse.py's records, in memory."""
    kept: dict[str, dict] = {}
    monkeypatch.setattr(reuse, "carry_fields", lambda repo, tree, tools: {"tree": tree})
    monkeypatch.setattr(reuse, "lookup", lambda path, f: (kept.get(f["tree"]), ""))
    monkeypatch.setattr(reuse, "record", lambda path, f, verdict, by, extra: kept.update(
        {f["tree"]: {"verdict": verdict, **extra}}) or "")  # fmt: skip
    monkeypatch.setattr(reuse, "prune", lambda path: 0)
    return kept


def _record(nodes: list[str], reads: dict[str, list[str]], failed: set[str] | None = None) -> None:
    reuse.record_suite(REPO_ROOT, "T", "TOOLS", dict.fromkeys(nodes, False), failed or set(), False,
                       "gate", reads=[(nodes, reads)])  # fmt: skip


def test_a_trees_record_joins_the_run_it_merges_into(store) -> None:
    _record([ONE], {F: ["a"]})
    _record([TWO], {F: ["b"]})
    assert store["T"]["reads"] == {F: ["a", "b"]} and store["T"]["reads_v"] == rs.VERSION
    _record([GEE], {})  # a run nobody watched
    assert store["T"]["reads"] == {F: ["a", "b"]}
    _record([GEE], {G: ["g"]}, failed={ONE, TWO})  # watched now; F's passes left
    assert store["T"]["reads"] == {G: ["g"]}


def test_a_trees_record_of_an_older_goh_stays_blind_until_one_run_watches_the_file(store) -> None:
    store["T"] = {"passed": {ONE: False, TWO: False}, "reads": {F: ["a"]}}
    _record([TWO], {F: ["b"]})
    assert store["T"]["reads"] == {}
    _record([ONE, TWO], {F: ["a", "b"]})
    assert store["T"]["reads"] == {F: ["a", "b"]}


def test_a_carry_keeps_each_source_for_its_own_nodes() -> None:
    plan = {"reads": [ra.of_record("/nonexistent", set())]}
    ra.keep(plan, rec([ONE, TWO], {F: ["a", "b"]}), {TWO})
    ra.keep(plan, {"passed": {GEE: False}, "reads": {G: ["old"]}}, {GEE})  # an older goh's
    got = rs.joined([ONE, TWO, GEE], [([ONE], {F: ["a2"]}), *plan["reads"]])
    assert got == {F: ["a", "a2", "b"]}, got


def _merged(tmp_path: Path, a: dict, b: dict) -> dict:
    (tmp_path / "a").write_text(json.dumps(a))
    (tmp_path / "b").write_text(json.dumps(b))
    av.merge_records(tmp_path / "b", tmp_path / "a")
    return json.loads((tmp_path / "b").read_text())


def test_the_judges_and_the_rests_records_join_a_file_they_split(tmp_path: Path) -> None:
    a = rec([ONE], {F: ["a"]}, remote=[GEE], remote_reads={G: ["g"]})
    got = _merged(tmp_path, a, rec([TWO], {F: ["b"]}))
    assert got["reads"] == {F: ["a", "b"]} and got["remote_reads"] == {G: ["g"]}, got
    del a["reads_v"]  # a record of an older goh, taken from the kept verdict of this tree
    assert _merged(tmp_path, a, rec([TWO], {F: ["b"]}))["reads"] == {}


def test_the_owed_runs_record_joins_a_file_the_pre_step_ran_part_of(tmp_path: Path) -> None:
    record, owed = tmp_path / "rec", tmp_path / "owed"
    record.write_text(json.dumps(rec([ONE], {F: ["a"]})))
    owed.write_text(json.dumps(rec([TWO], {F: ["b"]})))
    assert pre_owed.settle(record, owed, [TWO])
    assert json.loads(record.read_text())["reads"] == {F: ["a", "b"]}
    owed.write_text(json.dumps({**rec([GEE], {}), "passed": {GEE: False, f"{F}::x": False}}))
    assert pre_owed.settle(record, owed, [GEE])  # the owed run watched nothing: F is blind again
    assert json.loads(record.read_text())["reads"] == {}


def _suite_reads(monkeypatch, land: dict, main: dict, own: dict, carried: set[str]) -> dict:
    """The reads of the record the gate's suite-once reuse writes."""
    ran = {**land["passed"], **dict.fromkeys(land.get("remote", []), False)}
    union = {**dict.fromkeys(carried, False), **ran}
    gap = pre_owed.Gap([], [], land, main, own, "T", "M", "TOOLS", "ID", main["passed"],
                       carried | set(own.get("passed", ())), union)  # fmt: skip
    monkeypatch.setattr(pre_owed, "gap", lambda *a: (gap, ""))
    monkeypatch.setattr(reuse_audit, "use", lambda *a: "reuse")
    monkeypatch.setattr(reuse, "lookup", lambda path, f: (own or None, ""))
    got: dict = {}
    monkeypatch.setattr(reuse, "record", lambda path, f, verdict, by, extra: got.update(extra))
    for k in ("GOH_COVERAGE_BIN_DIR", "LLVM_PROFILE_FILE"):
        monkeypatch.delenv(k, raising=False)
    assert pre_reuse.suite(REPO_ROOT, Path("/nonexistent"), "b" * 40)[0]
    return got["reads"]


def test_the_suite_once_record_joins_the_pre_steps_part_of_a_file_and_mains(store, monkeypatch):
    main = rec([ONE, TWO], {F: ["a", "b"]})
    got = _suite_reads(monkeypatch, rec([ONE], {F: ["a2"]}), main, {}, {TWO})
    assert got == {F: ["a", "a2", "b"]}


def test_the_suite_once_record_never_gives_the_workers_pass_mains_old_reads(store, monkeypatch):
    main = rec([ONE, TWO, GEE], {F: ["a"], G: ["old"]})
    land = rec([ONE], {F: ["a"]}, remote=[GEE])
    assert _suite_reads(monkeypatch, land, main, {}, {TWO}) == {F: ["a"]}  # the worker said nothing
    land["remote_reads"] = {G: ["new"]}
    assert _suite_reads(monkeypatch, land, main, {}, {TWO}) == {F: ["a"], G: ["new"]}


def test_the_suite_once_record_is_blind_where_the_pre_steps_carry_was(store, monkeypatch) -> None:
    main, land = rec([ONE, TWO], {F: ["a"]}), rec([GEE], {G: []})
    own = {"passed": {TWO: False}, "reads": {F: ["a"]}}  # kept by an older goh
    assert _suite_reads(monkeypatch, land, main, own, {ONE}) == {G: []}


# -- the Linux worker's share -------------------------------------------------------------------


def _junit(cases: list[tuple[str, str, list[str] | None, str]], count: int | None) -> str:
    said = "" if count is None else (f'<properties><property name="{_shard_run.COLLECTED}" '
                                     f'value="{count}"/></properties>')  # fmt: skip
    body = "".join(
        f'<testcase classname="{c}" name="{n}">'
        + ("" if r is None else f"<properties><property name='{ra.READS_PROP}' "
           f"value='{json.dumps(r)}'/></properties>") + f"{fault}</testcase>"
        for c, n, r, fault in cases
    )  # fmt: skip
    return f"<testsuites><testsuite>{said}{body}</testsuite></testsuites>"


RED = '<failure message="boom"/>'


@pytest.fixture
def tree(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "tests").mkdir()
    for f in (F, G):
        (tmp_path / f).touch()
    monkeypatch.setattr(rl, "measure", lambda *a: None)
    monkeypatch.setattr(rg, "measure", lambda *a: None)
    return tmp_path


def _share(tree: Path, rc: int, junit: str, nodes: list[str]) -> rl.Share:
    (tree / "log").write_text("")
    (tree / "j.xml").write_text(junit)
    proc = SimpleNamespace(wait=lambda: rc, poll=lambda: rc, kill=lambda: None)
    return rl.Share(nodes, proc, tree / "j.xml", tree / "log")


def test_the_workers_passes_bring_what_they_read(tree: Path) -> None:
    record = tree / "rec"
    record.write_text(json.dumps(rec([], {})))
    said = [("tests.test_f", "one", ["a"], ""), ("tests.test_f", "two", ["b"], ""),
            ("tests.test_g", "gee", None, "")]  # fmt: skip
    share = _share(tree, 0, _junit(said, 3), [F, G])
    assert rl.end(tree, share, str(record), av.merge_records, lambda ns: 1) == 0
    got = json.loads(record.read_text())
    assert got["remote"] == [ONE, TWO, GEE]
    assert got["remote_reads"] == {F: [], ONE: ["a"], TWO: ["b"]}


ONE_RED = [("tests.test_f", "one", [], RED)]


@pytest.mark.parametrize(
    ("cases", "count", "nodes", "redo"),
    [
        (ONE_RED, 3, [ONE, TWO, GEE], [ONE, TWO, GEE]),  # its workers died after one test (N2b)
        (ONE_RED, 3, [F, G], [F, G]),
        (ONE_RED, None, [F], [F]),  # a red shard that does not say how many it had to run
        ([*ONE_RED, ("tests.test_f", "two", [], "")], 2, [F], [ONE]),  # whole: the failure alone
        ([*ONE_RED, ("tests.test_f", "two", [], "")], 2, [F, G], [G, ONE]),  # G: no case at all
        ([("", "tests.test_g", [], "<error/>"), *ONE_RED], 1, [ONE, G], [G, ONE]),  # G: no import
        ([("tests.test_f", "two[x]", [], RED)], 1, [TWO], [f"{F}::two[x]"]),
    ],
)
def test_a_red_share_leaves_no_test_unrun(tree: Path, cases, count, nodes, redo) -> None:
    ran: list[list[str]] = []
    share = _share(tree, 1, _junit(cases, count), nodes)
    assert rl.end(tree, share, "", av.merge_records, lambda ns: ran.append(ns) or 0) == 0
    assert ran == [redo], ran


def test_a_green_share_that_ran_less_than_it_collected_is_run_on_the_mac(tree: Path) -> None:
    ran: list[list[str]] = []
    share = _share(tree, 0, _junit([("tests.test_f", "one", [], "")], 2), [F])
    assert rl.end(tree, share, "", av.merge_records, lambda ns: ran.append(ns) or 0) == 0
    assert ran == [[F]], ran


@pytest.mark.parametrize("rc", [0, 1])
def test_the_gates_share_that_did_not_finish_runs_whole_on_the_mac(tree: Path, rc: int) -> None:
    ran: list[list[str]] = []
    share = _share(tree, rc, _junit(ONE_RED if rc else [("tests.test_f", "one", [], "")], 3), [F])
    green, lines = rg.finish(tree, share, False, {}, lambda fs: ran.append(fs) or 0)
    assert green and ran == [[F]] and "fallback: unfinished" in lines[0], lines


CRASH = """
import os, pytest

@pytest.mark.parametrize("i", range(8))
def test_dies(i):
    os._exit(1)
"""


def test_a_real_shard_whose_workers_die_says_what_it_had_to_run(tmp_path: Path) -> None:
    """The whole seam, a real xdist session under MARK: its workers die at once, pytest exits 1 with
    a few cases, and the junit says nine were collected, so the share is unfinished; its reads are
    in each case that ended. No test of it is a shard itself."""
    import subprocess

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_f.py").write_text(CRASH)
    (tmp_path / "tests/test_g.py").write_text(
        f"import os\ndef test_g():\n    open(__file__).close()\n"
        f"    assert {_shard_run.MARK!r} not in os.environ\n"
    )
    (tmp_path / "conftest.py").write_text(
        f"import sys\nsys.path.insert(0, {str(REPO_ROOT / 'tests')!r})\nimport _read_audit\n"
        "def pytest_configure(config):\n    config.pluginmanager.register(_read_audit, 'ra')\n"
    )

    def shard(*args: str) -> tuple[int, str]:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml=j.xml",
             *args], cwd=tmp_path, capture_output=True, text=True, stdin=subprocess.DEVNULL,
            check=False, env=hermetic_env(drop_git=True, **{_shard_run.MARK: "1"}),
        )  # fmt: skip
        return r.returncode, (tmp_path / "j.xml").read_text()

    rc, junit = shard("-n", "2", "--max-worker-restart=1", F, G)
    got = rl.kinds(tmp_path, junit)
    assert rc == 1 and len(got) < 9 and rl.unfinished(tmp_path, [F, G], junit, got, rc) == [F, G]
    rc, junit = shard("-n", "2", G)
    got = rl.kinds(tmp_path, junit)
    assert rc == 0 and got == {f"{G}::test_g": "passed"}, got
    assert rl.unfinished(tmp_path, [G], junit, got, rc) == []
    assert rl.reads(tmp_path, junit) == {G: [], f"{G}::test_g": [G]}
