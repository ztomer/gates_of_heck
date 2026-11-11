"""A workflow runs on a named image and named releases (`crates/goh/src/workflowpin/`).

`ubuntu-latest` is re-pointed by GitHub to a new OS image (CI warned it was moving to Ubuntu 26,
M.FLOODREAP), and `actions/checkout@v4` or `dtolnay/rust-toolchain@stable` is re-pointed by its
owner: the same file runs different code on different days, against the house rule that "latest" is
a named release (GOH.RUNNERPIN). What moves lives beside the code (`workflowpin/tests.rs`,
mutation-proven); this file is the whole-repo behaviour: the incident as written, the fix, index
truth, the composite-action scope, the reasoned escape, no path exemption, the dispatcher, the
structural step and its floor.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, hermetic_env, run_goh, write

LABEL = "no moving runner label or action ref in a workflow"
WF = ".github/workflows/ci.yml"
SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
# antiknob's workflow before GOH.RUNNERPIN: a major-only tag and a branch, on a moving runner.
MOVING = (
    "on: push\njobs:\n  gate:\n    runs-on: ubuntu-latest\n    steps:\n"
    "      - uses: actions/checkout@v7\n"
    "      - uses: dtolnay/rust-toolchain@stable\n"
)
# The same workflow after it.
PINNED = (
    "on: push\njobs:\n  gate:\n    runs-on: ubuntu-24.04\n    steps:\n"
    f"      - uses: actions/checkout@{SHA} # v7.0.1\n"
    "      - uses: dtolnay/rust-toolchain@b725eda525362e7cf34af9a7243cac1e3f01a7ec # no releases\n"
)
ESCAPED = (
    "on: push\njobs:\n  gate:\n    runs-on: ubuntu-24.04\n    steps:\n"
    "      # workflow-pin-ok: the vendor publishes no release tags\n"
    "      - uses: vendor/tool@main\n"
)


def _structural(goh: Path, repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(goh), "structural", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_the_incident_is_named_at_its_lines_with_the_fix(repo: Path) -> None:
    write(repo, WF, MOVING)
    commit_all(repo)
    r = run_goh(repo, "workflow-pins")
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"{WF}:4: runner `ubuntu-latest`" in r.stderr, r.stderr
    assert f"{WF}:6: `actions/checkout@v7` is on `v7`" in r.stderr, r.stderr
    assert f"{WF}:7: `dtolnay/rust-toolchain@stable` is on `stable`" in r.stderr, r.stderr
    assert "ubuntu-24.04" in r.stderr and "workflow-pin-ok: <reason>" in r.stderr, r.stderr


def test_the_pinned_workflow_passes(repo: Path) -> None:
    write(repo, WF, PINNED)
    commit_all(repo)
    r = run_goh(repo, "workflow-pins")
    assert r.returncode == 0 and "1 workflow/action file," in r.stdout, r.stdout + r.stderr
    assert "3 references: 2 pinned by commit" in r.stdout, r.stdout


def test_staged_judges_the_index_not_the_worktree(repo: Path) -> None:
    write(repo, WF, MOVING)
    git(repo, "add", WF)
    write(repo, WF, PINNED)  # the worktree is clean; the commit is not
    r = run_goh(repo, "workflow-pins", "--staged")
    assert r.returncode == 1 and f"{WF}:4:" in r.stderr, r.stdout + r.stderr
    assert run_goh(repo, "workflow-pins").returncode == 0  # the worktree's own verdict


def test_a_composite_action_is_in_scope(repo: Path) -> None:
    write(
        repo,
        ".github/actions/setup/action.yml",
        "name: setup\nruns:\n  using: composite\n  steps:\n    - uses: actions/setup-python@v7\n",
    )
    commit_all(repo)
    r = run_goh(repo, "workflow-pins")
    assert r.returncode == 1, r.stdout + r.stderr
    assert ".github/actions/setup/action.yml:5:" in r.stderr, r.stderr


def test_a_reasoned_escape_passes_and_an_empty_one_does_not(repo: Path) -> None:
    write(repo, WF, ESCAPED)
    commit_all(repo)
    assert run_goh(repo, "workflow-pins").returncode == 0
    write(repo, WF, ESCAPED.replace(" the vendor publishes no release tags", ""))
    commit_all(repo)
    r = run_goh(repo, "workflow-pins")
    assert r.returncode == 1 and "with no reason" in r.stderr, r.stdout + r.stderr


def test_no_path_exemption_reaches_it(repo: Path) -> None:
    """What CI runs on is not a style: `GOH_EXCLUDE` does not reach `.github/`."""
    write(repo, WF, MOVING)
    write(repo, ".gatesrc", "GOH_EXCLUDE='^\\.github/'\n")
    commit_all(repo)
    assert run_goh(repo, "workflow-pins").returncode == 1
    r = run_goh(repo, "workflow-pins", "--exclude", "x")
    assert r.returncode == 2, r.stdout + r.stderr  # no such flag


def test_the_dispatcher_knows_it(repo: Path) -> None:
    write(repo, WF, MOVING)
    commit_all(repo)
    r = subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "goh.sh"), "workflow-pins"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 1 and "unknown check" not in r.stderr, r.stdout + r.stderr


def test_the_structural_step_fails_at_both_scopes(goh: Path, repo: Path) -> None:
    # No checkout step: the checkout-credentials step, earlier in the pipeline, would stop first.
    write(repo, WF, "on: push\njobs:\n  gate:\n    runs-on: ubuntu-latest\n    steps: []\n")
    git(repo, "add", WF)
    staged = _structural(goh, repo, "--staged")
    assert staged.returncode == 1 and f"structural: {LABEL}" in staged.stderr, staged.stderr
    commit_all(repo)
    full = _structural(goh, repo, "--full")
    assert full.returncode == 1 and f"structural: {LABEL}" in full.stderr, full.stderr


def test_the_floor_reds_a_scan_that_lost_its_subject(goh: Path, repo: Path) -> None:
    """`GOH_WORKFLOW_PINS_MIN_REFS`: three read, a floor of four is red, three is green."""
    write(repo, WF, PINNED)
    write(repo, ".gatesrc", "GOH_WORKFLOW_PINS_MIN_REFS=4\n")
    commit_all(repo)
    below = f"`{LABEL}` read 3 workflow references and .gatesrc declares a floor of 4"
    red = _structural(goh, repo, "--full", "--keep-going")
    assert red.returncode == 1 and below in red.stdout + red.stderr, red.stdout + red.stderr
    write(repo, ".gatesrc", "GOH_WORKFLOW_PINS_MIN_REFS=3\n")
    commit_all(repo)
    green = _structural(goh, repo, "--full", "--keep-going")
    assert "floor of 3 (GOH_WORKFLOW_PINS_MIN_REFS)" not in green.stdout + green.stderr, (
        green.stdout + green.stderr
    )
