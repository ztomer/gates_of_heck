"""A land check that is stopped leaves nothing behind (lib/check_jobs.py, GOH.CHECKVERDICT).

On 2026-11-25 `timeout` on `goh land --check` ended the check alone: its jobs (each in a session of
its own) ran on, and the commit gate's worktree stayed registered, in three lanes. A signal of any
kind, SIGKILL included, must leave no process and no worktree, and the run must be on record.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time

import pytest
from conftest import REPO_ROOT, git, hermetic_env
from orphan_kit import adopt
from test_landq import _land_wt, env  # noqa: F401 -- `env` is a fixture
from timing_bounds import GiveUp, patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))
import pid_owner
import timing_ledger

from lib import check_jobs, commit_key, commit_stamp, proc_deadline

# A job as the check runs one: a child of its own group, and a grandchild that left the group.
JOB = """\
sleep 300 &
"$GOH_PY" -c 'import os, time; os.fork() or (os.setsid(), time.sleep(300)); time.sleep(300)' &
wait
"""
DRIVER = """\
import sys
sys.path[:0] = [sys.argv[1], sys.argv[1] + "/lib"]
import os, pathlib, subprocess
from lib import check_jobs
top, tmp = sys.argv[2], pathlib.Path(sys.argv[3])
with check_jobs.guarded("", top, "f" * 40, signals=True):
    with check_jobs.worktree(top, tmp / "wt", tmp):
        subprocess.run(["git", "-C", top, "worktree", "add", "-q", "--detach", str(tmp / "wt")],
                       check=True)
        check_jobs.run(["bash", "-c", sys.argv[4]], pathlib.Path(top), dict(os.environ), 300)
"""


def below(pid: int) -> set[int]:
    return check_jobs._below({pid}, check_jobs._table()) - {pid}


def stamped(pid: int, least: int, proc: subprocess.Popen) -> list[pid_owner.Stamp]:
    """Every process below PID once LEAST of them are up, stamped."""
    give_up = GiveUp(30)
    while len(found := below(pid)) < least:
        assert not give_up.passed() and proc.poll() is None, (found, proc.poll())
        time.sleep(0.05)
    return [s for s in map(pid_owner.stamp, found) if s]


def none_left(stamps: list[pid_owner.Stamp]) -> None:
    give_up = GiveUp(20)
    while left := [s.number for s in stamps if not pid_owner.gone(s)]:
        assert not give_up.passed(), f"still running: {left}"
        time.sleep(0.05)


def ended(stamps: list[pid_owner.Stamp]) -> None:
    """A red run leaves nothing behind either."""
    for s in stamps:
        pid_owner.signal(s, signal.SIGKILL)


@pytest.mark.load_sensitive
@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGINT, signal.SIGKILL])
def test_a_stopped_check_leaves_no_process_and_no_worktree_and_is_recorded(repo, tmp_path, number):
    tmp, ledger = tmp_path / "gate", tmp_path / "timing"
    mine = hermetic_env(drop_git=True, GOH_TIMING_DIR=str(ledger), GOH_PY=sys.executable)
    proc = subprocess.Popen(
        [sys.executable, "-c", DRIVER, str(REPO_ROOT), str(repo), str(tmp), JOB],
        env=mine, stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )  # fmt: skip
    stamps: list[pid_owner.Stamp] = []
    try:
        # the watcher, bash, its sleep, the forker, and the two it forks into
        stamps = stamped(proc.pid, 5, proc)
        assert (tmp / "wt").is_dir(), "the worktree is there to be left behind"
        proc.send_signal(number)
        code = proc_deadline.wait(proc, patience(30))
        assert code == (-9 if number == signal.SIGKILL else 128 + number), code
        if number != signal.SIGKILL:  # what `timeout` returns to is already clean
            assert [s.number for s in stamps if not pid_owner.gone(s)] == []
        none_left(stamps)
    finally:
        proc.kill()
        proc.wait()
        ended(stamps)
        proc.stderr.close()
    assert not tmp.exists()
    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1
    (row,) = timing_ledger.spooled(ledger / "ledger.jsonl")
    assert (row["kind"], row["exit"], row["ok"]) == ("land check", 128 + number, False), row
    assert row["sha"] == "f" * 12 and row["wall_s"] >= 0, row


@pytest.mark.load_sensitive
def test_the_cli_check_itself_dies_clean(repo, tmp_path, env):  # noqa: F811
    """The door: `goh land --check` TERMed with a step running, through tools/landq.py."""
    wt, _ = _land_wt(repo, tmp_path)
    commit_stamp.record(commit_key.of(wt, "HEAD"))  # its gate: apart
    job = tmp_path / "job.sh"
    job.write_text(JOB)
    env = {**env, "GOH_LAND_CHECK_STEPS": f"bash {job}", "GOH_TIMING_DIR": str(tmp_path / "timing"),
           "GOH_PY": sys.executable}  # fmt: skip
    proc = subprocess.Popen([sys.executable, str(REPO_ROOT / "tools" / "landq.py"), "--check",
                             str(wt)], env=env, stdin=subprocess.DEVNULL, text=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip
    stamps: list[pid_owner.Stamp] = []
    try:
        give_up = GiveUp(60)
        while not (up := [p for p in below(proc.pid) if _command(p) == "sleep 300"]):
            assert not give_up.passed() and proc.poll() is None, proc.poll()
            time.sleep(0.1)
        stamps = stamped(proc.pid, 5, proc)
        proc.send_signal(signal.SIGTERM)
        assert proc_deadline.wait(proc, patience(30)) == 143, up
        assert [s.number for s in stamps if not pid_owner.gone(s)] == []
    finally:
        proc.kill()
        proc.wait()
        ended(stamps)
    listed = git(repo, "worktree", "list", "--porcelain")
    assert listed.count("worktree ") == 2 and "goh-commit-gate" not in listed, listed


def _command(pid: int) -> str:
    return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, check=False).stdout.strip()  # fmt: skip


@pytest.mark.load_sensitive
def test_a_job_past_its_deadline_takes_the_grandchild_that_left_its_group(tmp_path):
    seen: list[pid_owner.Stamp] = []
    real = check_jobs.sweep

    def spy(jobs, *a, **k):
        jobs = list(jobs)
        give_up = GiveUp(20)
        while len(found := below(jobs[0].number)) < 3 and not give_up.passed():
            time.sleep(0.05)
        seen.extend(s for s in map(pid_owner.stamp, found) if s)
        return real(jobs, *a, **k)

    mine = hermetic_env(GOH_PY=sys.executable)
    patch = pytest.MonkeyPatch()
    patch.setattr(check_jobs, "sweep", spy)
    try:
        rc, out, _ = check_jobs.run(["bash", "-c", JOB], tmp_path, mine, 1)
    finally:
        patch.undo()
    assert rc == 124 and "timed out after 1 s" in out, out
    assert len(seen) >= 3, seen
    try:
        none_left(seen)
    finally:
        ended(seen)


@pytest.mark.load_sensitive
def test_a_strangers_number_is_never_swept():
    """A job's pid handed to another process by the time of the sweep (GOH.STRAYSIGNAL)."""
    stranger = adopt(subprocess.Popen(["sleep", "30"], stdin=subprocess.DEVNULL,
                                        start_new_session=True))  # fmt: skip
    try:
        now = pid_owner.stamp(stranger.pid)
        assert now is not None
        old = pid_owner.Stamp(now.number, (now.born[0] - 100, now.born[1]), now.group)
        assert check_jobs.sweep([old], grace=0.1) == []
        assert stranger.poll() is None, "a stranger was signalled"
        assert check_jobs.sweep([now], grace=0.1, tick=stranger.poll) == []
        assert stranger.poll() is not None
    finally:
        stranger.kill()
        stranger.wait()


def test_every_checks_end_is_its_row_and_the_report_reads_them(tmp_path, monkeypatch):
    rows: list[dict] = []
    monkeypatch.setattr(timing_ledger, "append", lambda row: rows.append(row) or True)
    for door, rc in (("", 0), ("", 1), ("enqueue", None)):
        with check_jobs.guarded(door, "/x/gates_of_heck", "a" * 40) as g:
            g.rc = rc
    assert [(r["kind"], r["exit"], r["ok"]) for r in rows] == [
        ("land check", 0, True), ("land check", 1, False), ("land check:enqueue", 1, False),
    ]  # fmt: skip
    killed = {**rows[0], "exit": 137, "wall_s": 7.5}
    said = check_jobs.report([*rows, killed])
    assert said[0].startswith("goh land check: 3 runs") and "1 stopped (137)" in said[0], said
    assert "1 green" in said[0] and "1 refused" in said[0], said


def test_a_split_job_reads_stdout_alone_and_a_failure_says_its_stderr(tmp_path):
    both = "echo tests/test_a.py; echo noise >&2; exit "
    mine = hermetic_env()
    rc, out, _ = check_jobs.run(["bash", "-c", both + "0"], tmp_path, mine, 30, split=True)
    assert (rc, out) == (0, "tests/test_a.py\n"), out
    rc, out, _ = check_jobs.run(["bash", "-c", both + "3"], tmp_path, mine, 30, split=True)
    assert rc == 3 and out.splitlines() == ["tests/test_a.py", "noise"], out
