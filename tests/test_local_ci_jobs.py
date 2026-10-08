"""local_ci.sh under GOH_CI_JOBS: steps run concurrently, report in declared order (BACKLOG P2).

The default stays SERIAL: concurrency is a repo's declaration, never an inference. What has to hold
when a repo declares it:
  * reports come out in DECLARED order, whatever order the steps finish in;
  * the fail accumulator and the exit code are unchanged;
  * steps sharing a resource tag (`[tag] cmd`) never overlap;
  * a timeout reaps only its own step's group, and the sibling still passes;
  * a step that reads stdin gets EOF, not a sibling's input or a hang.
"""

import os
import subprocess
import time
from pathlib import Path

import pytest

from timing_bounds import assert_sooner
from conftest import REPO_ROOT

LOCAL_CI = REPO_ROOT / "gates" / "local_ci.sh"


def run_ci(cwd: Path, **env_extra: str):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GOH_CI_")}
    env.update(env_extra)
    t0 = time.monotonic()
    r = subprocess.run(
        ["/bin/bash", str(LOCAL_CI)],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    return r, time.monotonic() - t0


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    d.mkdir()
    return d


def steps(repo: Path, *cmds: str, extra: str = "") -> None:
    (repo / ".gatesrc").write_text(f"GOH_CI_STEPS='{':'.join(cmds)}'\n{extra}")


def meet(d: Path, me: str, *others: str) -> str:
    """A step that proves CONCURRENCY without a clock: it marks itself started, then waits (up to
    10 s) for every other step to have started too. Run serially, the first never sees the rest
    and fails. The wall-clock form (`wall < 1.1`) flaked at 1.11 and 1.33 s under load (5B)."""
    cond = " && ".join(f"[ -e {d}/{o} ]" for o in others)
    return (
        f"touch {d}/{me}; i=0; until {cond} || [ $i -ge 200 ]; do sleep 0.05; i=$((i+1)); done; "
        f"{cond} || exit 9"  # an exit, not a status: a command appended after it must not mask it
    )


def test_jobs_run_concurrently_and_report_in_declared_order(repo):
    steps(
        repo,
        meet(repo, "a", "b", "c") + "; sleep 0.4; echo AAA",
        meet(repo, "b", "a", "c") + "; sleep 0.2; echo BBB",
        meet(repo, "c", "a", "b") + "; echo CCC",
        extra="GOH_CI_JOBS=3\n",
    )
    r, _ = run_ci(repo)
    out = r.stdout + r.stderr
    assert r.returncode == 0, f"the three steps did not run at once with GOH_CI_JOBS=3:\n{out}"
    a, b, c = (out.index(f"[{i}/3]") for i in (1, 2, 3))
    assert a < b < c, f"reports are not in declared order:\n{out}"


def test_default_is_serial(repo):
    # Distinct strings for the same reason as below: a repeated step is a proven-cache hit.
    steps(repo, "sleep 0.6", "sleep 0.61")
    r, wall = run_ci(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert wall >= 1.2, f"steps overlapped ({wall:.2f} s) without GOH_CI_JOBS"


def test_steps_sharing_a_tag_never_overlap(repo):
    # Each step holds a mkdir lock for its whole run; an overlap makes the second mkdir fail.
    hold = "mkdir {d}/lock || exit 7; sleep 0.4; rmdir {d}/lock".format(d=repo)
    steps(
        repo, f"[db] {hold}", f"[db] {hold}", f"[db] {hold}", "sleep 0.1", extra="GOH_CI_JOBS=4\n"
    )
    r, wall = run_ci(repo)
    assert r.returncode == 0, f"tagged steps overlapped:\n{r.stdout}{r.stderr}"
    assert wall >= 1.2, f"three [db] steps of 0.4 s finished in {wall:.2f} s"
    assert "[db]" in r.stdout, "the tag must stay visible in the step's name"


def test_a_failure_among_concurrent_steps_is_accumulated_and_named(repo):
    a, c = repo / "a.done", repo / "c.done"
    steps(
        repo,
        f"sleep 0.3; touch {a}",
        "echo WHY-IT-FAILED; exit 3",
        f"touch {c}",
        extra="GOH_CI_JOBS=3\n",
    )
    r, _ = run_ci(repo)
    assert r.returncode == 1
    assert a.exists() and c.exists(), "a failing step stopped its siblings"
    assert "WHY-IT-FAILED" in r.stderr, "the failing step's output was withheld"
    assert "1 of 3 step(s) failed" in r.stderr


def test_a_timeout_reaps_only_its_own_step(repo):
    done = repo / "sibling.done"
    steps(repo, "sleep 60", f"sleep 1; touch {done}", extra="GOH_CI_JOBS=2\nGOH_LCI_TIMEOUT=2\n")
    r, wall = run_ci(repo)
    assert r.returncode == 1
    assert "TIMED OUT after 2s: sleep 60" in r.stderr, r.stderr
    assert done.exists(), "the sibling was killed with the timed-out step"
    assert_sooner(wall, 3, 60, "a 2 s timeout over a 60 s step")


def test_a_step_reading_stdin_gets_eof(repo):
    steps(repo, "cat >/dev/null; echo GOT-EOF", "true", extra="GOH_CI_JOBS=2\n")
    r, _ = run_ci(repo)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("bad", ["two", "-1", "0"])
def test_a_bad_job_count_is_a_config_error(repo, bad):
    steps(repo, "true", extra=f"GOH_CI_JOBS={bad}\n")
    r, _ = run_ci(repo)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "GOH_CI_JOBS" in r.stderr and bad in r.stderr


def run_cli(cwd: Path, *args: str, **env_extra: str):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GOH_CI_")}
    env.update(env_extra)
    t0 = time.monotonic()
    r = subprocess.run(
        ["/bin/bash", str(LOCAL_CI), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    return r, time.monotonic() - t0


def test_jobs_flag_wins_over_gatesrc(repo):
    """A caller that schedules for the repo (rust_gate.sh --each-crate) owns the budget."""
    steps(repo, meet(repo, "a", "b"), meet(repo, "b", "a"), extra="GOH_CI_JOBS=1\n")
    r, _ = run_cli(repo, "--jobs", "2")
    assert r.returncode == 0, f"--jobs 2 ran serially:\n{r.stdout}{r.stderr}"


def test_steps_only_ignores_the_repos_own_steps(repo):
    marker = repo / "gatesrc-step-ran"
    steps(repo, f"touch {marker}")
    r, _ = run_cli(repo, "--steps-only", "--step", "true")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not marker.exists(), "--steps-only ran a .gatesrc step"
    assert "1 step(s)" in r.stdout


def test_steps_only_without_steps_is_an_error(repo):
    steps(repo, "true")
    r, _ = run_cli(repo, "--steps-only")
    assert r.returncode == 2
    assert "no steps declared" in r.stderr, r.stderr


def test_the_environment_is_not_configuration(repo, tmp_path):
    """A key is configuration iff the repo's own file says so. Measured: a sweep that exported
    GOH_CI_JOBS=4 made every local_ci the suite ran concurrent, and an order test went red --
    the parent's budget had become every nested repo's. GOH_CI_STEPS leaked the same way."""
    leaked = tmp_path / "leaked-step-ran"
    steps(repo, "sleep 0.6", "sleep 0.61")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GOH_CI_")}
    env.update(GOH_CI_JOBS="4", GOH_CI_STEPS=f"touch {leaked}")
    t0 = time.monotonic()
    r = subprocess.run(
        ["/bin/bash", str(LOCAL_CI)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    wall = time.monotonic() - t0
    assert r.returncode == 0, r.stdout + r.stderr
    assert wall >= 1.2, f"an inherited GOH_CI_JOBS made the run concurrent ({wall:.2f} s)"
    assert not leaked.exists(), "an inherited GOH_CI_STEPS ran"
