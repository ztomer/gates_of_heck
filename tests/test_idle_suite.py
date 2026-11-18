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

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, commit_all, git, write
from timing_bounds import GiveUp, assert_sooner

sys.path.insert(0, str(REPO_ROOT / "lib"))
import idle_suite


@pytest.fixture
def main(tmp_path: Path, monkeypatch) -> Path:
    """A repository whose release needs a full suite, outside any goh suite's markers."""
    for k in (*idle_suite.IN_SUITE, idle_suite.OFF_ENV):
        monkeypatch.delenv(k, raising=False)
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
        "os.environ['GOH_GATE_CLASS']]))"
    )
    return [sys.executable, "-c", code]


def test_an_idle_lander_runs_the_suite_on_mains_tip_in_its_own_worktree(main, tmp_path) -> None:
    q, out = queue(tmp_path), tmp_path / "ran"
    sha = git(main, "rev-parse", "HEAD").strip()
    assert idle_suite.run(q, main, argv=probe(out), poll=0.05) == "green"
    assert out.read_text().split() == [sha, "/dev/null", "work"]
    assert git(main, "status", "--porcelain").strip() == ""  # main's own checkout untouched
    assert json.loads((q / "idle-suite" / "last.json").read_text())["outcome"] == "green"
    assert idle_suite.run(q, main, argv=probe(out), poll=0.05) == "nothing"  # judged: not again
    write(main, "b.txt", "two\n")
    commit_all(main, "two")
    assert idle_suite.run(q, main, argv=probe(out), poll=0.05) == "green"  # main moved: owed
    assert out.read_text().split()[0] == git(main, "rev-parse", "HEAD").strip()


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
        (q / "running").mkdir(exist_ok=True)
        (q / "running" / "0001-T.LAND.json").write_text("{}")

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
    monkeypatch.setattr(landq, "audit_drain", lambda: None)
    assert landq.drain() == 0
    assert len(calls) == 2, calls  # yielded: the lander looked for the land, then idled again


def _load_landq():
    spec = importlib.util.spec_from_file_location("landq", REPO_ROOT / "tools" / "landq.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["landq"] = mod
    spec.loader.exec_module(mod)
    return mod
