"""The enqueue's pre-step runs what its land's gate would run for want of a verdict (GOH.PREUNDECL).

lib/pre_owed.py. The gate's suite takes the enqueue's verdict when every test main passed is judged
on this tree or carried from main's record; 27 lands in four days missed it, all for tests main
passed that nothing here answered -- a read no rule declares, the binary's build, a test deleted or
renamed -- and ran them under the lander's lock. Pinned here, each rule with what it must refuse:
the owed tests run and the gate then takes the verdict; a red one is the pre-step's red; nothing
refused is carried without a run; a test is gone only by a collection read whole; a run on a moved
tree settles nothing. In memory: no git, no store, no pytest (the tree is one directory of files).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import _land_verdicts as lv
import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import affected_verdict as av
import gate_identity
import pre_owed
import pre_reuse
import reuse
import reuse_audit

from lib import declared_reads, step_timings

A, B, BIN = "tests/test_a.py::test_a", "tests/test_b.py::test_b", "tests/test_bin.py::test_bin"
GONE = "tests/test_b.py::test_old[x]"
BASE, TIP = "b" * 40, "c" * 40


class Table:  # tools/affected_tests.py in small: `<x>.txt` chooses tests/test_<x>.py
    Closure = staticmethod(lambda root: SimpleNamespace(base=set))

    @staticmethod
    def affected(root, changed):
        return {f"tests/test_{p[:-4]}.py": "changed" for p in changed if p.endswith(".txt")}


class Land:
    """main passed MAINS (test_a read lib/a.txt); the lane changes b.txt, and its pre-step, chosen
    against main, passed test_b. `say` is what a run of the owed tests does."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root, self.record = tmp_path, tmp_path / "verdicts"
        (tmp_path / "tests").mkdir()
        for name in ("a", "b", "bin"):
            (tmp_path / "tests" / f"test_{name}.py").touch()
        self.here, self.changed = "ID", ["b.txt"]
        self.main = {"verdict": "green", "passed": {A: False, B: False},
                     "reads": {"tests/test_a.py": ["lib/a.txt"], "tests/test_b.py": [],
                               "tests/test_bin.py": []}}  # fmt: skip
        self.own: dict = {}
        self.rec = {"identity": "ID", "sha": TIP, "passed": {B: False}, "base": BASE,
                    "selection": "affected"}  # fmt: skip
        self.ran: list[list[str]] = []
        self.kept: list = []
        self.rc, self.passes, self.owed_id = 0, None, "ID"
        self.collects = {A, B, BIN}
        self.collect_said: tuple[int, str] | None = None
        git = {"merge-base": BASE}
        monkeypatch.setattr(pre_reuse, "_git", lambda root, *a: git.get(a[0]) or f"tree {a[-1]}")
        monkeypatch.setattr(pre_reuse, "_diff", lambda *a: self.changed)
        monkeypatch.setattr(pre_reuse, "_table", lambda root: Table)
        monkeypatch.setattr(gate_identity, "suite", lambda root: self.here)
        monkeypatch.setattr(gate_identity, "suite_tools", lambda: "TOOLS")
        monkeypatch.setattr(reuse, "lookup_suite", self._lookup)
        monkeypatch.setattr(reuse, "record_suite", lambda *a, **k: self.kept.append((a, k)))
        monkeypatch.setattr(reuse, "carry_fields", lambda *a: {})
        monkeypatch.setattr(reuse_audit, "use", lambda *a: "reuse")
        monkeypatch.setattr(step_timings, "timed", self._timed)
        monkeypatch.setattr(pre_owed, "subprocess", SimpleNamespace(run=self._collect, DEVNULL=-3))
        # the build's inputs are lib/binary_runners.py's to say, from a git tree this one is not
        monkeypatch.setattr(lv, "_builds", lambda changed, root: "Cargo.lock" in changed)
        monkeypatch.setenv(pre_owed.ENQUEUE, "1")
        monkeypatch.setenv(pre_owed.RECORD, str(self.record))
        for k in (pre_owed.REUSE, "GOH_COVERAGE_BIN_DIR", "LLVM_PROFILE_FILE"):
            monkeypatch.delenv(k, raising=False)

    def _lookup(self, root, tree, tools):
        return (self.main if tree.startswith(f"tree {BASE}") else self.own or None), "none"

    def _collect(self, argv, **kw):
        files = [a for a in argv if a.startswith("tests/")]
        ids = sorted(n for n in self.collects if n.split("::")[0] in files)
        assert pre_owed.RECORD not in kw["env"] and "--collect-only" in argv, argv
        rc, said = self.collect_said or (0, f"{len(ids)} tests collected in 0.1s")
        return SimpleNamespace(returncode=rc, stdout="\n".join([*ids, "", said]) + "\n")

    def _timed(self, label, argv, **kw):
        nodes = [a for a in argv if "::" in a]
        self.ran.append(nodes)
        words = dict(w.split("=", 1) for w in argv if w.startswith("GOH_"))
        assert words[pre_owed.REUSE] == os.devnull and words[pre_owed.RECORD] != str(self.record)
        got = dict.fromkeys(nodes if self.passes is None else self.passes, False)
        Path(words[pre_owed.RECORD]).write_text(json.dumps(
            {"identity": self.owed_id, "sha": TIP, "passed": got,
             "reads": {"tests/test_a.py": ["lib/a.txt"]}}))  # fmt: skip
        return self.rc

    def pre_step(self) -> int:
        self.record.write_text(json.dumps(self.rec))
        return pre_owed.run(self.root, BASE)

    def gate(self) -> tuple[bool, str]:
        return pre_reuse.suite(self.root, self.record, BASE)[:2]


@pytest.fixture
def land(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Land:
    return Land(tmp_path, monkeypatch)


def test_a_refused_carry_runs_in_the_pre_step_and_the_gate_takes_the_verdict(land: Land) -> None:
    land.changed = ["b.txt", "lib/a.txt"]  # test_a read it; the table does not choose test_a
    assert land.pre_step() == 0 and land.ran == [[A]], land.ran
    rec = json.loads(land.record.read_text())
    assert rec["passed"] == {A: False, B: False} and rec["settled"] == [A], rec
    assert rec["reads"] == {"tests/test_a.py": ["lib/a.txt"]} and rec["selection"] == "affected"
    ok, why = land.gate()
    assert ok, why
    (_root, _tree, _tools, union, *_), kw = land.kept[0]
    assert set(union) == {A, B} and kw["judged"] == {A, B}, (union, kw)  # judged here, not carried
    assert not Path(f"{land.record}.owed").exists()


def test_an_input_that_moved_is_never_carried_without_the_run(land, monkeypatch, capsys) -> None:
    """The planted fault: with the owed run taken away, the gate must refuse the verdict."""
    land.changed = ["b.txt", "lib/a.txt"]
    monkeypatch.delenv(pre_owed.ENQUEUE)  # a lane's own run, or a pre-land under the lock
    assert land.pre_step() == 0 and land.ran == []
    ok, why = land.gate()
    assert not ok and "neither judged here nor carried" in why and A in why, why
    monkeypatch.setenv(pre_owed.ENQUEUE, "1")
    land.rc = 1  # ... and a red one is the pre-step's red, with nothing settled by it
    assert land.pre_step() == 1
    assert "settled" not in json.loads(land.record.read_text()) and not land.gate()[0]
    assert "test_a.py read lib/a.txt" in capsys.readouterr().out  # the read to declare, named


def test_a_file_whose_reads_were_never_watched_runs(land: Land) -> None:
    del land.main["reads"]["tests/test_a.py"]
    assert land.pre_step() == 0 and land.ran == [[A]] and land.gate()[0]


def test_a_binary_test_main_passed_under_another_build_runs(land: Land) -> None:
    land.main["passed"][BIN] = True
    land.changed = ["b.txt", "Cargo.lock"]
    assert land.pre_step() == 0 and land.ran == [[BIN]], land.ran
    assert land.gate()[0]


def test_a_verdict_that_stands_already_collects_and_runs_nothing(land, monkeypatch) -> None:
    monkeypatch.setattr(pre_owed, "collected", lambda *a: pytest.fail("collected"))
    assert land.pre_step() == 0 and land.ran == [] and land.gate()[0]
    assert "settled" not in json.loads(land.record.read_text())


def test_a_test_gone_from_a_changed_file_is_settled_by_the_collection(land: Land) -> None:
    land.main["passed"][GONE] = False
    assert land.pre_step() == 0 and land.ran == [], land.ran
    ok, why = land.gate()
    assert ok, why
    assert GONE not in land.kept[0][0][3]  # answered, and no pass of this tree


@pytest.mark.parametrize("said", [
    (2, "1 test collected, 1 error in 0.1s"),  # red: a file that does not import
    (0, "3 tests collected in 0.1s"),  # an id the list does not show
    (0, "1/2 tests collected (1 deselected) in 0.1s"), (0, ""), (5, "Killed")])  # fmt: skip
def test_a_collection_not_read_whole_makes_no_test_gone(land: Land, said) -> None:
    land.main["passed"][GONE] = False
    land.collect_said = said
    assert land.pre_step() == 0 and land.ran == []
    ok, why = land.gate()
    assert not ok and GONE in why, why


def test_a_file_left_with_no_test_at_all_settles_its_tests(land: Land) -> None:
    land.main["passed"][GONE] = False
    land.collects, land.collect_said = set(), (5, "no tests collected in 0.1s")
    assert land.pre_step() == 0 and land.gate()[0]


def test_a_test_still_here_is_run_however_it_is_spelled_never_called_gone(land: Land) -> None:
    land.main["passed"][GONE] = False
    land.collects.add(GONE)
    assert land.pre_step() == 0 and land.ran == [[GONE]]


def test_a_skip_in_the_green_run_is_answered_and_is_no_pass(land: Land) -> None:
    land.changed, land.passes = ["b.txt", "lib/a.txt"], []
    assert land.pre_step() == 0 and land.gate()[0]
    assert A not in land.kept[0][0][3] and A not in land.kept[0][1]["judged"]


@pytest.mark.parametrize("moved", ["ANOTHER", None])
def test_a_run_on_a_tree_that_moved_settles_nothing(land: Land, moved) -> None:
    land.changed, land.owed_id = ["b.txt", "lib/a.txt"], moved
    assert land.pre_step() == 0 and land.ran == [[A]]
    assert json.loads(land.record.read_text()) == land.rec and not land.gate()[0]


def test_settled_is_read_on_the_records_own_tree_alone(land: Land) -> None:
    land.rec["settled"], land.changed = [A], ["b.txt", "lib/a.txt"]
    land.record.write_text(json.dumps(land.rec))
    assert land.gate()[0]
    land.here = "ANOTHER"
    ok, why = land.gate()
    assert not ok and "another tree" in why, why


def test_past_the_most_the_gate_runs_them(land: Land, monkeypatch) -> None:
    land.changed = ["b.txt", "lib/a.txt"]
    monkeypatch.setattr(pre_owed, "MOST", 0)
    assert land.pre_step() == 0 and land.ran == [] and not land.gate()[0]


def test_the_land_keys_outlive_the_plugins_import(land: Land, monkeypatch) -> None:
    """tests/_land_verdicts.py takes the land's keys out of the environment as it loads (`_take`),
    and the pre-step's process loads it here: its own record's clean-up then had no key."""

    def loads(*_a):
        os.environ.pop(pre_owed.RECORD)
        return None, "why"

    monkeypatch.setattr(pre_owed, "gap", loads)
    assert land.pre_step() == 0 and os.environ[pre_owed.RECORD] == str(land.record)


@pytest.mark.parametrize("rc", [0, 1])
def test_the_pre_steps_whole_set_owes_only_after_it_is_green(monkeypatch, rc) -> None:
    asked: list = []
    monkeypatch.setenv(av.RECORD, "/x/rec")
    monkeypatch.delenv("GATES_OF_HECK_SUITE_RUNNER", raising=False)
    monkeypatch.setattr(av, "judged", lambda *a: rc)
    monkeypatch.setattr(pre_owed, "run", lambda *a: asked.append(a) or 7)
    assert av.whole(REPO_ROOT, [A], BASE, True, lambda: 0) == (7 if rc == 0 else 1)
    assert asked == ([(REPO_ROOT, BASE)] if rc == 0 else [])
    assert av.whole(REPO_ROOT, [A], BASE, False, lambda: 3) == 3  # a part of the set: never


TREE_READS = (  # the files READS names, joined below
    "tests/test_fakes_cannot_orphan.py", "tests/test_git_init_ratchet.py",
    "tests/test_goh_entry.py", "tests/test_load_sensitive.py", "tests/test_ps_table_unbounded.py",
)  # fmt: skip
READS = {
    "test_fakes_cannot_orphan": "tests/_new.py", "test_git_init_ratchet": "tests/_new.py",
    "test_goh_entry": "tests/test_new.py", "test_load_sensitive": "tests/_new.py",
    "test_ps_table_unbounded": "tests/_new.py",
}  # fmt: skip


@pytest.mark.parametrize("name", READS)
def test_the_reads_the_replayed_lands_refused_whole_files_for_are_declared(name: str) -> None:
    """A file the selector chose no test of, refused land after land for a read it really makes."""
    text = (REPO_ROOT / "tests" / f"{name}.py").read_text(encoding="utf-8")
    got = declared_reads.reads(text, REPO_ROOT)
    assert any(declared_reads.matches(p, READS[name]) for p in got), got
