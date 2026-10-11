"""The stdin gate's scope verdicts: an exclusion that hides every Python file is red and named at
full scope, a commit staging only excluded Python is not applicable, and the call floor goes red
when the scan stops seeing calls."""

from __future__ import annotations

from pathlib import Path

from conftest import commit_all, run_goh, stage, write

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
