"""The third shape: an orphaned process BURNING a core on a busy loop, whoever wrote it.

THE CLASS (2026-10-21). The backstop knew TWO shapes: the registry, and the house's own spinner
command line, matched whole. A hand-rolled spinner is a different command line, so the backstop
left all twenty-four of them alone: `/bin/zsh /private/var/.../opencode/load.sh`, four cores
each, 78 minutes of CPU apiece, four and a half hours old, parent 1, their owner long dead. The
script was a file:

    for i in $(seq 1 24); do (while :; do :; done) & done

So the backstop asks a shape it can NAME without owning it, and asks four questions before it
touches anything: the parent is 1 (nobody will ever reap it), it has BURNED (its CPU time is
over the floor), it is STILL burning (the CPU time grew between two samples), and the code a
shell runs contains a busy loop.

The negative controls are RESHAPED so they do not collide with the broader rule -- a control that
itself spins is now a process the rule would catch: a live parent, a near miss that SLEEPS
instead of spinning, a command line that only MENTIONS the shape, and a process that has not
burned yet. And every fixture is BOUNDED: a control that spins forever is a leak waiting for the
next killed run, so each one stops on its own clock.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import load_kit
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))

import load_reap
import load_registry as reg

#
# Every fixture here is BOUNDED: a control that spins forever is a leak waiting for the next
# killed run, and the point of these tests is that the suite can be SIGKILLed without leaving a
# core burning behind it. The spinner stops on its own clock.

# A bounded spinner: it burns a core, and its own timer stops it. The bound is what makes the
# suite safe to SIGKILL.
BOUNDED_SPINNER = """#!/bin/sh
(sleep 6; kill -TERM $$) &
while :; do :; done
"""

# A near miss: a loop that does work, so it is not a spinner at all.
SLEEPER = """#!/bin/sh
while true; do sleep 1; done
"""

# A prefix of the shape: the loop is MENTIONED, not run, and the work it does is real.
MENTION = """#!/bin/sh
echo 'while :; do :; done'
i=0
while [ $i -lt 400000 ]; do i=$((i + 1)); done
"""

# The CPU the fixtures are given, in seconds. The house's floor is a minute (load_reap.
# BURN_CPU_S); a test cannot burn a minute of CPU, so the floor is a parameter and the test
# asks the same four questions with a floor a fixture can reach.
FLOOR_S = 0.3


def _script(tmp_path: Path, body: str, name: str = "spin.sh") -> Path:
    """A script the fixture runs, and the path it is at."""
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _cpu_of(pid: int) -> float:
    """The CPU a pid has burned, in seconds."""
    out = subprocess.run(
        ["ps", "-o", "cputime=", "-p", str(pid)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )  # fmt: skip
    return load_reap._cpu_seconds(out.stdout)


def _burning(pid: int, floor: float = FLOOR_S) -> bool:
    """Has this pid burned enough CPU to be a spinner rather than a start-up?"""
    return _cpu_of(pid) >= floor


def test_the_backstop_kills_an_orphaned_process_burning_a_core_on_a_busy_loop(
    tmp_path: Path,
) -> None:
    """THE CLASS (2026-10-21). Twenty-four hand-rolled spinners, four cores each, four and a
    half hours old, and a backstop that knew only the house's own command line: it left every one
    of them alone. This one is recognised by its shape -- an orphan, burning, on a busy loop."""
    script = _script(tmp_path, BOUNDED_SPINNER)
    pid = load_kit.orphan(["/bin/sh", str(script)])
    assert reg.pid_ppid(pid) == 1, "the fixture did not become an orphan"
    assert load_kit.wait_until(lambda: _burning(pid)), "the fixture never burned a core"
    report = load_reap.reap_burning_orphans(min_cpu_s=FLOOR_S)
    assert pid in report.killed, report
    assert not load_kit.alive(pid), "the spinner outlived the backstop"


def test_the_backstop_leaves_a_spinner_with_a_live_parent_alone(tmp_path: Path) -> None:
    """NEGATIVE CONTROL. A live parent: the owner is here, and it will reap it. The shape, the
    burn and the code are all the leak's -- only the parent saves it."""
    script = _script(tmp_path, BOUNDED_SPINNER)
    with load_kit.owning(["/bin/sh", str(script)]) as child:
        pid = child.proc.pid
        assert load_kit.wait_until(lambda: _burning(pid)), "the fixture never burned a core"
        report = load_reap.reap_burning_orphans(min_cpu_s=FLOOR_S)
        assert pid not in report.killed, report
        assert load_kit.alive(pid), "the backstop killed a spinner whose parent was alive"


def test_the_backstop_leaves_a_near_miss_that_sleeps_alone(tmp_path: Path) -> None:
    """NEGATIVE CONTROL. A loop that SLEEPS instead of spinning: it costs no CPU, and it is not
    the shape. The control must not itself spin, or it would be a process the rule would catch."""
    script = _script(tmp_path, SLEEPER)
    pid = load_kit.orphan(["/bin/sh", str(script)])
    report = load_reap.reap_burning_orphans(min_cpu_s=FLOOR_S)
    assert pid not in report.killed, report
    assert load_kit.alive(pid), "the backstop killed a sleeper"


def test_the_backstop_leaves_a_command_line_that_only_looks_like_the_shape_alone(
    tmp_path: Path,
) -> None:
    """NEGATIVE CONTROL. A PREFIX: the code MENTIONS the loop and does real work instead. Text is
    not code -- `echo 'while :; do :; done'` writes a loop, it does not run one."""
    script = _script(tmp_path, MENTION)
    pid = load_kit.orphan(["/bin/sh", str(script)])
    assert load_kit.wait_until(lambda: _burning(pid)), "the fixture never burned a core"
    report = load_reap.reap_burning_orphans(min_cpu_s=FLOOR_S)
    assert pid not in report.killed, report
    assert load_kit.alive(pid), "the backstop killed a process that only mentions the shape"


def test_the_backstop_leaves_a_spinner_that_has_not_burned_alone(tmp_path: Path) -> None:
    """NEGATIVE CONTROL. A process that has just started has no CPU behind it: the floor is what
    separates a spinner from a start-up, and without it the backstop would kill every shell that
    happened to be orphaned."""
    script = _script(tmp_path, "sleep 30\n")
    pid = load_kit.orphan(["/bin/sh", str(script)])
    report = load_reap.reap_burning_orphans(min_cpu_s=FLOOR_S)
    assert pid not in report.killed, report
    assert load_kit.alive(pid), "the backstop killed a process that had burned nothing"


def test_a_killed_test_run_leaves_no_process_of_its_own_behind(tmp_path: Path) -> None:
    """THE BOUND (2026-10-21). A run that is SIGKILLed cannot clean up -- that is how the eight
    helpers of 2026-10-20 were orphaned -- so every fixture a run starts is bounded by its own
    clock. This proves it: the run is killed, and the spinner it left dies on its own."""
    script = _script(tmp_path, BOUNDED_SPINNER)
    out = tmp_path / "pid.txt"
    spinner = 0
    with load_kit.owning(
        [
            sys.executable,
            "-c",
            "import subprocess,sys,time;"
            "p=subprocess.Popen(sys.argv[1:-1], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,"
            "stderr=subprocess.DEVNULL, start_new_session=True);"
            "open(sys.argv[-1],'w').write(str(p.pid));"
            "time.sleep(300)",
            "/bin/sh",
            str(script),
            str(out),
        ]
    ) as child:
        assert load_kit.wait_until(lambda: out.exists() and out.read_text().strip()), (
            "the runner never started its spinner"
        )
        spinner = int(out.read_text().strip())
        load_kit.own_pid(spinner)
        assert load_kit.alive(spinner)
        child.proc.kill()  # SIGKILL: the run cannot clean up
        child.proc.wait(timeout=30)
    assert load_kit.wait_until(lambda: not load_kit.alive(spinner), seconds=20.0), (
        "a killed run left a process burning a core behind it"
    )
