"""The commit's lint judges what the change ADDS (GOH.LINTDELTA).

py_lint_ceiling.py judged whole files against a count: a file whose findings predate the change
(a ruff bump, a rule the house set gained) was red for every lane that touched it -- the faults
harvest counted 65 lane reports re-naming such findings -- while a NEW finding in a file with room
under its ceiling (or one that replaced a finding the change fixed) passed: hooks/claude/
discard_guard.py:36 went in at 102 characters, an E501 the commit gate never flagged. At commit, a
file HEAD carries is now judged by the delta against HEAD's own findings: any finding the change
adds is red whatever the ceiling, and findings that predate it are one counted line, not a failure.
"""

import json
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT, commit_all, stage, write

GATE = REPO_ROOT / "gates" / "py_staged.sh"
TWO_FINDINGS = "import os\nimport sys\n"  # F401 twice, and formatted
LONG = 'X = "' + "y" * 110 + '"\n'

pytestmark = pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")


def run(repo):
    return subprocess.run(["/bin/bash", str(GATE), str(repo)], capture_output=True, text=True,
                          check=False, stdin=subprocess.DEVNULL)  # fmt: skip


def committed(repo, text: str, ceiling: int) -> None:
    write(repo, ".gatesrc", "GOH_PY_LINT_CEILINGS=tools/py_lint_ceilings.json\n")
    write(repo, "tools/py_lint_ceilings.json", json.dumps({"old.py": ceiling}) + "\n")
    write(repo, "old.py", text)
    commit_all(repo, "old.py and its ceiling")


def test_a_change_that_adds_no_finding_passes_whatever_the_file_carried(repo):
    committed(repo, TWO_FINDINGS + "\n\nVALUE = 1\n", 0)  # over a ceiling of 0: a ruff bump's drift
    write(repo, "old.py", TWO_FINDINGS + "\n\nVALUE = 2\n")
    stage(repo, "old.py")
    r = run(repo)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "ruff findings predate this change" in out, out


def test_a_new_long_line_in_a_touched_file_is_red_under_its_ceiling(repo):
    committed(repo, TWO_FINDINGS, 2)
    write(repo, "old.py", "import os\n" + LONG)  # one F401 fixed, one E501 added: still 2
    stage(repo, "old.py")
    r = run(repo)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "E501" in out and "this change adds" in out, out
    assert "F401" not in out.split("this change adds", 1)[1].split("reproduce", 1)[0], out


def test_a_finding_on_an_unchanged_line_the_change_caused_is_red(repo):
    committed(repo, "import os\n\nprint(os.sep)\n", 0)
    write(repo, "old.py", "import os\n")  # the use deleted: F401 on a line the change kept
    stage(repo, "old.py")
    r = run(repo)
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "F401" in out, out


def test_discard_guards_line_36_is_fixed():
    over = [n for n, ln in enumerate((REPO_ROOT / "hooks/claude/discard_guard.py").read_text()
                                     .splitlines(), 1) if len(ln) > 100]  # fmt: skip
    assert over == [], over
