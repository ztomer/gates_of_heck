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
next killed run, so each one stops on its own CPU budget.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import load_kit
import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))

import load_reap
import load_registry as reg

# ONE WORKER (with tests/test_load_reap.py, test_load_tool.py ...): the backstop's sweep is GLOBAL,
# so a sweep run here kills the deliberate orphan another worker's test is about to judge, and
# theirs kills ours -- "assert pid in report.killed" against an empty report, because somebody
# else's sweep got there first (G9, 2026-10-26). A shared group keeps every sweep and every
# deliberate orphan on one worker, and a worker runs its tests one by one.
pytestmark = pytest.mark.xdist_group("load_orphans")

#
# Every fixture here is BOUNDED: a control that spins forever is a leak waiting for the next
# killed run, and the point of these tests is that the suite can be SIGKILLed without leaving a
# core burning behind it. The spinner stops on its own CPU budget.

# A bounded spinner: it burns a core, and the kernel stops it (SIGXCPU) once it has burned
# SPINNER_CPU_S of its OWN CPU. The bound is what makes the suite safe to SIGKILL, and it is in
# the quantity the tests wait for: a wall-clock timer (`sleep 6; kill`) ran out on a starved box
# before the spinner had burned FLOOR_S, and a dead pid reads as no CPU -- "the fixture never
# burned a core" in goh's coverage run (push of 0057e76, 2026-10-25). A CPU budget cannot expire
# before the floor is reached, however long the box makes it wait, and it caps what a leaked
# spinner can ever burn.
SPINNER_CPU_S = 5


def _bounded_spinner(budget_s: int = SPINNER_CPU_S) -> str:
    """A spinner that stops on its own CPU budget."""
    return f"""#!/bin/sh
ulimit -t {budget_s}
while :; do :; done
"""


BOUNDED_SPINNER = _bounded_spinner()
# The budget of a spinner a test must still find ALIVE after the backstop has looked at it. The
# window from "it has burned the floor" to the last check is the test's WALL time -- ps calls and
# the backstop's one-second sample, all of them stretched by a busy box -- and a spinner can spend
# at most one CPU-second of its budget per wall second in it. Five seconds left a starved test
# about four seconds of wall to finish in; thirty leaves it half a minute, and the owner (or the
# suite's reaper) stops it long before. The bound on a leak stays: half the house's own floor.
JUDGED_CPU_S = 30
JUDGED_SPINNER = _bounded_spinner(JUDGED_CPU_S)

# A near miss: a loop that does work, so it is not a spinner at all.
# It is bounded by a COUNT of its sleeps: it burns no CPU, so a CPU budget would never end it, and
# an unbounded one outlived the run load.py killed at its deadline by half an hour (2026-10-25).
SLEEPER = """#!/bin/sh
i=0
while [ $i -lt 120 ]; do sleep 1; i=$((i + 1)); done
"""

# A prefix of the shape: the loop is MENTIONED, not run, and the work it does is real.
# It works until the TEST releases it ($1 appears), not for a fixed amount of work: a count of
# 400000 was under a CPU-second, so on a busy box the control finished its work between "it has
# burned the floor" and "is it still alive?", and a control that EXITED read as one the backstop
# killed (G9, 2026-10-26). Its leak bound is a CPU budget, and if the budget is what ends it, it
# says so ($2): the backstop's SIGTERM/SIGKILL never writes that file.
MENTION = f"""#!/bin/sh
ulimit -t {JUDGED_CPU_S}
trap 'echo budget > "$2"; exit 0' XCPU
echo 'while :; do :; done'
i=0
while [ ! -e "$1" ]; do i=$((i + 1)); done
echo released > "$2"
"""

# The CPU the fixtures are given, in seconds. The house's floor is a minute (load_reap.
# BURN_CPU_S); a test cannot burn a minute of CPU, so the floor is a parameter and the test
# asks the same four questions with a floor a fixture can reach.
FLOOR_S = 0.3
# How long the starved-spinner test holds its spinner off the CPU: past the 6 s wall-clock timer
# the fixture once had, which is the starvation that timer could not survive.
STARVED_S = 8


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
    script = _script(tmp_path, JUDGED_SPINNER)
    pid = load_kit.orphan(["/bin/sh", str(script)])
    assert reg.pid_ppid(pid) == 1, "the fixture did not become an orphan"
    assert load_kit.wait_until(lambda: _burning(pid)), "the fixture never burned a core"
    report = load_reap.reap_burning_orphans(min_cpu_s=FLOOR_S)
    assert pid in report.killed, report
    assert not load_kit.alive(pid), "the spinner outlived the backstop"


def test_the_backstop_leaves_a_spinner_with_a_live_parent_alone(tmp_path: Path) -> None:
    """NEGATIVE CONTROL. A live parent: the owner is here, and it will reap it. The shape, the
    burn and the code are all the leak's -- only the parent saves it."""
    script = _script(tmp_path, JUDGED_SPINNER)
    with load_kit.owning(["/bin/sh", str(script)]) as child:
        pid = child.proc.pid
        assert load_kit.wait_until(lambda: _burning(pid)), "the fixture never burned a core"
        report = load_reap.reap_burning_orphans(min_cpu_s=FLOOR_S)
        assert pid not in report.killed, report
        # Alive, or ended by its OWN budget (SIGXCPU) -- the backstop sends SIGTERM and SIGKILL,
        # never that, so the verdict does not hang on how long a busy box made the test take.
        assert child.proc.poll() in (None, -signal.SIGXCPU), (
            "the backstop killed a spinner whose parent was alive"
        )


def test_a_starved_spinner_still_reaches_the_floor(tmp_path: Path) -> None:
    """THE FIXTURE'S BOUND (push of 0057e76, 2026-10-25). A box can hold the spinner off the CPU
    for as long as it likes -- here, stopped outright for longer than the wall-clock timer it once
    had -- and the spinner must still be alive to burn FLOOR_S when it is let go: its bound is its
    own CPU, which a stopped or starved process does not spend."""
    script = _script(tmp_path, BOUNDED_SPINNER)
    with load_kit.owning(["/bin/sh", str(script)]) as child:
        pid = child.proc.pid
        # Stopped once it SPINS: its loop, and any timer started before it, are running.
        assert load_kit.wait_until(lambda: _cpu_of(pid) > 0), "the fixture never started spinning"
        os.kill(pid, signal.SIGSTOP)
        time.sleep(STARVED_S)
        os.kill(pid, signal.SIGCONT)
        assert load_kit.wait_until(lambda: _burning(pid)), "a starved spinner never burned a core"
        assert SPINNER_CPU_S > FLOOR_S * 10, "the budget must leave the floor far behind"


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
    release, ended = tmp_path / "release", tmp_path / "ended"
    pid = load_kit.orphan(["/bin/sh", str(script), str(release), str(ended)])
    try:
        assert load_kit.wait_until(lambda: _burning(pid)), "the fixture never burned a core"
        report = load_reap.reap_burning_orphans(min_cpu_s=FLOOR_S)
        assert pid not in report.killed, report
        # Alive, or ended by its own budget and said so: a control the backstop killed is neither.
        assert load_kit.alive(pid) or ended.exists(), (
            "the backstop killed a process that only mentions the shape"
        )
        assert not ended.exists() or ended.read_text().strip() == "budget", ended.read_text()
    finally:
        release.touch()


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
            (
                "import subprocess,sys,time;"
                "p=subprocess.Popen(sys.argv[1:-1], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,"
                "stderr=subprocess.DEVNULL, start_new_session=True);"
                "open(sys.argv[-1],'w').write(str(p.pid));"
                "time.sleep(300)"
            ),
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


# The calls that SWEEP the whole machine, and the call that leaves an orphan for a sweep to find.
# A CALL, not a definition named in a string (`"def reap_burning_orphans("` is a slice bound).
# The group, as the file's MARK: a line that only names it (as this rule's own does) is not one.
ON_THE_WORKER = re.compile(r"^pytestmark = .*xdist_group\(\"load_orphans\"\)", re.MULTILINE)
GLOBAL_SWEEP = re.compile(r"(?<!def )\b(reap_burning_orphans|reap_orphans|load_kit\.orphan)\(")


def test_every_file_that_sweeps_or_orphans_runs_on_the_one_worker() -> None:
    """THE CLASS (G9, 2026-10-26). This file swept the machine from every xdist worker while
    test_load_reap.py's orphans waited on another, so each killed the other's fixture and a load
    of ten decided which: the sweep is global, and a file that sweeps, or leaves an orphan for a
    sweep, belongs to the `load_orphans` group -- a marker nobody remembered, so a rule that reads."""
    missing = [
        path.name
        for path in sorted((REPO_ROOT / "tests").glob("test_*.py"))
        if GLOBAL_SWEEP.search(text := path.read_text(encoding="utf-8"))
        and not ON_THE_WORKER.search(text)
    ]
    assert not missing, f"sweeps or orphans outside the load_orphans worker: {missing}"
