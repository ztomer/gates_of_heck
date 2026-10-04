"""`lib/bounded_run.py` — the ceiling, and the subtree sweep that makes it mean anything.

THE MEASUREMENT THESE PIN. `gates/local_ci.sh`'s timeout swept the timed-out step with
`pkill -P "$pid"` — DIRECT children only. A test step is a chain (`cargo test` → the test binary →
the server the test spawned), so against `bash -c 'sleep 400 & wait'`, measured 2026-10-03:

    pkill -TERM -P $pid ; pkill -KILL -P $pid   →  2 grandchildren STILL ALIVE
    killpg(getpgid(pid), SIGKILL)              →  NOTHING SURVIVED

The mechanism meant to unstick a hung step was leaving running exactly the processes that make the
next step hang. So the sweep is the test, and the ceiling alone would be a gate that stops the
symptom and keeps the cause.

`calibrate-the-instrument` in practice: the first version of the measurement probe matched processes
by NAME and reported a confident `survivors=0` for every arm, because a copied `/bin/sleep` is
killed by the kernel at exec (rc=137 — its signature does not travel with the copy) and the probe was
measuring a process that never existed. Every helper here therefore works from PIDs the test itself
created.
"""

from pathlib import Path
import os
import signal
import re
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import bounded_run  # noqa: E402


def alive(pid: int) -> bool:
    """Is this pid still running? `kill -0`, which is the same question the OS answers."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def spawn_sleeper(seconds: int = 600) -> subprocess.Popen:
    """A child that will outlive this test if nobody kills it.

    `/bin/sleep` by path, never a copy: a copied Mach-O loses its signature and the kernel kills it
    at exec (rc=137), which makes a leak probe report "nothing survived" for a process that never
    ran. That is how the first version of this file's measurement was wrong.
    """
    return subprocess.Popen(["/bin/sleep", str(seconds)])


# Every sleeper this module starts, so a FAILING test cannot leave the very orphans it is testing
# for. The same rule the module enforces on other code, applied to itself.
_SLEEPERS: list[int] = []


def note(proc: subprocess.Popen) -> int:
    _SLEEPERS.append(proc.pid)
    return proc.pid


@pytest.fixture(autouse=True)
def _no_strays():
    yield
    for pid in _SLEEPERS:
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    _SLEEPERS.clear()


def run(cmd, timeout=30, grace=1):
    return bounded_run.run(cmd, timeout, grace, "the step")


# ── the sweep ────────────────────────────────────────────────────────────────


def test_the_sweep_reaches_a_grandchild(tmp_path: Path) -> None:
    """The case the old `pkill -P` sweep missed, measured above. A grandchild that outlived the
    sweep is an orphan, and an orphan is what makes the NEXT run hang."""
    marker = tmp_path / "grandchild"
    # A shell that backgrounds a long sleep and waits: the step's direct child is the shell, and
    # the sleep is a GRANDCHILD. `pkill -P` can only ever see the shell.
    code = run(
        ["/bin/bash", "-c", f"/bin/sleep 900 & echo $! > {marker}; wait"], timeout=2, grace=2
    )
    assert code == bounded_run.TIMEOUT_EXIT, code
    assert marker.exists(), "the fixture never recorded a grandchild, so it proved nothing"
    grandchild = int(marker.read_text().strip())
    time.sleep(0.3)
    assert not alive(grandchild), (
        f"pid {grandchild} survived the sweep — the bound stops the symptom and keeps the cause"
    )


def test_a_hanging_step_is_reported_not_silently_killed(tmp_path: Path, capsys) -> None:
    """`TIMED OUT after Ns`, with the number. A ceiling nobody gave a value, or that expires without
    saying so, is the 2026-10-03 incident with a shorter fuse."""
    code = run(["/bin/bash", "-c", "sleep 901"], timeout=2, grace=1)
    captured = capsys.readouterr()
    assert code == bounded_run.TIMEOUT_EXIT, code
    assert "TIMED OUT after 2s" in captured.err, captured.err
    assert "the step" in captured.err, captured.err


def test_the_ceiling_is_honoured_within_its_own_tolerance() -> None:
    """The bound must be a BOUND. A gate that says 30 s and means "eventually" is the defect."""
    started = time.monotonic()
    code = run(["/bin/bash", "-c", "sleep 902"], timeout=2, grace=1)
    elapsed = time.monotonic() - started
    assert code == bounded_run.TIMEOUT_EXIT
    assert elapsed < 20, f"a 2s ceiling took {elapsed:.1f}s"


# ── pass-through: a bounded step must not change anything ────────────────────


def test_a_passing_step_passes_its_own_code_through() -> None:
    assert run(["/bin/sh", "-c", "exit 0"]) == 0


def test_a_failing_step_reports_its_own_code_not_124() -> None:
    """Otherwise every red step reads as a timeout and the ceiling becomes the thing people blame."""
    assert run(["/bin/sh", "-c", "exit 7"]) == 7


def test_the_group_outlives_reparenting_so_a_leak_is_still_visible() -> None:
    """The measurement that makes the canary exact. A leaked child is REPARENTED the instant its
    parent exits — `ps` shows `ppid 1` — so a descendant walk taken at exit finds nothing and a
    canary built on it reports a clean run while the server runs on. The process GROUP survives.

    Run through `run_step`, which is what a gate uses, so the assertion is on the value a caller
    actually receives rather than on a helper's internals.
    """
    outcome = bounded_run.run_step(
        ["/bin/sh", "-c", "/bin/sleep 903 & exit 0"],
        30,
        1,
        "leaky step",
        output=bounded_run.DEVNULL_SENTINEL,
    )
    try:
        assert outcome.code == 0, outcome
        assert outcome.survivors, (
            "the leak was invisible: a reparented child has to still be findable by its group"
        )
        for pid in outcome.survivors:
            assert alive(pid)
    finally:
        for pid in outcome.survivors:
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass


def test_a_step_that_leaves_nothing_reports_no_survivors() -> None:
    """The other direction, and the one that keeps the canary from crying wolf."""
    outcome = bounded_run.run_step(
        ["/bin/sh", "-c", "exit 0"], 30, 1, "clean step", output=bounded_run.DEVNULL_SENTINEL
    )
    assert outcome.code == 0
    assert not outcome.survivors, outcome.survivors
    assert not outcome.timed_out


def test_a_command_that_does_not_exist_is_127_not_a_silent_pass() -> None:
    """An unbound ceiling must never read as a step that passed."""
    assert run(["/nonexistent/goh-no-such-binary"]) == 127


# ── the CLI, and its refusals ────────────────────────────────────────────────


def test_usage_errors_are_exit_two() -> None:
    path = Path(bounded_run.__file__)
    for argv in (["--timeout", "30"], ["--timeout", "0", "--", "true"]):
        proc = subprocess.run(
            [sys.executable, str(path), *argv], capture_output=True, text=True, check=False
        )
        assert proc.returncode == 2, (argv, proc.returncode, proc.stdout)


@pytest.mark.parametrize("value", ["abc", "0", "-5", "10s"])
def test_a_bad_ceiling_is_refused_not_ignored(value: str) -> None:
    """A typo'd or non-positive ceiling that is silently ignored leaves the step unbounded, which is
    the defect. `abc` is argparse's refusal and `0`/`-5` are ours; both are exit 2 and both NAME the
    value, and neither runs the command at all."""
    path = Path(bounded_run.__file__)
    marker = Path(bounded_run.__file__).parent / "_bounded_run_ran_this"
    marker.unlink(missing_ok=True)
    proc = subprocess.run(
        [sys.executable, str(path), "--timeout", value, "--", "/usr/bin/touch", str(marker)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2, (value, proc.returncode, proc.stdout, proc.stderr)
    assert value in proc.stderr, (value, proc.stderr)
    assert not marker.exists(), f"a refused ceiling still ran the step ({value})"


# ── the wiring, because a library nothing calls is a rumour (R7) ─────────────


def test_local_ci_runs_its_steps_through_this_helper() -> None:
    """`local_ci.sh` had its own `pkill -P` sweep inlined. The fix is only real if the runner goes
    through the helper, so the call site is pinned rather than assumed. It goes through
    `lib/orphan_canary.py wrap`, which delegates here -- ONE call per step, because the ceiling, the
    output capture and the leak report are one thing and two call sites is how they drift apart."""
    source = (Path(__file__).resolve().parent.parent / "gates" / "local_ci.sh").read_text()
    assert "lib/orphan_canary.py" in source, "local_ci.sh no longer runs steps through the ceiling"
    assert 'lib/bounded_run.py" "${args[@]}"' not in source, (
        "local_ci.sh grew a second runner call — the ceiling and the leak report must be one"
    )
    # ...and the old sweep is gone from the CODE. It survives in a comment that records the
    # measurement, which is the right place for it; what must not come back is the command.
    assert not re.search(r"^[^#]*pkill -(?:TERM |KILL )?-P", source, re.M), (
        "the direct-children-only sweep is back in the runner"
    )


def test_common_goh_step_has_a_ceiling() -> None:
    """`goh_step` had NO timeout while `local_ci.sh` did, so the same hang was bounded in one path
    and unbounded in the other. That is the absence of a policy, not two policies."""
    source = (Path(__file__).resolve().parent.parent / "gates" / "_common.sh").read_text()
    assert "bounded_run.py" in source
    assert "GOH_STEP_TIMEOUT" in source


def test_the_default_ceiling_exists_on_both_paths() -> None:
    """Unset meant "no limit", and it was unset in every repo in the estate. A default that has to
    be typed is a default that does not exist."""
    for name, needle in (
        ("gates/local_ci.sh", "${GOH_LCI_TIMEOUT:-900}"),
        ("gates/_common.sh", "${GOH_STEP_TIMEOUT:-$_goh_step_default_timeout}"),
    ):
        source = (Path(__file__).resolve().parent.parent / name).read_text()
        assert needle in source, f"{name} has no default ceiling"
