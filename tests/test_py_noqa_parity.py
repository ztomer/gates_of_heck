"""py_staged.sh and py_gate.sh with GOH_PY_NOQA_PARITY: a `# noqa` ruff honours and a second linter
the repo runs ignores is a finding (BACKLOG 4.5).

necrohand's `# noqa: F401` quieted ruff while its own pyflakes gate still named four unused imports in
a file the sweep had just cleaned: pyflakes reads no `# noqa`. A repo that names its second linter in
the key gets each such line reported, with the linter that still sees it; a directive the second
linter has nothing to say about stands, and without the key nothing changes.
"""

import shutil
import subprocess

import pytest
from conftest import REPO_ROOT, commit_all, stage, write

STAGED = REPO_ROOT / "gates" / "py_staged.sh"
PARITY = REPO_ROOT / "gates" / "py_noqa_parity.py"
SILENCED = "import os  # noqa: F401\n"  # ruff honours it; pyflakes still reports the import

pytestmark = pytest.mark.skipif(
    shutil.which("ruff") is None
    or subprocess.run(
        ["python3", "-m", "pyflakes", "--version"], capture_output=True, check=False
    ).returncode,
    reason="ruff or pyflakes not available",
)


def run(repo):
    return subprocess.run(
        ["/bin/bash", str(STAGED), str(repo)], capture_output=True, text=True, check=False
    )


def keyed(repo, value="pyflakes"):
    write(repo, ".gatesrc", f"GOH_PY_NOQA_PARITY={value}\n")
    commit_all(repo, "key")


def test_a_noqa_the_second_linter_ignores_is_named_with_that_linter(repo):
    keyed(repo)
    write(repo, "mod.py", SILENCED)
    stage(repo, "mod.py")
    r = run(repo)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "mod.py:1" in out and "pyflakes" in out and "noqa: F401" in out, out


def test_a_noqa_the_second_linter_has_nothing_to_say_about_stands(repo):
    write(repo, "ruff.toml", '[lint]\nextend-select = ["E741"]\n')  # so ruff uses the directive
    keyed(repo)
    write(repo, "mod.py", "l = 1  # noqa: E741\n")
    stage(repo, "mod.py")
    r = run(repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_without_the_key_nothing_changes(repo):
    write(repo, "mod.py", SILENCED)
    stage(repo, "mod.py")
    r = run(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "noqa parity" not in r.stdout + r.stderr


def test_a_linter_it_cannot_read_is_refused_naming_the_key(repo):
    keyed(repo, "pylint")
    write(repo, "mod.py", "X = 1\n")
    stage(repo, "mod.py")
    r = run(repo)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "GOH_PY_NOQA_PARITY" in out and "pylint" in out, out


def test_the_push_scope_reads_every_tracked_file(repo):
    write(repo, "clean.py", "X = 1\n")
    write(repo, "pkg/mod.py", SILENCED)
    commit_all(repo, "two files")
    r = subprocess.run(
        ["python3", str(PARITY), "--linters", "pyflakes", "--all"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "pkg/mod.py:1" in out and "clean.py" not in out, out
