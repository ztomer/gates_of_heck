"""checks/check_python_formatted.py — the repo's Python is ruff-formatted.

The class: a Python tree with no formatter has its shape decided by whichever
hand last edited each file, and the damage that causes (a comment merged onto a
`continue`, a statement de-indented out of its loop) parses and reads fine, so
only a tool can catch it.

So the gate must go RED on a mis-formatted file, GREEN on a formatted one, and
RED-with-a-reason when ruff is missing — a formatter that is not installed is a
missing gate, not a pass. It must also not FORMAT: the checker answers a
question, so "the gate went red" and "somebody edited 100 files" stay different
events. And the answer must come from the repo's own ruff settings, which means
the settings file that applies is the one at the repo root — a formatter whose
verdict depends on the directory it was launched from is not a gate.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, commit_all, run_check, stage, write

CHECK = "checks/check_python_formatted.py"

BAD = "x = {  'a':1,'b':2 }\n"
GOOD = 'x = {"a": 1, "b": 2}\n'

needs_ruff = pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff is not installed")


@needs_ruff
def test_misformatted_file_is_red_and_formatted_is_green(repo):
    write(repo, "tools/bad.py", BAD)
    write(repo, "tools/good.py", GOOD)
    bad = run_check(repo, CHECK, "tools")
    assert bad.returncode == 1, bad.stdout + bad.stderr
    assert "not ruff-formatted" in bad.stdout + bad.stderr

    (repo / "tools" / "bad.py").unlink()
    good = run_check(repo, CHECK, "tools")
    assert good.returncode == 0, good.stdout + good.stderr


@needs_ruff
def test_a_named_tree_does_not_judge_the_rest_of_the_repo(repo):
    """The tree list is a scope, not a suggestion: an unformatted file OUTSIDE it
    must not decide the answer, or the gate is only as narrow as whoever listed
    the trees — and a rename that moves a file out of the list silences it."""
    write(repo, "tools/good.py", GOOD)
    write(repo, "scratch/bad.py", BAD)
    only_tools = run_check(repo, CHECK, "tools")
    assert only_tools.returncode == 0, only_tools.stdout + only_tools.stderr

    whole_repo = run_check(repo, CHECK)
    assert whole_repo.returncode == 1, whole_repo.stdout + whole_repo.stderr


@needs_ruff
def test_missing_ruff_is_a_failure_not_a_skip(repo, monkeypatch, tmp_path):
    """A formatter that is not installed cannot check anything, and a gate that
    passes because its tool was missing has reported success it never earned.

    PATH keeps the interpreter and loses only ruff — an empty PATH would fail
    this test by hiding `python3` too, which proves nothing about the gate.
    """
    write(repo, "tools/good.py", GOOD)
    narrow = tmp_path / "bin"
    narrow.mkdir()
    (narrow / "python3").symlink_to(Path(sys.executable))
    monkeypatch.setenv("PATH", str(narrow))
    assert shutil.which("ruff") is None, (
        "ruff is still reachable; the control is not testing itself"
    )
    out = run_check(repo, CHECK, "tools")
    assert out.returncode == 1, out.stdout + out.stderr
    assert "ruff is not installed" in out.stdout + out.stderr


def test_selftest_gates_on_its_own_detection():
    """The calibration must run without a repo of its own — it plants both files
    in a temp directory and asks the real ruff binary about them."""
    out = subprocess.run(
        ["python3", str(REPO_ROOT / CHECK), "--selftest"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "mis-formatted file is caught" in out.stdout + out.stderr


@needs_ruff
def test_the_gate_does_not_reformat_anything(repo):
    """It answers a question. Applying the formatter is the repo's own `fmt`
    target, so a red gate and an edited tree are never the same event."""
    write(repo, "tools/bad.py", BAD)
    before = (repo / "tools" / "bad.py").read_text()
    run_check(repo, CHECK, "tools")
    assert (repo / "tools" / "bad.py").read_text() == before


# ── --staged: the commit's own bytes, and only its files ─────────────────────
# The v0.20.0 push was refused for two unformatted test files that pre-commit had
# let through, because the step ran at full scope only. A check over the INDEX
# blobs of the staged .py files reformats nothing, so the reason for keeping it
# out of pre-commit ("a hook that reformatted the repository") never applied.


@needs_ruff
def test_staged_judges_the_index_blob_not_the_worktree(repo):
    """Index truth: the staged bytes are what the commit will contain. A worktree
    fixed after `git add` must not launder a misformatted commit, and a worktree
    broken after `git add` must not refuse a well-formed one."""
    write(repo, "tools/a.py", BAD)
    stage(repo, "tools/a.py")
    write(repo, "tools/a.py", GOOD)
    red = run_check(repo, CHECK, "--staged")
    assert red.returncode == 1, red.stdout + red.stderr
    assert "tools/a.py" in red.stdout + red.stderr

    stage(repo, "tools/a.py")
    write(repo, "tools/a.py", BAD)
    green = run_check(repo, CHECK, "--staged")
    assert green.returncode == 0, green.stdout + green.stderr


@needs_ruff
def test_staged_does_not_judge_files_outside_the_commit(repo):
    """An unformatted file that is not part of this commit is the full-scope
    gate's business: refusing a commit over a file it does not touch is the
    pre-commit hook nobody can satisfy."""
    write(repo, "scratch/bad.py", BAD)
    commit_all(repo)
    write(repo, "tools/good.py", GOOD)
    stage(repo, "tools/good.py")
    out = run_check(repo, CHECK, "--staged")
    assert out.returncode == 0, out.stdout + out.stderr


@needs_ruff
def test_staged_with_no_python_is_a_named_non_run(repo):
    write(repo, "notes.md", "# notes\n")
    stage(repo, "notes.md")
    out = run_check(repo, CHECK, "--staged")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "not applicable" in out.stdout + out.stderr


@needs_ruff
def test_staged_honours_the_repos_own_excludes(repo):
    """ruff resolves settings from the stdin filename, but an explicitly named
    path skips its excludes unless forced: a repo that excludes a generated tree
    must not be refused over it at commit time when the full gate allows it."""
    write(repo, "pyproject.toml", '[tool.ruff]\nextend-exclude = ["gen"]\n')
    write(repo, "gen/out.py", BAD)
    stage(repo, "pyproject.toml", "gen/out.py")
    out = run_check(repo, CHECK, "--staged")
    assert out.returncode == 0, out.stdout + out.stderr
    full = run_check(repo, CHECK)
    assert full.returncode == 0, full.stdout + full.stderr


@needs_ruff
def test_staged_missing_ruff_is_a_failure_not_a_skip(repo, monkeypatch, tmp_path):
    write(repo, "tools/good.py", GOOD)
    stage(repo, "tools/good.py")
    narrow = tmp_path / "bin"
    narrow.mkdir()
    (narrow / "python3").symlink_to(Path(sys.executable))
    (narrow / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(narrow))
    out = run_check(repo, CHECK, "--staged")
    assert out.returncode == 1, out.stdout + out.stderr
    assert "ruff is not installed" in out.stdout + out.stderr
