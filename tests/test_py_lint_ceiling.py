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
        ["/bin/bash", str(GATE), str(repo)],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
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
        check=False, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(out.read_text()) == {"old.py": 2}


# THE HOUSE SET (RUFF.RULES, gates/ruff_house.py). koffee_big's ruff.toml selected PLW1510 alone, so
# its `ruff check` read as a lint gate and checked one rule. A finding from the house set is a
# finding under ceilings whatever the repo selects: B006 (a mutable default) is outside both
# PLW1510 and ruff's defaults, so only the house set can name it.
ONE_RULE = '[lint]\nselect = ["PLW1510"]\n'
MUTABLE_DEFAULT = "def f(x=[]):\n    return x\n"


def test_a_planted_house_finding_is_red_whatever_the_repo_selects(repo):
    write(repo, "ruff.toml", ONE_RULE)
    ceilings(repo, {})
    write(repo, "new.py", MUTABLE_DEFAULT)
    stage(repo, "new.py")
    r = run(repo)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "B006" in r.stdout + r.stderr
    # Calibration: the repo's own config alone passes this file -- the red is the house set's.
    plain = subprocess.run(
        ["ruff", "check", "--no-cache", "new.py"], cwd=repo, capture_output=True, text=True,
        check=False, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert plain.returncode == 0, plain.stdout


def test_a_repo_ignore_cannot_subtract_a_house_rule(repo):
    write(repo, "ruff.toml", '[lint]\nignore = ["B006"]\n')
    ceilings(repo, {})
    write(repo, "new.py", MUTABLE_DEFAULT)
    stage(repo, "new.py")
    r = run(repo)
    assert r.returncode != 0 and "B006" in r.stdout + r.stderr


def test_seed_counts_the_house_set(repo):
    write(repo, "ruff.toml", ONE_RULE)
    write(repo, "old.py", MUTABLE_DEFAULT)
    commit_all(repo, "debt")
    out = repo / "tools" / "py_lint_ceilings.json"
    r = subprocess.run(
        ["python3", str(SEED), "--seed", str(out)], cwd=repo, capture_output=True, text=True,
        check=False, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(out.read_text()) == {"old.py": 1}


def test_a_ceiling_for_a_file_that_is_gone_is_stale(repo):
    ceilings(repo, {"old.py": 2, "gone.py": 1})
    write(repo, "old.py", TWO_FINDINGS)
    stage(repo, "old.py")
    r = run(repo)
    assert r.returncode != 0
    assert "gone.py" in r.stdout + r.stderr and "not in this commit" in r.stdout + r.stderr


def test_unadopted_the_floor_holds_and_the_house_debt_is_named(repo):
    write(repo, "ruff.toml", ONE_RULE)
    write(repo, "new.py", MUTABLE_DEFAULT)
    stage(repo, "ruff.toml", "new.py")
    r = run(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (
        "house ruff set" in r.stdout + r.stderr
        and "not adopted: 1 finding here" in r.stdout + r.stderr
    )


def test_commit_with_paths_reads_the_ceiling_it_commits(repo):
    """`git commit -- <paths>` commits a TEMPORARY index only GIT_INDEX_FILE names (GOH.HOOKREFRESH,
    the skills repo, 2026-11-13). The ceiling written down in the working tree and committed by
    path never reached the repository's own index, and the gate read THAT one: the commit was
    refused for being under a ceiling it was lowering. The hook below is the stock hook's shape --
    carry the index, unbind, run the staged gate -- and the commit is a real one."""
    write(repo, "old.py", TWO_FINDINGS + "import json\n")
    ceilings(repo, {"old.py": 3})
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/bash\n"
        f'. "{REPO_ROOT}/gates/_git_env.sh"; goh_hook_unbind || exit $?\n'
        f'exec bash "{REPO_ROOT}/gates/py_staged.sh"\n'
    )
    hook.chmod(0o755)
    write(repo, "old.py", TWO_FINDINGS)
    write(repo, "tools/py_lint_ceilings.json", json.dumps({"old.py": 2}) + "\n")
    r = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-m",
            "lower the ceiling",
            "--",
            "old.py",
            "tools/py_lint_ceilings.json",
        ],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    # ...and the other direction: a ceiling RAISED by path is still seen, from the same index.
    write(repo, "tools/py_lint_ceilings.json", json.dumps({"old.py": 3}) + "\n")
    write(repo, "old.py", TWO_FINDINGS + "import json\n")
    r = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-m",
            "raise it",
            "--",
            "old.py",
            "tools/py_lint_ceilings.json",
        ],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode != 0 and "raised 2 -> 3" in r.stdout + r.stderr, r.stdout + r.stderr
