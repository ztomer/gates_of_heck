"""The hook's carried index is read only by `goh_bind_hook_index` (`crates/goh/src/hookindex/`).

routines' and media_server's tools/gate.sh read `${GOH_HOOK_INDEX_FILE:-...}` bare, so a gate a
test ran on a FIXTURE under a `commit -a` hook bound the outer commit's index and died "unable to
read 404acec" (2026-10-08, contract #12). What an expansion is lives beside the code
(`hookindex/tests.rs`, mutation-proven); this file is the whole-repo behaviour: the incident as
written, index truth, the binder's own file, exclusion, the dispatcher, and the structural step.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, hermetic_env, run_goh, write

LABEL = "no bare read of the hook's index"
# routines' tools/gate.sh line 33 before 852b8d5.
BARE = (
    "#!/usr/bin/env bash\nset -euo pipefail\n"
    'staged="$(GIT_INDEX_FILE="${GOH_HOOK_INDEX_FILE:-$(git rev-parse --git-path index)}" '
    'git diff --cached --name-only --diff-filter=d)"\n'
)
# The same line after it.
BOUND = (
    "#!/usr/bin/env bash\nset -euo pipefail\n"
    'staged="$(. "$GOH/gates/_git_env.sh"; goh_bind_hook_index; git diff --cached --name-only)"\n'
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


def test_the_incident_is_named_at_its_line(repo: Path) -> None:
    write(repo, "tools/gate.sh", BARE)
    commit_all(repo)
    r = run_goh(repo, "bare-hook-index")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "tools/gate.sh:3:" in r.stderr and "$GOH_HOOK_INDEX_FILE" in r.stderr, r.stderr
    assert "goh_bind_hook_index" in r.stderr, r.stderr  # the fix is named


def test_the_fixed_line_passes(repo: Path) -> None:
    write(repo, "tools/gate.sh", BOUND)
    commit_all(repo)
    r = run_goh(repo, "bare-hook-index")
    assert r.returncode == 0 and "1 tracked shell source" in r.stdout, r.stdout + r.stderr


def test_staged_judges_the_index_not_the_worktree(repo: Path) -> None:
    write(repo, "tools/gate.sh", BARE)
    git(repo, "add", "tools/gate.sh")
    write(repo, "tools/gate.sh", BOUND)  # the worktree is clean; the commit is not
    r = run_goh(repo, "bare-hook-index", "--staged")
    assert r.returncode == 1 and "tools/gate.sh:3:" in r.stderr, r.stdout + r.stderr
    assert run_goh(repo, "bare-hook-index").returncode == 0  # the worktree's own verdict


def test_the_binders_own_file_is_its_one_reader(repo: Path) -> None:
    """gates/_git_env.sh reads both variables -- it is where they are bound. The exemption is the
    DEFINITION, not a path: this repo's own copy passes, and so does a consumer's that defines it."""
    write(repo, "lib/env.sh", (REPO_ROOT / "gates" / "_git_env.sh").read_text())
    commit_all(repo)
    r = run_goh(repo, "bare-hook-index")
    assert r.returncode == 0, r.stdout + r.stderr


def test_goh_exclude_applies(repo: Path) -> None:
    write(repo, "vendor/gate.sh", BARE)
    write(repo, ".gatesrc", "GOH_EXCLUDE='^vendor/'\n")
    commit_all(repo)
    assert run_goh(repo, "bare-hook-index").returncode == 0
    assert run_goh(repo, "bare-hook-index", "--exclude", "").returncode == 1


def test_the_dispatcher_knows_it(repo: Path) -> None:
    write(repo, "tools/gate.sh", BARE)
    commit_all(repo)
    r = subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "goh.sh"), "bare-hook-index"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        check=False,
    )
    assert r.returncode == 1 and "unknown check" not in r.stderr, r.stdout + r.stderr


def test_the_structural_step_fails_at_both_scopes(goh: Path, repo: Path) -> None:
    write(repo, "tools/gate.sh", BARE)
    git(repo, "add", "tools/gate.sh")
    staged = _structural(goh, repo, "--staged")
    assert staged.returncode == 1 and f"structural: {LABEL}" in staged.stderr, staged.stderr
    commit_all(repo)
    full = _structural(goh, repo, "--full")
    assert full.returncode == 1 and f"structural: {LABEL}" in full.stderr, full.stderr
