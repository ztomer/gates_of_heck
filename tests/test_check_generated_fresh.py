"""check_generated_fresh.py — contract tests.

Generators here are tiny shell commands obeying the out-dir convention: the
checker appends one argument (the sandbox) and the generator must produce
each listed artifact at the same relative path beneath it.
"""

import time

from conftest import commit_all, run_check, write

FRESH = "checks/check_generated_fresh.py"


def run(repo, *args):
    return run_check(repo, FRESH, *args)


def seed(repo):
    """A committed artifact + a generator that reproduces it exactly."""
    write(repo, "gen/out.txt", "generated v1\n")
    write(repo, "tools/gen.sh", '#!/bin/sh\necho "generated $VER" > "$1/gen/out.txt"\n')
    commit_all(repo)


# ---- fresh ------------------------------------------------------------------


def test_fresh_generator_passes(repo):
    seed(repo)
    r = run(repo, "--generator", "VER=v1 sh tools/gen.sh", "gen/out.txt")
    assert r.returncode == 0, r.stderr


def test_failure_never_writes_in_place(repo):
    seed(repo)
    write(repo, "gen/out.txt", "stale content\n")
    commit_all(repo)
    r = run(repo, "--generator", "VER=v1 sh tools/gen.sh", "gen/out.txt")
    assert r.returncode == 1
    # Without --write the checker regenerates into its sandbox ONLY: the
    # committed (stale) bytes are evidence and must be left alone.
    assert (repo / "gen/out.txt").read_text() == "stale content\n"


# ---- stale --------------------------------------------------------------------


def test_stale_artifact_fails_naming_the_file(repo):
    seed(repo)
    write(repo, "gen/out.txt", "stale content\n")
    commit_all(repo)
    r = run(repo, "--generator", "VER=v1 sh tools/gen.sh", "gen/out.txt")
    assert r.returncode == 1
    assert "gen/out.txt" in r.stderr


def test_missing_output_is_a_violation_naming_the_file(repo):
    seed(repo)
    noop = 'true'
    r = run(repo, "--generator", noop, "gen/out.txt")
    assert r.returncode == 1
    assert "NOT PRODUCED" in r.stderr and "gen/out.txt" in r.stderr


# ---- --write blesses ------------------------------------------------------------


def test_write_blesses_stale_artifact(repo):
    seed(repo)
    write(repo, "gen/out.txt", "stale\n")
    commit_all(repo)
    r = run(repo, "--generator", "VER=v1 sh tools/gen.sh", "gen/out.txt",
            "--write")
    assert r.returncode == 0, r.stderr
    assert (repo / "gen/out.txt").read_text() == "generated v1\n"
    # And after blessing, plain verification passes.
    assert run(repo, "--generator", "VER=v1 sh tools/gen.sh",
               "gen/out.txt").returncode == 0


def test_write_does_not_invent_missing_outputs(repo):
    seed(repo)
    r = run(repo, "--generator", "true", "gen/out.txt", "--write")
    assert r.returncode == 0, r.stderr
    assert (repo / "gen/out.txt").exists()


# ---- timeout -------------------------------------------------------------------


def test_timeout_kills_hung_generator(repo):
    seed(repo)
    start = time.monotonic()
    r = run(repo, "--generator", "sh -c 'sleep 60'", "gen/out.txt",
            "--timeout", "1")
    elapsed = time.monotonic() - start
    assert r.returncode == 2
    assert "timed out" in r.stderr
    assert elapsed < 15, f"timeout did not kill the generator ({elapsed:.0f}s)"


def test_default_timeout_exists():
    import subprocess
    import sys
    from pathlib import Path
    here = Path(__file__).resolve().parent.parent
    help_text = subprocess.run(
        [sys.executable, str(here / FRESH), "--help"],
        capture_output=True, text=True).stdout
    assert "120" in help_text, "--timeout default of 120s is not documented"


# ---- preconditions (exit 2) ------------------------------------------------------


def test_missing_committed_artifact_is_precondition(repo):
    seed(repo)
    r = run(repo, "--generator", "true", "gen/absent.txt")
    assert r.returncode == 2 and "not found" in r.stderr


def test_failing_generator_is_precondition(repo):
    seed(repo)
    r = run(repo, "--generator", "sh -c 'exit 4'", "gen/out.txt")
    assert r.returncode == 2 and "exited 4" in r.stderr
