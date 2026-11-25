"""An enqueue's green set outlives a stop in what follows it, and a kill leaves no side file.

GOH.PREPARTIAL (lib/affected_verdict.py `mark_green`, `stands`; lib/pre_deadline.py). The pre-step
runs the tests its land's gate would run (lib/pre_owed.py) and the release canary after its set; a
deadline in either threw the green set away (GOH.NITS3, 2026-11-23: 1500 s, stopped in the canary).
Nothing is started here: the child, the wait and the kill are the test's own.
"""

from __future__ import annotations

import json
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import affected_verdict as av
import land_prestep
import pre_deadline
import pre_owed
import proc_deadline

from lib import step_timings

NODE, BASE = "tests/test_a.py::test_a", "b" * 40
WHOLE = {"identity": "ID", "sha": "c" * 40, "passed": {NODE: False}, "base": BASE,
         "selection": "affected"}  # fmt: skip


class Stop:
    """A pre-step's run under its deadline; killed when it fires, or by a signal (`by`)."""

    def __init__(self, tmp_path: Path, mp: pytest.MonkeyPatch) -> None:
        self.record, self.mark = tmp_path / "record", tmp_path / "mark"
        self.on: dict[int, object] = {}
        self.killed: list[bool] = []  # ... with a side file there, at each kill
        mp.setenv(av.RECORD, str(self.record))
        mp.setenv(pre_deadline.ENQUEUE, "1")
        mp.setattr(pre_deadline.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=0))
        mp.setattr(pre_deadline.signal, "signal", self.on.__setitem__)
        mp.setattr(proc_deadline, "wait", lambda *a: self.status)
        mp.setattr(pre_deadline, "kill_tree", self._kill)
        mp.setattr(pre_deadline.pre_cpu, "at_deadline", lambda *a: None)
        mp.setattr(pre_deadline.os, "_exit", self._exit)
        self.status: int | None = None

    def _kill(self, child) -> None:
        self.killed.append(bool(self.sides()))

    def _exit(self, code: int) -> None:
        raise SystemExit(code)

    def sides(self) -> list[str]:
        return sorted(p.name for p in self.record.parent.glob("record.*"))

    def leave(self) -> None:
        for side in (".judges", ".owed", ".mac"):
            Path(f"{self.record}{side}").write_text("{}")

    def fire(self) -> int:
        return pre_deadline._run(["x"], 1, self.mark, 4)


@pytest.fixture
def stop(tmp_path, monkeypatch) -> Stop:
    return Stop(tmp_path, monkeypatch)


def test_the_sets_green_verdict_survives_a_deadline_in_what_follows_it(stop, capsys) -> None:
    stop.record.write_text(json.dumps(WHOLE))
    assert stop.fire() == pre_deadline.EXIT_DEADLINE  # the set itself was still running
    av.mark_green(str(stop.record))
    assert stop.fire() == 0 and len(stop.killed) == 2
    said = capsys.readouterr().out
    assert "the set's green verdict stands" in said
    assert land_prestep.no_verdict(said.splitlines()) is None  # the enqueue keeps a green one
    assert {k: v for k, v in json.loads(stop.record.read_text()).items() if k in WHOLE} == WHOLE


def test_a_lands_own_run_at_its_deadline_is_no_verdict(stop, monkeypatch) -> None:
    stop.record.write_text(json.dumps(WHOLE))
    av.mark_green(str(stop.record))
    monkeypatch.delenv(pre_deadline.ENQUEUE)  # the ledger would read its seconds as the set's cost
    assert stop.fire() == pre_deadline.EXIT_DEADLINE
    monkeypatch.setenv(pre_deadline.ENQUEUE, "1")
    monkeypatch.delenv(av.RECORD)
    assert stop.fire() == pre_deadline.EXIT_DEADLINE


@pytest.mark.parametrize("off", [
    {"selection": None}, {"base": None}, {"passed": None}, {"identity": None},
    {"identity": "MOVED"}, {"green": None}, {"identity": None, "green": None}])  # fmt: skip
def test_a_record_that_is_not_the_whole_sets_green_one_is_no_verdict(stop, off) -> None:
    stop.record.write_text(json.dumps({**WHOLE, "green": "ID", **off}))
    assert stop.fire() == pre_deadline.EXIT_DEADLINE, off


@pytest.mark.parametrize("rec", ["", "{", "[]", json.dumps({**WHOLE, "selection": "x"})])
def test_only_a_whole_sets_record_is_marked(tmp_path, rec) -> None:
    path = tmp_path / "record"
    path.write_text(rec)
    av.mark_green(str(path))
    assert path.read_text() == rec and not av.stands(str(path))
    for none in ("", str(tmp_path / "none")):
        av.mark_green(none)
        assert not av.stands(none) and not av.stands(None)


@pytest.mark.parametrize("rc", [0, 1])
def test_the_set_is_marked_before_anything_that_follows_it_and_never_when_red(
    monkeypatch, tmp_path, rc
) -> None:
    record, seen = tmp_path / "r", []
    record.write_text(json.dumps(WHOLE))
    monkeypatch.setenv(av.RECORD, str(record))
    monkeypatch.delenv("GATES_OF_HECK_SUITE_RUNNER", raising=False)
    monkeypatch.setattr(av, "judged", lambda *a: rc)
    monkeypatch.setattr(pre_owed, "run", lambda *a: seen.append(av.stands(str(record))) or 0)
    assert av.whole(REPO_ROOT, [NODE], BASE, True, lambda: 0) == rc
    assert seen == ([True] if rc == 0 else []) and av.stands(str(record)) is (rc == 0)


@pytest.mark.parametrize("rc", [-9, 137, 143, 75, 124, 1, 2])
def test_an_owed_run_that_was_stopped_is_no_red_and_settles_nothing(
    monkeypatch, tmp_path, rc
) -> None:
    record = tmp_path / "r"
    record.write_text(json.dumps({**WHOLE, "green": "ID"}))
    monkeypatch.setenv(pre_owed.RECORD, str(record))
    monkeypatch.setenv(pre_owed.ENQUEUE, "1")
    gap = SimpleNamespace(left=["tests/test_b.py::test_b"], why=[])
    monkeypatch.setattr(pre_owed, "gap", lambda *a: (gap, ""))
    monkeypatch.setattr(pre_owed, "collected", lambda root, nodes: set(nodes))
    monkeypatch.setattr(step_timings, "timed", lambda *a, **k: rc)
    assert pre_owed.run(REPO_ROOT, BASE) == (rc if rc in (1, 2) else 0)
    assert "settled" not in json.loads(record.read_text()) and av.stands(str(record))


def test_a_deadline_leaves_no_side_file_and_the_record(stop) -> None:
    stop.record.write_text("{}")
    stop.leave()
    assert stop.fire() == pre_deadline.EXIT_DEADLINE
    assert stop.killed == [True] and stop.sides() == [] and stop.record.is_file()


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_a_signal_leaves_no_side_file(stop, number) -> None:
    stop.status = 0
    assert stop.fire() == 0  # the run ends by itself: its own code owns what is beside its record
    stop.leave()
    with pytest.raises(SystemExit) as gone:
        stop.on[number](number, None)
    assert gone.value.code == 128 + number
    assert stop.killed == [True] and stop.sides() == []  # swept after the writers are dead
