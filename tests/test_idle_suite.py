"""The idle lander runs the full suite on main's tip, and yields to any land (lib/idle_suite.py,
GOH.GATEBUDGET2).

Owner 2026-11-17: "the lander runs the full suite on main when idle", so a full record for main's
tip is on disk before a ship asks (the release kit refused every ship until a 12+ minute hand run).
The run must never delay a land: it holds no lock, and the first pending request stops it.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
import time
from pathlib import Path

import _goh_db_fixture as gdb
import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, commit_all, git, write
from timing_bounds import GiveUp, assert_sooner

sys.path.insert(0, str(REPO_ROOT / "lib"))
import idle_suite
import landq_state


@pytest.fixture
def main(tmp_path: Path, monkeypatch) -> Path:
    """A repository whose release needs a full suite, outside any goh suite's markers."""
    for k in (*idle_suite.IN_SUITE, idle_suite.OFF_ENV):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(
        "GOH_SHIP_DIR", str(tmp_path / "ship")
    )  # no ship in flight (GOH.IDLESUITERETIRE): a goh.db with no ship row
    gdb.made(monkeypatch, tmp_path)
    repo = tmp_path / "main"
    fast_init(repo, "main")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    write(repo, ".gatesrc", "GOH_SHIP_FULL_SUITE=on\n")
    commit_all(repo, "one")
    return repo


def queue(tmp_path: Path) -> Path:
    q = tmp_path / "landq"
    for d in ("pending", "logs"):
        (q / d).mkdir(parents=True, exist_ok=True)
    return q


def probe(out: Path) -> list[str]:
    """A stand-in suite: where it ran, at which commit, with which record and class."""
    code = (
        "import os, subprocess, sys; sha = subprocess.run(['git', 'rev-parse', 'HEAD'], "
        "capture_output=True, text=True).stdout.strip(); "
        f"open({str(out)!r}, 'w').write(' '.join([sha, os.environ['GOH_LAND_VERDICTS_RECORD'], "
        "os.environ['GOH_GATE_CLASS'], os.environ['GOH_LAND_VERDICTS_REUSE'], "
        "os.environ.get('GOH_CHANGE_BASE', '-')]))"
    )
    return [sys.executable, "-c", code]


def test_an_idle_lander_runs_the_suite_on_mains_tip_in_its_own_worktree(main, tmp_path) -> None:
    q, out = queue(tmp_path), tmp_path / "ran"
    sha = git(main, "rev-parse", "HEAD").strip()
    assert idle_suite.run(q, main, argv=probe(out), poll=0.05) == "green"
    assert out.read_text().split() == [sha, "/dev/null", "work", "@tree", "-"]  # the ship's own
    assert git(main, "status", "--porcelain").strip() == ""  # main's own checkout untouched
    assert json.loads((q / "idle-suite" / "last.json").read_text())["outcome"] == "green"
    assert idle_suite.run(q, main, argv=probe(out), poll=0.05) == "nothing"  # judged: not again
    write(main, "b.txt", "two\n")
    commit_all(main, "two")
    assert idle_suite.run(q, main, argv=probe(out), poll=0.05) == "green"  # main moved: owed
    assert out.read_text().split()[0] == git(main, "rev-parse", "HEAD").strip()


@pytest.fixture
def ships(main, tmp_path: Path) -> Path:
    """goh ship's result directory (the `main` fixture's), empty: no ship in flight."""
    return tmp_path / "ship"


def _ship_running(ships: Path, repo: Path) -> None:
    gdb.ship(ships / repo.name / "20261122T174900Z-ae63e2b2.json", {"status": "running"})


def test_the_idle_run_judges_as_the_ship_does_and_records_its_base(main, tmp_path, ships) -> None:
    """GOH.IDLESUITERETIRE: the ship of a tip adopts a green idle run of it, so the idle run makes
    the ship's own judgement: its diff coverage based at the last version tag."""
    q, out = queue(tmp_path), tmp_path / "ran"
    tagged = git(main, "rev-parse", "HEAD").strip()
    git(main, "tag", "v1.0.0")
    write(main, "b.txt", "two\n")
    commit_all(main, "two")
    sha = git(main, "rev-parse", "HEAD").strip()
    assert idle_suite.run(q, main, argv=probe(out), poll=0.05) == "green"
    assert out.read_text().split() == [sha, "/dev/null", "work", "@tree", tagged]
    last = json.loads((q / "idle-suite" / "last.json").read_text())
    assert (last["sha"], last["outcome"], last["base"]) == (sha, "green", tagged), last


def test_no_idle_run_starts_while_a_ship_of_the_repo_is_in_flight(main, tmp_path, ships) -> None:
    """v0.149.2 (2026-11-22): the idle run began 38 s before the ship's run of the same tree, both
    ran at once, and the ship's release land waited 388 s for the slot the idle run held."""
    q, out = queue(tmp_path), tmp_path / "ran"
    _ship_running(ships, main)
    assert idle_suite.run(q, main, argv=probe(out), poll=0.05) == "shipping"
    assert not out.exists()


@pytest.mark.load_sensitive  # a thread, a clock, a sleep: each wait bounded by patience
def test_a_ship_that_starts_stops_a_running_idle_suite(main, tmp_path, ships) -> None:
    q = queue(tmp_path)
    started = tmp_path / "started"
    slow = [sys.executable, "-c",
            f"import time; open({str(started)!r}, 'w').close(); time.sleep(600)"]  # fmt: skip
    over = threading.Event()

    def ship() -> None:
        give_up = GiveUp(60)
        while not started.exists() and not over.is_set() and not give_up.passed():
            time.sleep(0.05)
        _ship_running(ships, main)

    t = threading.Thread(target=ship)
    t.start()
    t0 = time.monotonic()
    said: list[str] = []
    try:
        assert idle_suite.run(q, main, say=said.append, argv=slow, poll=0.05) == "shipping"
    finally:
        over.set()
        t.join()
    assert_sooner(time.monotonic() - t0, 5, 600, "a ship stops the idle suite")
    assert any("stopped, a ship is in flight" in line for line in said), said


# A base-code fault only an unchosen test sees (GOH.CARRYBASECODE): the land gate runs base.py's
# readers (test_reader.py imports it, and passes); test_unchosen.py reads base.py only through the
# conftest fixture every test loads, so the gate never chose it; the idle full suite backstops it.
BASE = "def scale():\n    return {}\n"
CONFTEST = (
    "import pytest\nimport base\n\n\n@pytest.fixture\ndef factor():\n    return base.scale()\n"
)
READER = "import base\n\n\ndef test_reader():\n    assert base.scale() > 0\n"
UNCHOSEN = "def test_unchosen(factor):\n    assert factor == 2\n"
FAKE_LOOPCTL = "import sys\nopen(sys.argv[0] + '.filed', 'a').write(sys.argv[-1] + '\\n')\n"


def test_a_planted_base_fault_only_an_unchosen_test_sees_goes_red_and_files_its_item(
    main, tmp_path, monkeypatch
) -> None:
    q = queue(tmp_path)
    loopctl = tmp_path / "loopctl.py"
    loopctl.write_text(FAKE_LOOPCTL)
    (tmp_path / "queue.json").write_text(json.dumps(
        {"phases": [{"id": "SPD", "requires": ["metric", "predicted"]}]}))  # fmt: skip
    monkeypatch.setenv("GOH_LOOPCTL", str(loopctl))
    monkeypatch.setenv("GOH_LOOP_QUEUE", str(tmp_path / "queue.json"))
    for rel, text in (("base.py", BASE.format(2)), ("conftest.py", CONFTEST),
                      ("tests/test_reader.py", READER),
                      ("tests/test_unchosen.py", UNCHOSEN)):  # fmt: skip
        write(main, rel, text)
    commit_all(main, "a base module and its suite")
    green = git(main, "rev-parse", "HEAD").strip()
    suite = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist"]
    assert idle_suite.run(q, main, argv=suite, poll=0.05) == "green"
    assert json.loads((q / "idle-suite" / "last.json").read_text())["green"] == green
    write(main, "base.py", BASE.format(3))  # the planted fault
    commit_all(main, "feat: base scales faster (GOH.PLANTED)")
    write(main, "notes.txt", "x\n")
    commit_all(main, "docs: a note (GOH.OTHER)")
    said: list[str] = []
    assert idle_suite.run(q, main, say=said.append, argv=suite, poll=0.05) == "red"
    tip = git(main, "rev-parse", "HEAD").strip()
    assert f"idle full suite on {tip[:8]}: filed GOH.IDLERED{tip[:8].upper()}" in said, said
    item = json.loads(Path(f"{loopctl}.filed").read_text().splitlines()[-1])
    assert item["id"] == f"GOH.IDLERED{tip[:8].upper()}" and item["phase"] == "SPD", item
    text = item["item"]
    assert "tests/test_unchosen.py::test_unchosen" in text and "test_reader" not in text, text
    assert "(GOH.PLANTED)" in text and "(GOH.OTHER)" in text, text  # the lands since green
    assert f"since the last green full run at {green[:8]}" in text, text
    assert "a base module and its suite" not in text, text  # the green tip itself is not a suspect
    last = json.loads((q / "idle-suite" / "last.json").read_text())
    assert (last["outcome"], last["green"], last["red"]) == ("red", green, tip), last


@pytest.mark.load_sensitive  # a thread, a clock, a sleep: each wait bounded by patience
def test_a_pending_land_stops_the_suite_at_once(main, tmp_path) -> None:
    q = queue(tmp_path)
    started = tmp_path / "started"
    slow = [sys.executable, "-c",
            f"import time; open({str(started)!r}, 'w').close(); time.sleep(600)"]  # fmt: skip

    over = threading.Event()  # the run returned: nothing left to arrive for

    def arrive() -> None:
        give_up = GiveUp(60)
        while not started.exists() and not over.is_set() and not give_up.passed():
            time.sleep(0.05)
        # Where an enqueue's own lander has already moved it: running/, not pending/.
        landq_state.put(q, "0001-T.LAND", {}, "running")  # a goh.db row (GOH.LANDQDB)

    t = threading.Thread(target=arrive)
    t.start()
    t0 = time.monotonic()
    try:
        assert idle_suite.run(q, main, argv=slow, poll=0.05) == "yielded"
    finally:
        over.set()
        t.join()
    assert_sooner(time.monotonic() - t0, 5, 600, "a pending land stops the idle suite")
    assert json.loads((q / "idle-suite" / "last.json").read_text())["outcome"] == "yielded"


def test_nothing_runs_where_no_full_suite_is_declared_or_it_is_off(main, tmp_path, monkeypatch):
    q, out = queue(tmp_path), tmp_path / "ran"
    monkeypatch.setenv(idle_suite.OFF_ENV, "off")
    assert idle_suite.run(q, main, argv=probe(out)) == "off"
    monkeypatch.delenv(idle_suite.OFF_ENV)
    monkeypatch.setenv("GATES_OF_HECK_SUITE_REAL_HOME", "/x")  # a lander a test started
    assert idle_suite.run(q, main, argv=probe(out)) == "off"
    monkeypatch.delenv("GATES_OF_HECK_SUITE_REAL_HOME")
    write(main, ".gatesrc", "GOH_SHIP_FULL_SUITE=\n")
    commit_all(main, "no full suite")
    assert idle_suite.run(q, main, argv=probe(out)) == "nothing"
    assert not out.exists()


def test_the_lander_runs_it_when_idle_and_drains_again_after_it_yields(tmp_path, monkeypatch):
    landq = importlib.import_module("landq") if "landq" in sys.modules else _load_landq()
    monkeypatch.chdir(tmp_path)  # the lander moves to its state dir; this test's cwd comes back
    monkeypatch.setenv("GOH_LANDQ_DIR", str(tmp_path / "landq"))
    calls = []
    answers = iter(["yielded", "nothing"])

    def fake(q: Path, live: Path, say) -> str:
        calls.append(live)
        return next(answers)

    monkeypatch.setattr(landq.idle_suite, "run", fake)
    assert landq.drain() == 0
    assert len(calls) == 2, calls  # yielded: the lander looked for the land, then idled again


def _load_landq():
    spec = importlib.util.spec_from_file_location("landq", REPO_ROOT / "tools" / "landq.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["landq"] = mod
    spec.loader.exec_module(mod)
    return mod
