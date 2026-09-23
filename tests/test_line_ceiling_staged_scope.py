"""The line-cap ceiling and ratchet steps at --staged police the INDEX.

Siblings of the skills-corpus fix (5afa28a), same class: "line-cap exemptions
carry a ceiling" read GOH_LINE_BASELINE from the working tree, and "cap-exempt
files within their ceilings" measured `wc -l` of working-tree files against a
working-tree baseline -- at pre-commit scope, judging a commit neither had
seen. Both directions are pinned for both steps and both pipelines:

  - a clean staged change passes over an unstaged violation (no false red);
  - a staged violation fails though its fix is unstaged (no false green).

Each case also runs --full, proving the violation is real to the checker and
that --full still reads the tree.

The last tests go through a REAL `git commit`, because a hook's environment is
not a test's: a plain commit hands the hook GIT_INDEX_FILE=.git/index,
RELATIVE, which resolves to nothing inside the index view; `git commit <path>`
hands it a temporary index the gate must judge instead of .git/index.
"""
import os
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT, commit_all, git, stage, write

BASELINE = ".gates_loc_baseline.txt"
GATESRC = (
    "GOH_MAX_LINES=5\n"
    "GOH_LINE_EXCLUDE='big\\.txt'\n"
    f"GOH_LINE_BASELINE={BASELINE}\n"
)


def _lines(n: int) -> str:
    return "".join(f"line {i}\n" for i in range(n))


def _ceiling_repo(repo: Path) -> Path:
    """big.txt: 8 lines, exempt from the 5-line cap, bounded by a ceiling of 8."""
    write(repo, ".gatesrc", GATESRC)
    write(repo, "big.txt", _lines(8))
    write(repo, BASELINE, "8\tbig.txt\n")
    commit_all(repo)
    return repo


def _env(runner: str, goh: Path, **extra: str) -> dict:
    env = dict(os.environ, GOH_DIR=str(REPO_ROOT), **extra)
    env.pop("GOH_BIN", None)
    env.pop("GOH_NO_NATIVE", None)
    if runner == "python":
        env["GOH_NO_NATIVE"] = "1"
    else:
        env["GOH_BIN"] = str(goh)
    return env


def _run(runner: str, goh: Path, repo: Path, scope: str, **extra: str) -> subprocess.CompletedProcess:
    cmd = ["bash", str(REPO_ROOT / "gates" / "structural.sh"), scope]
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                          env=_env(runner, goh, **extra))


RUNNERS = pytest.mark.parametrize("runner", ["python", "native"])
CEILING = "line-cap exemptions carry a ceiling failed"
RATCHET = "cap-exempt files within their ceilings failed"
# The VERDICTS, not just the step labels: a checker that crashes inside the
# view (no .git pointer, an unresolvable GIT_INDEX_FILE) fails the same step
# and would satisfy a label-only assertion. Watched happen, 2026-09-23.
NO_CEILING = "exempt from the cap with NO ceiling"
GREW = "big.txt: 8 -> 12"


def _out(r: subprocess.CompletedProcess) -> str:
    return r.stdout + r.stderr


@RUNNERS
def test_ceiling_clean_staged_change_passes_over_an_unstaged_dropped_ceiling(runner, repo, goh):
    _ceiling_repo(repo)
    write(repo, "small.txt", "x\n")
    stage(repo, "small.txt")
    write(repo, BASELINE, "# ceiling dropped, not staged\n0\t__under_cap_sentinel__\n")

    staged = _run(runner, goh, repo, "--staged")
    assert staged.returncode == 0, _out(staged)

    full = _run(runner, goh, repo, "--full")
    assert full.returncode == 1 and CEILING in _out(full) and NO_CEILING in _out(full), _out(full)


@RUNNERS
def test_ceiling_staged_dropped_ceiling_fails_though_restored_in_the_tree(runner, repo, goh):
    _ceiling_repo(repo)
    write(repo, BASELINE, "0\t__under_cap_sentinel__\n")
    stage(repo, BASELINE)
    write(repo, BASELINE, "8\tbig.txt\n")  # restored, NOT staged

    staged = _run(runner, goh, repo, "--staged")
    assert staged.returncode == 1 and NO_CEILING in _out(staged), _out(staged)

    full = _run(runner, goh, repo, "--full")
    assert full.returncode == 0, _out(full)


@RUNNERS
def test_ratchet_clean_staged_change_passes_over_unstaged_growth(runner, repo, goh):
    _ceiling_repo(repo)
    write(repo, "small.txt", "x\n")
    stage(repo, "small.txt")
    write(repo, "big.txt", _lines(12))  # past its ceiling, NOT staged

    staged = _run(runner, goh, repo, "--staged")
    assert staged.returncode == 0, _out(staged)

    full = _run(runner, goh, repo, "--full")
    assert full.returncode == 1 and RATCHET in _out(full) and GREW in _out(full), _out(full)


@RUNNERS
def test_ratchet_staged_growth_fails_though_shrunk_in_the_tree(runner, repo, goh):
    _ceiling_repo(repo)
    write(repo, "big.txt", _lines(12))
    stage(repo, "big.txt")
    write(repo, "big.txt", _lines(8))  # back under, NOT staged

    staged = _run(runner, goh, repo, "--staged")
    assert staged.returncode == 1 and GREW in _out(staged), _out(staged)

    full = _run(runner, goh, repo, "--full")
    assert full.returncode == 0, _out(full)


@RUNNERS
def test_both_steps_run_at_the_repo_root_from_a_subdirectory(runner, repo, goh):
    """The ratchet's `[ -f "$GOH_LINE_BASELINE" ]` and the checkers' relative
    paths resolved against the CALLER's cwd, so a run from a subdirectory
    skipped the ratchet without a word. Growth must fail from anywhere."""
    _ceiling_repo(repo)
    write(repo, "big.txt", _lines(12))
    stage(repo, "big.txt")
    (repo / "sub").mkdir()
    for scope in ("--staged", "--full"):
        r = subprocess.run(["bash", str(REPO_ROOT / "gates" / "structural.sh"), scope],
                           cwd=repo / "sub", capture_output=True, text=True,
                           env=_env(runner, goh))
        assert r.returncode == 1 and GREW in _out(r), (scope, _out(r))


@RUNNERS
def test_a_set_git_work_tree_is_refused_not_silently_obeyed(runner, repo, goh):
    """GIT_WORK_TREE outranks the view's .git pointer, so git inside the view
    would read the working tree again. Refuse with the reason."""
    _ceiling_repo(repo)
    r = _run(runner, goh, repo, "--staged", GIT_WORK_TREE=str(repo))
    assert r.returncode == 2, _out(r)
    assert "GIT_WORK_TREE" in _out(r)


def _hooked_commit(runner: str, goh: Path, repo: Path, *args: str) -> subprocess.CompletedProcess:
    hooks = repo / ".hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text(f'#!/bin/bash\nexec bash "{REPO_ROOT}/gates/structural.sh" --staged\n')
    hook.chmod(0o755)
    return subprocess.run(
        ["git", "-c", "core.hooksPath=.hooks", "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-m", "c", *args],
        cwd=repo, capture_output=True, text=True, env=_env(runner, goh))


@RUNNERS
def test_a_real_commit_is_judged_on_its_index(runner, repo, goh):
    """Plain `git commit`: the hook gets GIT_INDEX_FILE=.git/index (relative)."""
    _ceiling_repo(repo)
    write(repo, ".gitignore", ".hooks/\n")
    commit_all(repo)
    write(repo, "big.txt", _lines(12))
    stage(repo, "big.txt")
    write(repo, "big.txt", _lines(8))
    refused = _hooked_commit(runner, goh, repo)
    assert refused.returncode != 0 and GREW in _out(refused), _out(refused)

    git(repo, "reset", "-q")
    write(repo, "small.txt", "x\n")
    stage(repo, "small.txt")
    write(repo, "big.txt", _lines(12))
    accepted = _hooked_commit(runner, goh, repo)
    assert accepted.returncode == 0, _out(accepted)


@RUNNERS
def test_a_partial_commit_is_judged_on_its_temporary_index(runner, repo, goh):
    """`git commit <path>` builds a temporary index from the named paths; the
    hook gets that (absolute) GIT_INDEX_FILE, and it is what the commit records."""
    _ceiling_repo(repo)
    write(repo, ".gitignore", ".hooks/\n")
    commit_all(repo)
    write(repo, "big.txt", _lines(12))
    write(repo, "small.txt", "x\n")
    stage(repo, "small.txt")
    refused = _hooked_commit(runner, goh, repo, "big.txt")
    assert refused.returncode != 0 and GREW in _out(refused), _out(refused)

    accepted = _hooked_commit(runner, goh, repo, "small.txt")
    assert accepted.returncode == 0, _out(accepted)
