"""The gate's carry rule judges a base-code change by its readers, and is measured
(GOH.CARRYBASECODE, GOH.CARRYSHADOW).

The owner ruled on 2026-11-22 ("Approve CARRYBASECODE now"): a change to a file pytest loads before
every test is judged by its readers in the kept record's mode, not by the whole suite under the
lock. Pinned here: the rule's choice (tests/_carry_shadow.py `choose`), a fixture gate that carries
across a base-code change and records F against what ran, a red in an audit-shadowed source counted
outside F, the line tools/land.sh reads from `<record>.why` into the ledger, and
`goh land-profile --last N`'s three figures over a fixture queue and ledger.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

import _carry_shadow as cs
import _tree_graph
import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import land_profile
import land_records
import reuse

# -- the rule's choice -----------------------------------------------------------------------------


class Graph:
    built = 0
    extra: ClassVar[set[str]] = set()

    def __init__(self, root: Path) -> None:
        Graph.built += 1

    def base(self) -> set[str]:
        return {"tests/_schedule.py", "lib/lock_order.tsv", "tests/conftest.py",
                ".github/workflows/gate.yml", *Graph.extra}  # fmt: skip

    def load_reads(self) -> dict[str, str]:
        return {"lib/lock_order.tsv": "tests/_lock_order.py"}


class Table:
    Closure = Graph
    asked: ClassVar[list[tuple[list[str], bool, object]]] = []

    @staticmethod
    def affected(root: Path, changed: list[str], enqueue: bool, graph=None) -> dict[str, str]:
        Table.asked.append((changed, enqueue, graph))
        return {"tests/test_schedule.py": "imports _schedule"}


@pytest.mark.parametrize("mode", [cs.GATE, cs.ENQUEUE])
def test_a_base_code_change_is_judged_by_its_readers_in_the_records_mode(mode: str) -> None:
    Table.asked.clear()
    Graph.built = 0
    with cs.one_graph():
        got, why = cs.choose(REPO_ROOT, ["tests/_schedule.py"], Table, mode)
        assert cs.choose(REPO_ROOT, ["tests/_schedule.py"], Table, mode)[0] == got
    assert got == {"tests/test_schedule.py"}, why
    want = mode == cs.ENQUEUE
    assert [(c, e) for c, e, _ in Table.asked] == [(["tests/_schedule.py"], want)] * 2
    assert Graph.built == 1 and isinstance(Table.asked[0][2], Graph)  # one Closure, shared


@pytest.mark.parametrize("path", ["tests/conftest.py", "pyproject.toml", "lib/lock_order.tsv",
                                  ".github/workflows/gate.yml", "lib/unclassed.py"])  # fmt: skip
def test_what_pytest_reads_whole_as_it_starts_stays_wide(path: str) -> None:
    """Suite-wide and load-read stay wide, and so does a base file the table classes otherwise
    (the workflow) or not at all."""
    Graph.extra = {"lib/unclassed.py"}
    try:
        got, why = cs.choose(REPO_ROOT, [path, "tests/_schedule.py"], Table, cs.ENQUEUE)
    finally:
        Graph.extra = set()
    assert got is None and path in why, why


class RealTree:
    """tools/affected_tests.py on the run's one graph of this tree (tests/_tree_graph.py)."""

    @staticmethod
    def Closure(root: Path):
        assert root == REPO_ROOT
        return _tree_graph.real()

    @staticmethod
    def affected(root: Path, changed: list[str], enqueue: bool, graph=None) -> dict[str, str]:
        assert (root, graph) == (REPO_ROOT, _tree_graph.real())
        return _tree_graph.affected(changed, enqueue)


def test_on_the_real_tree_a_base_code_change_runs_its_readers_not_the_suite() -> None:
    """tests/_schedule.py (base code: it orders) chose nothing to carry before; now its readers."""
    got, why = cs.choose(REPO_ROOT, ["tests/_schedule.py"], RealTree, cs.ENQUEUE)
    assert got is not None, why
    files = {n.split("::")[0] for n in got}
    suite = list((REPO_ROOT / "tests").glob("test_*.py"))
    assert "tests/test_schedule.py" in files, sorted(files)
    assert len(files) < len(suite) // 2, (len(files), len(suite))
    table = cs._table(REPO_ROOT, None)  # what the plugin asks, with no table of its own
    assert table.__file__ == str(REPO_ROOT / "tools" / "affected_tests.py")


def test_the_mode_is_the_records_and_the_enqueues_when_it_names_none(tmp_path: Path) -> None:
    (tmp_path / "gate.json").write_text(json.dumps({"mode": "gate"}))
    (tmp_path / "old.json").write_text(json.dumps({"passed": {}}))
    assert cs.mode_of(str(tmp_path / "gate.json")) == cs.GATE
    assert cs.mode_of(str(tmp_path / "old.json")) == cs.ENQUEUE
    assert cs.mode_of(str(tmp_path / "none.json")) == cs.ENQUEUE


# -- a fixture gate: F carried across a base-code change, and the reds outside it ------------------

SESSION_CONFTEST = f"""
import dataclasses, os, sys
sys.path.insert(0, {str(REPO_ROOT / "tests")!r})
sys.path.insert(0, {str(REPO_ROOT / "lib")!r})
import _land_verdicts as lv
import reuse
lv.identity = lambda root=None: "ID"
lv.here = lambda root=None: ("T1", "TOOLS")
lv.trees = lambda root, pre: [("c0" * 20, "T0")]
lv.diff = lambda root, sha: ["tests/_schedule.py"]
lv.choose = lambda root, changed, table=None, mode=None: (set(os.environ["CHOSEN"].split()), mode)
reuse.PATHS["carry"] = dataclasses.replace(reuse.PATHS["carry"], every=int(os.environ["EVERY"]))

def pytest_configure(config):
    config.pluginmanager.register(lv, "goh-land-verdicts")
"""
TEST_S = "def test_pass():\n    pass\n\ndef test_broken():\n    assert False\n"
TEST_T = "def test_ok():\n    pass\n\ndef test_planted():\n    assert False\n"
T0 = dict.fromkeys(("test_s.py::test_pass", "test_s.py::test_broken", "test_t.py::test_ok",
                    "test_t.py::test_planted"), False)  # fmt: skip


@pytest.fixture
def gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "store"
    monkeypatch.setenv("GOH_REUSE_DIR", str(store))
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl"))
    d = tmp_path / "s"
    d.mkdir()
    (d / "conftest.py").write_text(SESSION_CONFTEST)
    (d / "test_s.py").write_text(TEST_S)
    (d / "test_t.py").write_text(TEST_T)
    reuse.record_suite(REPO_ROOT, "T0", "TOOLS", T0, set(), True, "gate")
    record = d / "kept.json"
    record.write_text(json.dumps({"identity": "OTHER", "passed": {}, "mode": "gate"}))

    def run(chosen: str, every: int, *args: str) -> tuple[str, str]:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", *args], cwd=d,
            capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
            timeout=patience(120),
            env=hermetic_env(drop_git=True, GOH_REUSE_DIR=str(store), CHOSEN=chosen,
                             EVERY=str(every), GOH_LOOPCTL=str(d / "no-loopctl"),
                             GOH_LAND_VERDICTS_REUSE=str(record)),
        )  # fmt: skip
        assert r.returncode == 1, r.stdout + r.stderr
        return Path(f"{record}.why").read_text(), r.stdout

    return run


@pytest.mark.parametrize("workers", [[], ["-n", "2"]], ids=["serial", "xdist"])
def test_the_gate_carries_across_a_base_code_change_and_runs_f(gate, workers) -> None:
    """The diff moved a base file; its readers are test_s.py, so test_t.py's passes carry and F is
    test_s.py's two: test_planted, broken only where nothing chose it, never runs."""
    why, out = gate("test_s.py", 1000, *workers)
    assert "2 deselected, carried from" in out, out
    assert why == "shadow: F 2 of 2 run, 0 red outside F (gate mode)\n", why


def test_a_red_in_an_audit_shadowed_source_is_counted_outside_f(gate) -> None:
    """One use in ten the audit runs the carried nodes anyway: test_planted, outside F, goes red
    there, and the line names it -- F missed it."""
    why, out = gate("test_s.py", 1)
    assert "RUN anyway" in out, out
    assert why == ("shadow: F 2 of 4 run, 1 red outside F (gate mode): "
                   "test_t.py::test_planted\n"), why  # fmt: skip


# -- tools/land.sh: the `.why` lines into the ledger row ------------------------------------------


def _land_shadow(tmp_path: Path, lines: str) -> list[str]:
    rec = tmp_path / "rec.json"
    rec.write_text("{}")
    Path(f"{rec}.why").write_text(lines)
    script = (
        f'. "{REPO_ROOT}/tui/lib.sh"; . "{REPO_ROOT}/lib/land_gate.sh"; verdicts="{rec}"; '
        "land_suite_why >/dev/null; land_shadow; "
        'printf "%s|%s|%s|%s|%s\\n" "$suite_why" "${shadow_f-}" "${shadow_ran-}" '
        '"${shadow_red_out-}" "${shadow_red-}"'
    )
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False,
                       stdin=subprocess.DEVNULL, timeout=patience(30),
                       env=hermetic_env(GOH_DIR=str(REPO_ROOT)))  # fmt: skip
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().split("|")


def test_land_sums_the_shadow_lines_apart_from_the_suites_decision(tmp_path: Path) -> None:
    got = _land_shadow(tmp_path, "missed: nothing carries\n"
                       "shadow: F 2 of 4 run, 1 red outside F (enqueue mode): t.py::a\n"
                       "shadow: F 3 of 9 run, 0 red outside F (enqueue mode)\n")  # fmt: skip
    assert got == ["missed: nothing carries", "5", "13", "1", "t.py::a"], got
    assert _land_shadow(tmp_path, "reused: the pre-step judged this tree\n")[1:] == [
        "0", "0", "0", ""]  # fmt: skip
    unmeasured = _land_shadow(tmp_path, "missed: x\nshadow: not measured (OSError: boom)\n")
    assert unmeasured == ["missed: x", "", "", "", ""], unmeasured  # no measurement, not a zero


# -- goh land-profile --last N --------------------------------------------------------------------

T = dt.datetime(2026, 11, 22, 6, 0, tzinfo=dt.UTC)


def _at(minutes: float) -> dt.datetime:
    return T + dt.timedelta(minutes=minutes)


def _put(q: Path, filed: float, item: str, state: str, started: float, finished: float) -> None:
    stem = f"{int(_at(filed).timestamp() * 1e9):020d}-{item}"
    rec = {"repo": "/p/gates_of_heck", "mode": "land", "state": state, "item": item,
           "started": _at(started).isoformat(), "finished": _at(finished).isoformat(),
           "log": str(q / "logs" / f"{stem}.log")}  # fmt: skip
    land_records.write(q, f"{stem}", rec)
    (q / "logs" / f"{stem}.log").write_text("✓ pre-land green in 60 s\n")


def _row(minute: float, **shadow: object) -> dict:
    return {"v": 1, "subject": "goh land", "kind": "land", "repo": "goh", "wall_s": 60.0,
            "epoch": _at(minute).timestamp(), "cpu_s": 100.0, "sha": f"{minute:012.0f}",
            **shadow}  # fmt: skip


def test_land_profile_prints_shadowed_lands_f_against_run_and_reds_outside_f(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """OUT: red once outside F, then landed. IN: red once inside F, then landed with the suite
    reused (0 of 0). NONE: no shadow recorded (no kept record). Each red attempt is a gate run."""
    q = tmp_path / "q"
    for d in ("logs",):
        (q / d).mkdir(parents=True)
    _put(q, 0, "GOH.OUT", "red", 1, 3)
    _put(q, 4, "GOH.OUT", "landed", 5, 10)
    _put(q, 20, "GOH.IN", "red", 21, 23)
    _put(q, 24, "GOH.IN", "landed", 25, 30)
    _put(q, 40, "GOH.NONE", "landed", 41, 45)
    red = "tests/test_x.py::test_planted"
    rows = [
        _row(2.5, shadow_f=40, shadow_ran=400, shadow_red_out=1, shadow_red=red),
        _row(9.5, shadow_f=40, shadow_ran=400, shadow_red_out=0),
        _row(22.5, shadow_f=12, shadow_ran=12, shadow_red_out=0),
        _row(29.5, shadow_f=0, shadow_ran=0, shadow_red_out=0),
        _row(44.5),
    ]
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path / "t"))
    monkeypatch.setenv("GOH_LANDQ_DIR", str(q))
    assert land_profile.main(["--last", "3"]) == 0
    out = capsys.readouterr().out
    want = ("carry shadow (GOH.CARRYBASECODE): 2 of 3 lands measured; F, the set the carry rule "
            f"runs, was 92 tests where 812 ran (4 gate runs); 1 audit-shadowed reds outside F: "
            f"{red}")  # fmt: skip
    assert want in out, out
    assert land_profile.main(["--last", "3", "--json"]) == 0
    spd = json.loads(capsys.readouterr().out)["spd"]["last 3"]
    assert (spd["shadowed"], spd["shadow_red_out"], spd["shadow_red"]) == (2, 1, [red]), spd
