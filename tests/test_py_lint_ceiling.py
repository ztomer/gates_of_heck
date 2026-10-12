"""py_staged.sh with GOH_PY_LINT_CEILINGS: lint debt a repo carries is a ceiling per file that may
only fall (BACKLOG 4.4).

necrohand's commit gate refused a finished, fully tested commit over 69 ruff findings it did not
add, in files it touched; clearing the repo's 921 took 96 files. A ceiling lets that debt stand,
counted, while a commit adds none: a staged file may carry up to its ceiling, a file over it fails,
a file under it fails until the ceiling is written down, and a ceiling raised or deleted is a
finding. Unset, the gate is as strict as it was.
"""

import json
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT, commit_all, git, stage, write

GATE = REPO_ROOT / "gates" / "py_staged.sh"
SEED = REPO_ROOT / "gates" / "py_lint_ceiling.py"
TWO_FINDINGS = "import os\nimport sys\n"  # F401 twice, and formatted

pytestmark = pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")


def run(repo):
    return subprocess.run(
        ["/bin/bash", str(GATE), str(repo)], capture_output=True, text=True, check=False
    )


def ceilings(repo, values, *, commit=True):
    write(repo, ".gatesrc", "GOH_PY_LINT_CEILINGS=tools/py_lint_ceilings.json\n")
    write(repo, "tools/py_lint_ceilings.json", json.dumps(values) + "\n")
    if commit:
        commit_all(repo, "ceilings")


def test_a_file_at_its_ceiling_passes(repo):
    ceilings(repo, {"old.py": 2})
    write(repo, "old.py", TWO_FINDINGS)
    stage(repo, "old.py")
    r = run(repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_file_over_its_ceiling_fails_naming_it(repo):
    ceilings(repo, {"old.py": 1})
    write(repo, "old.py", TWO_FINDINGS)
    stage(repo, "old.py")
    r = run(repo)
    assert r.returncode != 0
    assert "old.py" in r.stdout + r.stderr and "F401" in r.stdout + r.stderr


def test_a_file_with_no_ceiling_carries_none(repo):
    ceilings(repo, {"old.py": 2})
    write(repo, "new.py", "import os\n")
    stage(repo, "new.py")
    r = run(repo)
    assert r.returncode != 0
    assert "new.py" in r.stdout + r.stderr


def test_a_file_under_its_ceiling_fails_until_it_is_written_down(repo):
    ceilings(repo, {"old.py": 3})
    write(repo, "old.py", TWO_FINDINGS)
    stage(repo, "old.py")
    r = run(repo)
    assert r.returncode != 0
    assert "old.py" in r.stdout + r.stderr and "2" in r.stdout + r.stderr
    write(repo, "tools/py_lint_ceilings.json", json.dumps({"old.py": 2}) + "\n")
    stage(repo, "tools/py_lint_ceilings.json")
    r = run(repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_raised_ceiling_is_a_finding(repo):
    ceilings(repo, {"old.py": 1})
    write(repo, "tools/py_lint_ceilings.json", json.dumps({"old.py": 2}) + "\n")
    write(repo, "old.py", TWO_FINDINGS)
    stage(repo, "tools/py_lint_ceilings.json", "old.py")
    r = run(repo)
    assert r.returncode != 0
    assert "raised" in r.stdout + r.stderr


def test_a_deleted_ceiling_file_is_a_finding_not_a_reset(repo):
    ceilings(repo, {"old.py": 2})
    git(repo, "rm", "-q", "tools/py_lint_ceilings.json")
    write(repo, "old.py", TWO_FINDINGS)
    stage(repo, "old.py")
    r = run(repo)
    assert r.returncode != 0
    assert "GOH_PY_LINT_CEILINGS" in r.stdout + r.stderr


def test_format_drift_still_fails_under_a_ceiling(repo):
    ceilings(repo, {"old.py": 1})
    write(repo, "old.py", "import os\nx   =   1\n")
    stage(repo, "old.py")
    r = run(repo)
    assert r.returncode != 0
    assert "format" in r.stdout + r.stderr


def test_seed_writes_every_tracked_files_count(repo):
    write(repo, "old.py", TWO_FINDINGS)
    write(repo, "clean.py", "x = 1\n")
    commit_all(repo, "debt")
    out = repo / "tools" / "py_lint_ceilings.json"
    r = subprocess.run(
        ["python3", str(SEED), "--seed", str(out)], cwd=repo, capture_output=True, text=True,
        check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(out.read_text()) == {"old.py": 2}
