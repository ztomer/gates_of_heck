"""A land's pre-land tests and its gate run AT ONCE on the tree that lands (GOH.GATEPARALLEL).

The lander ran them one after the other on one rebased tree that neither changes: pre-land 255 s,
then the gate 393 s (`goh land-profile`, 94 plain lands, 2026-11-21), so a land cost their sum.
Now both start together under the land lock, the first red stops the other and is the one named,
a step that hangs still dies at its own deadline, and nothing lands unless both are green (or the
pre-land had no verdict, which was never red) on the exact tree main fast-forwards to.

Concurrency is proven by a RENDEZVOUS, not a stopwatch: each side waits for the other's mark, so
a serial land deadlocks into the waiting side's red and a concurrent one passes on any load.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

from conftest import git, hermetic_env
from orphan_kit import wait_for_file
from test_affected_tests import _declare
from test_land import LAND, _main, _worktree
from timing_bounds import patience

WAIT_S = 6  # a side's wait for the other's mark


def _wait_for(mark: Path) -> str:
    """Shell: wait up to WAIT_S for MARK, else exit non-zero -- the serial land's red. orphan_kit's
    bounded wait, in a script beside the mark: its quotes cannot sit inside .gatesrc's own."""
    script = mark.with_suffix(".wait.sh")
    script.write_text(wait_for_file(mark, seconds=WAIT_S) + "\n")
    return f"bash {script}"


def _land(wt: Path, tmp_path: Path, pre: str, gate: str, **env: str):
    _declare(wt, f"GOH_LAND_GATE='{gate}'\nGOH_LAND_PRE='{pre}'\n")
    timing = tmp_path / "timing"
    timing.mkdir(exist_ok=True)
    e = hermetic_env(drop_git=True, GOH_TIMING_DIR=str(timing), **env)
    e.pop("GATES_OF_HECK_SUITE_RUNNER", None)
    return subprocess.run(["bash", str(LAND)], cwd=wt, env=e, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=patience(120), check=False)  # fmt: skip


def _sleeper() -> str:
    """A sleep whose argv is this test's alone, so `pgrep -f` finds only its survivors."""
    return f"sleep 30{uuid.uuid4().int % 1000:03d}.{uuid.uuid4().int % 997}"


def _alive(argv: str) -> bool:
    found = subprocess.run(["pgrep", "-f", argv], capture_output=True, stdin=subprocess.DEVNULL,
                           check=False)  # fmt: skip
    return found.returncode == 0


def test_the_pre_land_and_the_gate_run_at_once_and_land(repo: Path, tmp_path: Path) -> None:
    wt = _worktree(repo, tmp_path)
    p, g = tmp_path / "pre.mark", tmp_path / "gate.mark"
    r = _land(wt, tmp_path, f": > {p}; {_wait_for(g)}", f": > {g}; {_wait_for(p)}")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert _main(repo) == git(wt, "rev-parse", "HEAD").strip(), out
    row = json.loads((tmp_path / "timing" / "ledger.jsonl").read_text().splitlines()[-1])
    assert row["kind"] == "land" and row["exit"] == 0, row
    assert "pre_s" in row and "pre_run_s" in row, row  # the pre-land's seconds reach the land's row


def test_a_red_pre_land_stops_the_gate_and_is_the_one_named(repo: Path, tmp_path: Path) -> None:
    wt = _worktree(repo, tmp_path)
    started, hang = tmp_path / "gate.mark", _sleeper()
    before = _main(repo)
    r = _land(wt, tmp_path, f"{_wait_for(started)}; exit 1", f": > {started}; {hang}")
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "the pre-land tests failed" in out, out
    assert "the pre-land went red first" in out and "the gate was stopped" in out, out
    assert _main(repo) == before, out
    assert not _alive(hang), out  # the stopped gate left nothing running


def test_a_red_gate_stops_the_pre_land_and_is_the_one_named(repo: Path, tmp_path: Path) -> None:
    wt = _worktree(repo, tmp_path)
    started, hang = tmp_path / "pre.mark", _sleeper()
    before = _main(repo)
    r = _land(wt, tmp_path, f": > {started}; {hang}", f"{_wait_for(started)}; exit 1")
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "the gate failed" in out, out
    assert "the gate went red first" in out and "the pre-land was stopped" in out, out
    assert _main(repo) == before, out
    assert not _alive(hang), out


def test_a_pre_land_that_hangs_dies_at_its_deadline_and_the_gate_decides(
    repo: Path, tmp_path: Path
) -> None:
    wt = _worktree(repo, tmp_path)
    hang, seen = _sleeper(), tmp_path / "seen"
    r = _land(wt, tmp_path, hang, f"echo gate >> {seen}",
              GOH_LAND_PRE_MAX="2s", GOH_LAND_PRE_CEIL="2s")  # fmt: skip
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "pre-land: no verdict after " in out, out
    assert seen.read_text() == "gate\n", out
    assert _main(repo) == git(wt, "rev-parse", "HEAD").strip(), out
    assert not _alive(hang), out


def test_a_gate_that_hangs_dies_at_its_deadline_and_lands_nothing(
    repo: Path, tmp_path: Path
) -> None:
    wt = _worktree(repo, tmp_path)
    hang = _sleeper()
    before = _main(repo)
    r = _land(wt, tmp_path, "true", hang, GOH_LAND_GATE_MAX="3s")
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "deadline of 3s expired" in out and "the gate failed" in out, out
    assert "the gate went red -- nothing landed" in out, out  # the pre-land was green before it
    assert _main(repo) == before, out
    assert not _alive(hang), out
