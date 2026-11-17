"""tools/land.sh lands queue: one gate at a time, on the main it fast-forwards (GOH.LANDSERIAL).

With four lanes landing into one main, a ~14 min green gate was thrown away whenever main moved
under it ("main moved on since ... -- rebase onto it, then land again"): the hooks lane spent its
whole 3-attempt budget that way on 2026-11-14. So land.sh holds ONE lock per repository (a kernel
flock in the common git dir) from the rebase through the fast-forward: a second land waits, naming
who holds it, then rebases onto the main the first one landed and gates THAT, which is the commit
it will fast-forward to. GOH_LAND_GATE stands in for the gate; `LAND_LOG` records each gate's run.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git, hermetic_env
from timing_bounds import GiveUp, assert_sooner, patience

LAND = REPO_ROOT / "tools" / "land.sh"
# The fake gate: its start and end in $LAND_LOG, a second's work between them.
GATE = ('b=$(git rev-parse --abbrev-ref HEAD); echo "start $b" >>"$LAND_LOG"; sleep 1; '
        'echo "end $b" >>"$LAND_LOG"')  # fmt: skip


def _branch(repo: Path, tmp_path: Path, name: str, path: str = "") -> Path:
    """A worktree on branch NAME with one commit that writes PATH (default NAME.txt)."""
    wt = tmp_path / f"wt-{name}"
    git(repo, "worktree", "add", "-q", "-b", name, str(wt))
    (wt / (path or f"{name}.txt")).write_text(f"{name}\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "--no-verify", "-m", name)
    return wt


def _start(
    wt: Path, tmp_path: Path, gate: str = GATE, out=subprocess.PIPE
) -> subprocess.Popen[str]:
    env = hermetic_env(drop_git=True, GOH_LAND_GATE=gate, LAND_LOG=str(tmp_path / "gates.log"))
    return subprocess.Popen(["bash", str(LAND)], cwd=wt, env=env, stdout=out,
                            stderr=subprocess.STDOUT, text=True,
                            stdin=subprocess.DEVNULL)  # fmt: skip


def _until(what: str, done) -> None:
    """Wait on an observable state, never on a guess of how long a start takes under load."""
    give_up = GiveUp(60)
    while not done():
        assert not give_up.passed(), f"never saw: {what}"
        time.sleep(0.05)


def _land(wt: Path, tmp_path: Path, gate: str = GATE) -> tuple[int, str]:
    p = _start(wt, tmp_path, gate)
    out, _ = p.communicate(timeout=patience(60))
    return p.returncode, out


def _main_commits(repo: Path) -> list[str]:
    return git(repo, "log", "--format=%s", "main").split()


@pytest.mark.load_sensitive
def test_two_concurrent_lands_both_land_one_gate_at_a_time(repo: Path, tmp_path: Path) -> None:
    """The DONE state: two lands of disjoint branches started together both land, with no refused
    fast-forward; their gates never overlap; the waiter names the holder."""
    a, b = _branch(repo, tmp_path, "lane-a"), _branch(repo, tmp_path, "lane-b")
    # lane-a's gate holds the lock until lane-b is SEEN waiting on it: at load 70 a 0.3 s head start
    # let lane-a finish before lane-b reached the lock (the land of 44158daa, 2026-11-15).
    hold = tmp_path / "hold"
    os.mkfifo(hold)
    gate = GATE.replace("sleep 1;", f'[ "$b" != lane-a ] || read -r _ <"{hold}"; sleep 1;')
    log = tmp_path / "gates.log"
    pa = _start(a, tmp_path, gate)
    _until("lane-a's gate started", lambda: log.exists() and "start lane-a" in log.read_text())
    b_out = tmp_path / "lane-b.out"
    with b_out.open("w") as sink:
        pb = _start(b, tmp_path, gate, out=sink)
    _until("lane-b waiting on the lock", lambda: "waiting" in b_out.read_text())
    with hold.open("w") as release:
        release.write("go\n")
    out_a, _ = pa.communicate(timeout=patience(60))
    pb.wait(timeout=patience(60))
    out_b = b_out.read_text()
    assert pa.returncode == 0, out_a
    assert pb.returncode == 0, out_b
    assert {"lane-a", "lane-b"} <= set(_main_commits(repo)), _main_commits(repo)
    assert git(repo, "rev-parse", "main").strip() == git(b, "rev-parse", "HEAD").strip()
    ran = log.read_text().split("\n")
    assert ran[:4] == ["start lane-a", "end lane-a", "start lane-b", "end lane-b"], ran
    assert "waiting" in out_b and "lane-a" in out_b, out_b
    assert "rebased" in out_b, out_b


@pytest.mark.load_sensitive
def test_a_main_that_moved_before_the_land_is_rebased_onto_and_gated(
    repo: Path, tmp_path: Path
) -> None:
    """The gate judges the commit main will be fast-forwarded to: the branch rebased onto main."""
    wt = _branch(repo, tmp_path, "lane-a")
    (repo / "m.txt").write_text("main moved\n")
    git(repo, "add", "m.txt")
    git(repo, "commit", "-q", "--no-verify", "-m", "moved")
    gate = '[ -f m.txt ] && echo "gated with main" >>"$LAND_LOG"'
    rc, out = _land(wt, tmp_path, gate)
    assert rc == 0, out
    assert _main_commits(repo)[:2] == ["lane-a", "moved"], _main_commits(repo)
    assert (tmp_path / "gates.log").read_text() == "gated with main\n"


@pytest.mark.load_sensitive
def test_a_branch_that_conflicts_with_main_lands_nothing_and_is_left_as_it_was(
    repo: Path, tmp_path: Path
) -> None:
    wt = _branch(repo, tmp_path, "lane-a", "README.md")
    tip = git(wt, "rev-parse", "HEAD").strip()
    (repo / "README.md").write_text("main's own edit\n")
    git(repo, "commit", "-q", "--no-verify", "-am", "moved")
    before = git(repo, "rev-parse", "main").strip()
    rc, out = _land(wt, tmp_path, "true")
    assert rc == 1 and "conflicts" in out and "nothing landed" in out, out
    assert "in README.md --" in out, out  # the red line names what conflicted (GOH.LANDUNION)
    assert git(repo, "rev-parse", "main").strip() == before
    assert git(wt, "rev-parse", "HEAD").strip() == tip  # the rebase was aborted, not left half-done
    assert not git(wt, "status", "--porcelain").strip()


@pytest.mark.load_sensitive
def test_a_row_each_lane_added_to_a_registry_lands_both(repo: Path, tmp_path: Path) -> None:
    """GOH.LANDUNION: a registry .gitattributes merges by row (`merge=goh-rows`) rebases with
    land.sh's own driver, so the second of two lanes adding a row at one place lands, both rows in;
    the repo's config defines no driver (without one, the same rebase conflicts as text)."""
    rows = "| Key | Default |\n|---|---|\n| `GOH_A` | 1 |\n"
    (repo / ".gitattributes").write_text("keys.md merge=goh-rows\n")
    (repo / "keys.md").write_text(rows)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "registry")
    lanes = {}
    for name in ("lane-a", "lane-b"):
        lanes[name] = wt = _branch(repo, tmp_path, name)
        (wt / "keys.md").write_text(rows + f"| `GOH_{name[-1].upper()}{name[-1].upper()}` | 2 |\n")
        git(wt, "commit", "-q", "--no-verify", "-am", f"{name} row")
    for name, wt in lanes.items():
        rc, out = _land(wt, tmp_path, "true")
        assert rc == 0, (name, out)
    assert (repo / "keys.md").read_text() == rows + "| `GOH_BB` | 2 |\n| `GOH_AA` | 2 |\n"


@pytest.mark.load_sensitive
def test_a_process_the_gate_leaves_running_does_not_hold_the_lock(
    repo: Path, tmp_path: Path
) -> None:
    """The lock is the land's, not the gate's: a daemon the gate starts (a compile server) would
    inherit the lock's fd and hold every later land off for as long as it lives."""
    a, b = _branch(repo, tmp_path, "lane-a"), _branch(repo, tmp_path, "lane-b")
    marker = tmp_path / "daemon-pid"  # the daemon writes its pid, so the test kills exactly it
    code = "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(60)"
    daemon = f'python3 -c "{code}" {marker} >/dev/null 2>&1 &'
    try:
        rc, out = _land(a, tmp_path, daemon)
        assert rc == 0, out
        t0 = time.monotonic()
        rc, out = _land(b, tmp_path, "true")
        assert rc == 0, out
        assert marker.exists(), "the daemon must still be alive for this test to mean anything"
        assert_sooner(
            time.monotonic() - t0, 3, 60, f"the second land, behind the gate's daemon: {out}"
        )
    finally:
        with contextlib.suppress(OSError, ValueError):
            os.kill(int(marker.read_text()), signal.SIGKILL)
