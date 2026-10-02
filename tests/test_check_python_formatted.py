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
from conftest import REPO_ROOT, run_check, write

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
