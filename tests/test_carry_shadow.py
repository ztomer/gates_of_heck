"""The proposed carry rule is measured beside the gate's, never obeyed (GOH.CARRYSHADOW).

The owner rules on GOH.CARRYBASECODE (a base-code change judged by its readers, not the whole suite
under the lock) with one number: how often the gate went red on a test the proposed rule would have
carried a pass over. Pinned here: the proposed rule's choice (tests/_carry_shadow.py `proposed`), a
fixture gate whose red lies outside F is counted and one whose red lies inside F is not, the line
tools/land.sh reads from `<record>.why` into the ledger, and `goh land-profile --last N`'s three
figures over a fixture queue and ledger.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

import _carry_shadow as cs
import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import land_profile
import reuse

# -- the proposed rule's choice ------------------------------------------------------------------


class Graph:
    built = 0

    def __init__(self, root: Path) -> None:
        Graph.built += 1

    def base(self) -> set[str]:
        return {"tests/_schedule.py", "lib/lock_order.tsv", "tests/conftest.py"}

    def load_reads(self) -> dict[str, str]:
        return {"lib/lock_order.tsv": "tests/_lock_order.py"}


class Table:
    Closure = Graph
    asked: ClassVar[list[tuple[list[str], bool, object]]] = []

    @staticmethod
    def affected(root: Path, changed: list[str], enqueue: bool, graph=None) -> dict[str, str]:
        Table.asked.append((changed, enqueue, graph))
        return {"tests/test_schedule.py": "imports _schedule"}


def test_a_base_code_change_is_judged_by_its_readers_in_the_records_mode() -> None:
    Table.asked.clear()
    Graph.built = 0
    with cs.one_graph():
        assert cs.choose(REPO_ROOT, ["tests/_schedule.py"], Table)[0] is None  # the gate's rule
        got, _ = cs.proposed(REPO_ROOT, ["tests/_schedule.py"], cs.GATE, Table)
    assert got == {"tests/test_schedule.py"}
    assert [(c, e) for c, e, _ in Table.asked] == [(["tests/_schedule.py"], False)]
    assert Graph.built == 1 and isinstance(Table.asked[0][2], Graph)  # one Closure, shared


@pytest.mark.parametrize("path", ["tests/conftest.py", "pyproject.toml", "lib/lock_order.tsv"])
def test_what_pytest_reads_whole_as_it_starts_stays_wide(path: str) -> None:
    got, why = cs.proposed(REPO_ROOT, [path, "lib/reuse.py"], cs.ENQUEUE, Table)
    assert got is None and path in why


def test_the_mode_is_the_records_and_the_enqueues_when_it_names_none(tmp_path: Path) -> None:
    (tmp_path / "gate.json").write_text(json.dumps({"mode": "gate"}))
    (tmp_path / "old.json").write_text(json.dumps({"passed": {}}))
    assert cs.mode_of(str(tmp_path / "gate.json")) == cs.GATE
    assert cs.mode_of(str(tmp_path / "old.json")) == cs.ENQUEUE
    assert cs.mode_of(str(tmp_path / "none.json")) == cs.ENQUEUE


# -- a fixture gate: what the proposed rule would run, and the reds outside it ---------------------

SESSION_CONFTEST = f"""
import os, sys
sys.path.insert(0, {str(REPO_ROOT / "tests")!r})
sys.path.insert(0, {str(REPO_ROOT / "lib")!r})
import _carry_shadow
import _land_verdicts as lv
lv.identity = lambda root=None: "ID"
lv.here = lambda root=None: ("T1", "TOOLS")
lv.trees = lambda root, pre: [("c0" * 20, "T0")]
lv.diff = lambda root, sha: ["tests/_schedule.py"]
lv.choose = lambda root, changed: (None, "tests/_schedule.py is loaded before every test")
_carry_shadow.proposed = lambda root, changed, mode: (set(os.environ["PROPOSED"].split()), mode)

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

    def run(proposed: str, *args: str) -> str:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", *args], cwd=d,
            capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
            timeout=patience(120),
            env=hermetic_env(drop_git=True, GOH_REUSE_DIR=str(store), PROPOSED=proposed,
                             GOH_LOOPCTL=str(d / "no-loopctl"),
                             GOH_LAND_VERDICTS_REUSE=str(record)),
        )  # fmt: skip
        assert r.returncode == 1 and "nothing carried from" in r.stdout, r.stdout + r.stderr
        return Path(f"{record}.why").read_text()

    return run


@pytest.mark.parametrize("workers", [[], ["-n", "2"]], ids=["serial", "xdist"])
def test_a_gate_red_the_proposed_rule_would_have_carried_is_counted(gate, workers) -> None:
    """The gate's rule carried nothing (a base file moved) and ran all four; the proposed rule
    chooses only test_s.py, so it would have carried test_t.py's pass: test_planted is red outside
    F, test_broken (chosen) red inside it."""
    why = gate("test_s.py", *workers)
    assert why == ("shadow: F 2 of 4 run, 1 red outside F (gate mode): "
                   "test_t.py::test_planted\n"), why  # fmt: skip


def test_a_gate_red_inside_f_is_not_counted(gate) -> None:
    why = gate("test_s.py test_t.py")
    assert why == "shadow: F 4 of 4 run, 0 red outside F (gate mode)\n", why


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
    (q / "done" / f"{stem}.json").write_text(json.dumps(rec))
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
    for d in ("done", "logs"):
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
    want = ("carry shadow (GOH.CARRYBASECODE): 2 of 3 lands shadowed; the proposed rule would run "
            f"92 tests where 812 ran (4 gate runs); 1 gate reds outside F: {red}")  # fmt: skip
    assert want in out, out
    assert land_profile.main(["--last", "3", "--json"]) == 0
    spd = json.loads(capsys.readouterr().out)["spd"]["last 3"]
    assert (spd["shadowed"], spd["shadow_red_out"], spd["shadow_red"]) == (2, 1, [red]), spd
