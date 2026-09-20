"""py_staged.sh — the cheap half of py_gate at commit time, on staged .py only.

Three directions: a staged ruff finding fails, a staged format drift fails, a
clean staged file passes -- and an UNSTAGED bad file is out of scope (it is
the push gate's), as is a commit with nothing Python staged.
"""

import shutil
import subprocess

import pytest

from conftest import REPO_ROOT, stage, write

GATE = REPO_ROOT / "gates" / "py_staged.sh"

pytestmark = pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")


def run(repo):
    return subprocess.run(["/bin/bash", str(GATE), str(repo)], capture_output=True, text=True)


def test_nothing_python_staged_passes(repo):
    write(repo, "notes.txt", "hi\n")
    stage(repo, "notes.txt")
    r = run(repo)
    assert r.returncode == 0, r.stderr


def test_clean_staged_file_passes(repo):
    write(repo, "ok.py", "x = 1\n")
    stage(repo, "ok.py")
    r = run(repo)
    assert r.returncode == 0, r.stderr


def test_staged_ruff_finding_fails(repo):
    write(repo, "bad.py", "import os\n")  # F401: unused import
    stage(repo, "bad.py")
    r = run(repo)
    assert r.returncode != 0
    assert "F401" in r.stdout + r.stderr


def test_staged_format_drift_fails(repo):
    write(repo, "drift.py", "x   =   1\n")
    stage(repo, "drift.py")
    r = run(repo)
    assert r.returncode != 0
    assert "drift.py" in r.stdout + r.stderr


def test_unstaged_bad_file_is_not_this_gates_business(repo):
    write(repo, "bad.py", "import os\n")
    write(repo, "ok.py", "x = 1\n")
    stage(repo, "ok.py")
    r = run(repo)
    assert r.returncode == 0, r.stderr
