"""A checkout drops its token (`crates/goh/src/checkoutcreds/`).

`actions/checkout` writes the job token into `.git/config` (`http.*.extraheader`) unless its step
sets `persist-credentials: false`, and `goh credential-urls` refuses a credential in git config:
monitor's CI ran the structural gate after a default checkout and was red 2026-10-05..08, a red
nobody saw locally. What a kept token is lives beside the code (`checkoutcreds/tests.rs`,
mutation-proven); this file is the whole-repo behaviour: the incident as written, index truth,
the composite-action scope, the reasoned exemption, no path exemption, the dispatcher, and the
structural step.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, hermetic_env, run_goh, write

LABEL = "no checkout leaves its token in git config"
WF = ".github/workflows/ci.yml"
# monitor's CI before its fix: the default checkout, then the gate that found the token.
DEFAULT = (
    "on: push\njobs:\n  gate:\n    runs-on: ubuntu-latest\n    steps:\n"
    "      - uses: actions/checkout@v4\n"
    "      - run: tools/gate.sh --full\n"
)
# The same workflow after it.
DROPPED = DEFAULT.replace(
    "actions/checkout@v4\n",
    "actions/checkout@v4\n        with:\n          persist-credentials: false\n",
)
RELEASE = (
    "on: push\njobs:\n  release:\n    runs-on: ubuntu-latest\n    steps:\n"
    "      # persist-credentials-ok: the release job pushes its tag with the job token\n"
    "      - uses: actions/checkout@v4\n"
    "      - run: git push origin --tags\n"
)


def _structural(goh: Path, repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(goh), "structural", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        check=False,
    )


def test_the_incident_is_named_at_its_line_with_the_fix(repo: Path) -> None:
    write(repo, WF, DEFAULT)
    commit_all(repo)
    r = run_goh(repo, "checkout-credentials")
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"{WF}:6: job `gate`, step 1 (`actions/checkout@v4`)" in r.stderr, r.stderr
    assert "persist-credentials: false" in r.stderr, r.stderr  # the fix is named
    assert "persist-credentials-ok: <reason>" in r.stderr, r.stderr  # and the exemption


def test_the_fixed_workflow_passes(repo: Path) -> None:
    write(repo, WF, DROPPED)
    commit_all(repo)
    r = run_goh(repo, "checkout-credentials")
    assert r.returncode == 0 and "1 tracked workflow/action file" in r.stdout, r.stdout + r.stderr


def test_staged_judges_the_index_not_the_worktree(repo: Path) -> None:
    write(repo, WF, DEFAULT)
    git(repo, "add", WF)
    write(repo, WF, DROPPED)  # the worktree is clean; the commit is not
    r = run_goh(repo, "checkout-credentials", "--staged")
    assert r.returncode == 1 and f"{WF}:6:" in r.stderr, r.stdout + r.stderr
    assert run_goh(repo, "checkout-credentials").returncode == 0  # the worktree's own verdict


def test_staged_judges_only_staged_workflows(repo: Path) -> None:
    write(repo, WF, DEFAULT)
    commit_all(repo)
    write(repo, ".github/workflows/other.yml", DROPPED)
    git(repo, "add", ".github/workflows/other.yml")
    r = run_goh(repo, "checkout-credentials", "--staged")
    assert r.returncode == 0 and "1 staged workflow/action file" in r.stdout, r.stdout + r.stderr


def test_a_composite_action_is_in_scope(repo: Path) -> None:
    write(
        repo,
        ".github/actions/setup/action.yml",
        "name: setup\nruns:\n  using: composite\n  steps:\n    - uses: actions/checkout@v4\n",
    )
    commit_all(repo)
    r = run_goh(repo, "checkout-credentials")
    assert r.returncode == 1, r.stdout + r.stderr
    assert ".github/actions/setup/action.yml:5: composite action, step 1" in r.stderr, r.stderr


def test_a_reasoned_exemption_passes_and_an_empty_one_does_not(repo: Path) -> None:
    write(repo, WF, RELEASE)
    commit_all(repo)
    assert run_goh(repo, "checkout-credentials").returncode == 0
    write(repo, WF, RELEASE.replace(" the release job pushes its tag with the job token", ""))
    commit_all(repo)
    r = run_goh(repo, "checkout-credentials")
    assert r.returncode == 1 and "with no reason" in r.stderr, r.stdout + r.stderr


def test_no_path_exemption_reaches_it(repo: Path) -> None:
    """A credential check takes no path exemption (`goh secrets`): `GOH_EXCLUDE` is about style."""
    write(repo, WF, DEFAULT)
    write(repo, ".gatesrc", "GOH_EXCLUDE='^\\.github/'\n")
    commit_all(repo)
    assert run_goh(repo, "checkout-credentials").returncode == 1
    r = run_goh(repo, "checkout-credentials", "--exclude", "x")
    assert r.returncode == 2, r.stdout + r.stderr  # no such flag


def test_the_dispatcher_knows_it(repo: Path) -> None:
    write(repo, WF, DEFAULT)
    commit_all(repo)
    r = subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "goh.sh"), "checkout-credentials"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        check=False,
    )
    assert r.returncode == 1 and "unknown check" not in r.stderr, r.stdout + r.stderr


def test_the_structural_step_fails_at_both_scopes(goh: Path, repo: Path) -> None:
    write(repo, WF, DEFAULT)
    git(repo, "add", WF)
    staged = _structural(goh, repo, "--staged")
    assert staged.returncode == 1 and f"structural: {LABEL}" in staged.stderr, staged.stderr
    commit_all(repo)
    full = _structural(goh, repo, "--full")
    assert full.returncode == 1 and f"structural: {LABEL}" in full.stderr, full.stderr
