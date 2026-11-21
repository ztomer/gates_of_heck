"""ONE case table for a saved pid's liveness, run against BOTH halves (GOH.PIDALIVESH).

lib/pid_owner.py `alive` and lib/pid_owner.sh `goh_pid_alive` answer the same question -- is the
process a stamp was taken of still there? -- and drifted: the shell compared `ps -o lstart=` alone,
and `ps` prints a ZOMBIE's start time, so the shell called a zombie alive while Python (53eee0ba,
GOH.BACKSTOPSTAMP) called it gone. So the cases live here once, and each runs against both:

  * live:    the stamped process itself, running                       -> alive
  * free:    the stamped process ended and was reaped, the number free  -> gone
  * zombie:  the stamped process ended, its parent has not reaped it    -> gone
  * exiting: the stamped process has begun to exit (`ps` state E)       -> alive (not gone yet)
  * reused:  a stand-in of the test's own holds the number, another birth -> gone

The exiting window cannot be held open on demand (lib/pid_owner.py's test measured it at ~20 ms
for a 3 GiB child), so it is FAKED, each half at its own kernel seam: Python's `birth` refuses
the pid (macOS `proc_pidinfo` does, for an exiting process), and the shell's `_goh_pid_ps` reports
state `?E`, with the start time (as macOS 27's `ps` prints it, measured 2026-11-21) and without.
Every process here is one the test started.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest
from conftest import REPO_ROOT
from timing_bounds import GiveUp, patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import pid_owner

SHELL_LIB = REPO_ROOT / "lib" / "pid_owner.sh"
NOT_ITS_BIRTH = "Thu Jan  1 00:00:01 1970"  # a start time no process of the test's has

CASES = [
    ("live", True),
    ("free", False),
    ("zombie", False),
    ("exiting", True),
    ("exiting-no-start", True),
    ("reused", False),
]


@dataclass
class Setup:
    """One case, as each half sees it: the stamp it holds, and its kernel fake (if any)."""

    py: pid_owner.Stamp
    sh: str
    pid: int
    py_fake: Callable[[pytest.MonkeyPatch], None] | None = None
    sh_fake: str = ""
    procs: list[subprocess.Popen] = field(default_factory=list)


def _sleeper() -> subprocess.Popen:
    return subprocess.Popen(["/bin/sleep", "60"], stdin=subprocess.DEVNULL)


def _stamped(proc: subprocess.Popen) -> tuple[pid_owner.Stamp, str]:
    py = pid_owner.stamp(proc.pid)
    sh = pid_owner.stamp_line(proc.pid)
    assert py is not None and sh is not None
    return py, sh


def _refuse_birth(pid: int) -> Callable[[pytest.MonkeyPatch], None]:
    def fake(monkeypatch: pytest.MonkeyPatch) -> None:
        real = pid_owner.birth

        def birth(p: int, platform: str | None = None):
            return None if p == pid else real(p, platform)

        monkeypatch.setattr(pid_owner, "birth", birth)

    return fake


def _zombie(proc: subprocess.Popen) -> None:
    give_up = GiveUp(10)
    while not give_up.passed():
        argv = ["ps", "-o", "stat=", "-p", str(proc.pid)]
        out = subprocess.run(argv, capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, check=False).stdout.strip()  # fmt: skip
        if out.startswith("Z"):
            return
        time.sleep(0.02)
    raise AssertionError(f"pid {proc.pid} never read as a zombie")


@contextmanager
def setup(case: str) -> Iterator[Setup]:
    first = _sleeper()
    procs = [first]
    try:
        py, sh = _stamped(first)
        made = Setup(py, sh, first.pid, procs=procs)
        if case == "free":
            first.kill()
            first.wait()
        elif case == "zombie":
            first.kill()
            _zombie(first)  # unreaped: the test's own child, a zombie until its wait below
        elif case.startswith("exiting"):
            start = sh.split(None, 1)[1] if case == "exiting" else ""
            made.py_fake = _refuse_birth(first.pid)
            made.sh_fake = f'_goh_pid_ps() {{ echo "?E   {start}"; }}\n'
        elif case == "reused":
            first.kill()
            first.wait()
            stand_in = _sleeper()
            procs.append(stand_in)
            made.pid = stand_in.pid
            made.py = pid_owner.Stamp(stand_in.pid, (1, 0), stand_in.pid)
            made.sh = f"{stand_in.pid} {NOT_ITS_BIRTH}"
        yield made
    finally:
        for proc in procs:
            proc.kill()
            proc.wait()


def _shell_alive(made: Setup) -> bool:
    script = (
        f'. "{SHELL_LIB}"\n{made.sh_fake}goh_pid_alive "{made.sh}" && echo alive || echo gone\n'
    )
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=patience(30), check=False)  # fmt: skip
    assert done.stdout.strip() in ("alive", "gone"), (done.returncode, done.stderr)
    return done.stdout.strip() == "alive"


@pytest.mark.load_sensitive(reason="spawns, kills and leaves zombie its own sleepers")
@pytest.mark.parametrize(("case", "alive"), CASES, ids=[c for c, _ in CASES])
def test_both_halves_answer_each_case_alike(
    case: str, alive: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    with setup(case) as made:
        assert _shell_alive(made) is alive, f"lib/pid_owner.sh goh_pid_alive, case {case}"
        if made.py_fake is not None:
            made.py_fake(monkeypatch)
        assert pid_owner.alive(made.py) is alive, f"lib/pid_owner.py alive, case {case}"
        assert pid_owner.gone(made.py) is not alive


@pytest.mark.load_sensitive(reason="spawns and kills its own sleeper")
def test_a_stamp_without_a_start_time_is_never_alive() -> None:
    """A bare pid (an older record) proves nothing about which process holds the number now."""
    proc = _sleeper()
    try:
        script = (
            f'. "{SHELL_LIB}"\ngoh_pid_alive "{proc.pid}" && exit 3\n'
            'goh_pid_alive "" && exit 4\ngoh_pid_alive "x y" && exit 5\necho fine\n'
        )
        done = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=patience(30),
                              check=False)  # fmt: skip
        assert done.stdout.strip() == "fine", (done.returncode, done.stderr)
    finally:
        proc.kill()
        proc.wait()
