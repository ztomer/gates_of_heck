"""The stdin gate's scope verdicts: an exclusion that hides every Python file is red and named at
full scope, a commit staging only excluded Python is not applicable, and the call floor goes red
when the scan stops seeing calls. And the switch (BACKLOG 3.4): a repo that never wrote the key
runs the step in the pipeline, so its first undeclared call is red at both scopes, and only a
written `GOH_SUBPROCESS_STDIN=off` skips it."""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, commit_all, hermetic_env, run_goh, stage, write

LABEL = "every child process names its stdin"

BAD = "import subprocess\nsubprocess.run(['ls'])\n"

GOOD3 = "import subprocess\n" + "".join(
    f"subprocess.run(['echo', '{i}'], stdin=subprocess.DEVNULL)\n" for i in range(3)
)


def test_an_exclusion_that_removes_every_python_file_is_red_and_named(repo: Path) -> None:
    """Blind, not "no Python here": the exclusion in effect is named."""
    write(repo, "a.py", GOOD3)
    commit_all(repo)
    assert run_goh(repo, "subprocess-stdin", "--exclude", "").returncode == 0
    blind = run_goh(repo, "subprocess-stdin", "--exclude", r"\.py$")
    assert blind.returncode == 1, blind.stdout
    assert "read NONE of them" in blind.stdout, blind.stdout
    assert r"GOH_EXCLUDE is '\.py$'" in blind.stdout, blind.stdout


def test_the_floor_goes_red_when_the_scanner_stops_seeing_calls(repo: Path) -> None:
    """The break-prove: the tree read whole meets its floor; with the file holding the calls
    excluded, the scan still reads Python but sees too few calls, and is red."""
    write(repo, ".gatesrc", "GOH_SUBPROCESS_STDIN_MIN_CALLS=3\n")
    write(repo, "a.py", GOOD3)
    write(repo, "b.py", "x = 1\n")
    commit_all(repo)
    seen = run_goh(repo, "subprocess-stdin", "--exclude", "")
    assert seen.returncode == 0, seen.stdout + seen.stderr
    lost = run_goh(repo, "subprocess-stdin", "--exclude", "^a\\.py$")
    assert lost.returncode == 1, lost.stdout
    assert "declares a floor of 3" in lost.stdout, lost.stdout


def test_a_commit_staging_only_excluded_python_is_not_applicable(repo: Path) -> None:
    write(repo, "a.py", GOOD3)
    commit_all(repo)
    write(repo, "vendor/x.py", BAD)
    stage(repo, "vendor/x.py")
    r = run_goh(repo, "subprocess-stdin", "--staged", "--exclude", "^vendor/")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "every staged *.py is excluded" in r.stdout, r.stdout


def _structural(goh: Path, repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(goh), "structural", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        stdin=subprocess.DEVNULL,
    )


def test_a_repo_that_never_wrote_the_key_is_red_at_both_scopes(goh: Path, repo: Path) -> None:
    """The flip's red-first: no `GOH_SUBPROCESS_STDIN` in `.gatesrc`, one undeclared call."""
    write(repo, "a.py", BAD)
    commit_all(repo)
    full = _structural(goh, repo, "--full")
    assert full.returncode == 1, full.stdout + full.stderr
    assert f"structural: {LABEL}" in full.stderr, full.stderr
    write(repo, "b.py", BAD)
    stage(repo, "b.py")
    staged = _structural(goh, repo, "--staged")
    assert staged.returncode == 1, staged.stdout + staged.stderr
    assert f"structural: {LABEL} (staged)" in staged.stderr, staged.stderr


def test_only_a_written_off_skips_the_step(goh: Path, repo: Path) -> None:
    write(repo, ".gatesrc", "GOH_SUBPROCESS_STDIN=off\n")
    write(repo, "a.py", BAD)
    commit_all(repo)
    r = _structural(goh, repo, "--full")
    assert LABEL not in r.stdout + r.stderr, r.stdout + r.stderr


def test_an_unset_key_over_python_excluded_whole_still_says_it_read_none(
    goh: Path, repo: Path
) -> None:
    """The flip must not turn the blind verdict into a silent pass in a repo that never opted in."""
    write(repo, ".gatesrc", "GOH_EXCLUDE='\\.py$'\n")
    write(repo, "a.py", GOOD3)
    commit_all(repo)
    r = _structural(goh, repo, "--full")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "read NONE of them" in r.stdout + r.stderr, r.stdout + r.stderr
