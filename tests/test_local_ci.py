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

import pytest

from conftest import REPO_ROOT

LOCAL_CI = REPO_ROOT / "gates" / "local_ci.sh"


def run_ci(cwd: Path, *args: str):
    env = dict(os.environ)
    env.pop("GOH_CI_STEPS", None)
    return subprocess.run(
        ["/bin/bash", str(LOCAL_CI), *args],
        cwd=cwd, capture_output=True, text=True, env=env,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    d.mkdir()
    (d / ".gatesrc").write_text("")
    return d


def gatesrc(repo: Path, steps: str) -> None:
    (repo / ".gatesrc").write_text(f"GOH_CI_STEPS='{steps}'\n")


# ── dry-run ──


def test_dry_run_lists_steps_and_executes_nothing(repo):
    marker = repo / "marker"
    gatesrc(repo, f"touch {marker}:false")
    r = run_ci(repo, "--dry-run")
    assert r.returncode == 0
    assert not marker.exists(), "dry-run executed a step"
    assert "touch" in r.stdout and "false" in r.stdout


def test_dry_run_shows_step_source(repo):
    gatesrc(repo, "true")
    r = run_ci(repo, "--dry-run", "--step", "echo hi")
    assert r.returncode == 0
    combined = r.stdout + r.stderr
    assert ".gatesrc" in combined
    assert "--step" in combined or "cli" in combined.lower()


# ── execution semantics ──


def test_all_green_exits_zero(repo):
    gatesrc(repo, "true:true")
    r = run_ci(repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_failure_accumulates_does_not_stop(repo):
    # A failing MIDDLE step must not prevent later steps from running —
    # one CI pass should surface every failure at once.
    a, b = repo / "a.done", repo / "b.done"
    gatesrc(repo, f"true:exit 3:touch {b}")
    r = run_ci(repo, "--step", f"touch {a}")
    assert r.returncode != 0
    assert a.exists() and b.exists()


def test_failing_step_output_is_printed(repo):
    sentinel = "SENTINEL-FAILURE-DETAIL"
    gatesrc(repo, f"echo {sentinel} >&2; exit 1")
    r = run_ci(repo)
    assert r.returncode != 0
    assert sentinel in (r.stdout + r.stderr)


def test_successful_step_output_is_captured_not_streamed(repo):
    # The step's stdout must land in the log, not the console. The label IS
    # the command string, so the sentinel must come from OUTPUT only.
    (repo / "secret.txt").write_text("QUIET-SUCCESS-SENTINEL\n")
    gatesrc(repo, "cat secret.txt")
    r = run_ci(repo)
    assert r.returncode == 0
    assert "QUIET-SUCCESS-SENTINEL" not in (r.stdout + r.stderr)


def test_summary_counts_failures(repo):
    gatesrc(repo, "false:false:true")
    r = run_ci(repo)
    assert r.returncode != 0
    assert "2" in (r.stdout + r.stderr)


# ── step sourcing ──


def test_gatesrc_and_cli_steps_combine_in_order(repo):
    logf = repo / "order.log"
    gatesrc(repo, f"echo gatesrc-first >> {logf}")
    r = run_ci(repo, "--step", f"echo cli-second >> {logf}")
    assert r.returncode == 0, r.stdout + r.stderr
    lines = logf.read_text().splitlines()
    assert lines == ["gatesrc-first", "cli-second"]


def test_empty_tokens_are_ignored(repo):
    gatesrc(repo, "::true::")
    r = run_ci(repo)
    assert r.returncode == 0, r.stdout + r.stderr


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


# ── output discipline ──


def test_output_uses_kare_glyphs_only(repo):
    gatesrc(repo, "false:true")
    r = run_ci(repo)
    combined = r.stdout + r.stderr
    stripped = ""
    for line in combined.splitlines():
        stripped += line.strip("→·✓✗⚠ ")
        stripped += "\n"
    for ch in stripped.replace("\n", ""):
        ch_clean = ch if ch not in "→·✓✗⚠" else ""
        if ch_clean == "":
            continue
        assert ord(ch_clean) < 0x1F000 or ch_clean in "→·✓✗⚠↔↑↓←⌘⌥⌨", (
            f"non-Kare pictograph U+{ord(ch_clean):04X} {ch_clean!r}"
        )
