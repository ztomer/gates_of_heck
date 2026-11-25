"""local_ci.sh — the declarative step runner.

Contract pinned here:
  steps come from .gatesrc GOH_CI_STEPS (colon-separated) and/or --step args;
  a failing step does NOT stop the run (fail accumulator — every orchestrator
  this replaces kept going so one run shows all failures); failed-step logs are
  captured to a temp dir and printed; exit is nonzero iff ANY step failed;
  --dry-run lists without executing.
"""

import os
import subprocess
from pathlib import Path

import pid_owner  # lib/, on the path since conftest imported load_kit
import pytest
from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT

LOCAL_CI = REPO_ROOT / "gates" / "local_ci.sh"


def run_ci(cwd: Path, *args: str):
    env = dict(
        os.environ
    )  # local_ci drops an inherited GOH_CI_STEPS itself (test_local_ci_jobs.py)
    return subprocess.run(
        ["/bin/bash", str(LOCAL_CI), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    d.mkdir()
    write_gatesrc(d, "")
    return d


def gatesrc(repo: Path, steps: str) -> None:
    write_gatesrc(repo, f"GOH_CI_STEPS='{steps}'\n")


# ── dry-run ──


def test_dry_run_lists_steps_with_their_source_and_executes_nothing(repo):
    marker = repo / "marker"
    gatesrc(repo, f"touch {marker}:false")
    r = run_ci(repo, "--dry-run", "--step", "echo hi")
    assert r.returncode == 0
    assert not marker.exists(), "dry-run executed a step"
    assert "touch" in r.stdout and "false" in r.stdout
    combined = r.stdout + r.stderr
    assert ".gatesrc" in combined
    assert "--step" in combined or "cli" in combined.lower()


# ── execution semantics: one green run and one red one, each read for all it must show ──


def test_a_green_run_runs_every_step_in_order_from_the_repo_root(repo):
    """Invoked from OUTSIDE the repo, the root given positionally: a relative-path step resolved
    against the caller's cwd and failed (the runner cd's to $ROOT right after resolving it).

    And a step that reads stdin (`cat`) consumed the REMAINING step list from the runner's heredoc,
    and the run ended early, silently, exit 0: every child gets </dev/null now."""
    tools = repo / "tools"
    tools.mkdir()
    step = tools / "step.sh"
    step.write_text("#!/bin/bash\necho relative >> order.log\n")
    step.chmod(0o755)
    # The label IS the command string, so the sentinel must come from OUTPUT only.
    (repo / "secret.txt").write_text("QUIET-SUCCESS-SENTINEL\n")
    steps = (
        ":",  # an empty token is ignored
        "./tools/step.sh",
        "cat secret.txt",
        "cat",
        # the step string is the wrapper's --label: as a separate argument, a leading '-' read as a
        # flag and the wrapper refused before the step ran (GOH.NITS1)
        "-x 2>/dev/null; echo dashed >> order.log",
        "echo gatesrc-last >> order.log",
        ":",
    )
    gatesrc(repo, ":".join(steps))
    r = run_ci(repo.parent, "--step", "echo cli >> order.log", str(repo))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "QUIET-SUCCESS-SENTINEL" not in (r.stdout + r.stderr), "a passing step's output streamed"
    assert (repo / "order.log").read_text().split() == ["relative", "dashed", "gatesrc-last", "cli"]


def test_a_red_run_goes_on_names_what_failed_and_keeps_its_output(repo):
    """A failing MIDDLE step must not prevent later steps from running: one CI pass surfaces every
    failure at once, each with its output, counted at the end."""
    a, b = repo / "a.done", repo / "b.done"
    gatesrc(repo, f"true:exit 3:echo $((6*7))-FAILURE-DETAIL >&2; exit 1:touch {b}")
    r = run_ci(repo, "--step", f"touch {a}")
    combined = r.stdout + r.stderr
    assert r.returncode == 1, combined  # the accumulator's exit, not a step's 3
    assert a.exists() and b.exists()
    assert "42-FAILURE-DETAIL" in combined
    assert "2 of 5 steps failed" in combined, combined
    # The failure summary hands the operator a directory to search. A FIFO
    # left in it (the step-completion channel) makes `grep -r` block forever
    # on its read -- a hung command where a search was asked for.
    line = next(ln for ln in combined.splitlines() if "full output:" in ln)
    logdir = Path(line.split("full output:", 1)[1].strip())
    entries = list(logdir.iterdir())
    assert entries, "the reported log dir is empty"
    odd = [e.name for e in entries if not e.is_file()]
    assert odd == [], f"non-regular files in the reported log dir: {odd}"
    pictographs = [ch for ch in combined if ord(ch) >= 0x1F000]
    assert pictographs == [], f"non-Kare pictographs {pictographs}"


# ── step sourcing ──


def test_no_steps_anywhere_is_a_named_error(repo):
    (repo / ".gatesrc").unlink()
    r = run_ci(repo)
    assert r.returncode == 2
    combined = r.stdout + r.stderr
    assert "GOH_CI_STEPS" in combined
    assert "--step" in combined


def test_only_empty_steps_is_the_same_named_error(repo):
    gatesrc(repo, ":::")
    r = run_ci(repo)
    assert r.returncode == 2
    assert "GOH_CI_STEPS" in (r.stdout + r.stderr)


def test_help_works(repo):
    r = run_ci(repo, "--help")
    assert r.returncode == 0
    assert "--step" in r.stdout


def test_unknown_flag_rejected(repo):
    r = run_ci(repo, "--fast")
    assert r.returncode == 2


# ── per-step timeout ──


@pytest.mark.load_sensitive
def test_step_timeout_kills_hung_step(repo, monkeypatch):
    # The discriminator is the STEP'S OWN END, never the wall clock (GOH.TIMEOUTFLAKE). A step
    # whose ceiling never fired runs to its end, writes `finished` and exits 0; one that was killed
    # at its ceiling wrote nothing after the sleep and its shell is gone. The elapsed-time bound
    # this test carried (`assert_sooner(dt, 3, 60)`) timed local_ci.sh's whole start-up as well as
    # the step: in the GOH.SEEDPRIVATE land gate the run took ~33 s with the ceiling working (the
    # test's temp dir at 02:14:53, the next test's at 02:15:26) against a bound the lagging load
    # average had set near 30 s. local_ci's start-up is not the property under test.
    monkeypatch.setenv("GOH_LCI_TIMEOUT", "2")
    pid, finished = repo / "step.pid", repo / "finished"
    gatesrc(repo, f"echo $$ $(ps -o lstart= -p $$) > {pid}; sleep 60; touch {finished}")
    r = run_ci(repo)
    combined = r.stdout + r.stderr
    assert r.returncode == 1, combined  # accumulator exit, not 124
    assert "TIMED OUT" in combined and "GOH_LCI_TIMEOUT" in combined, combined
    assert not finished.exists(), "the step ran to its end: the ceiling never fired\n" + combined
    if pid.exists():  # a ceiling that fired before the shell started leaves no pid to ask about
        # by its start time (GOH.STRAYSIGNAL2): a pid reused since is not the step's shell
        assert not pid_owner.alive(pid_owner.read_stamp(pid))


@pytest.mark.load_sensitive
def test_invalid_timeout_is_usage_error(repo, monkeypatch):
    monkeypatch.setenv("GOH_LCI_TIMEOUT", "soon")
    gatesrc(repo, "true")
    r = run_ci(repo)
    assert r.returncode == 2
    assert "GOH_LCI_TIMEOUT" in (r.stdout + r.stderr)
