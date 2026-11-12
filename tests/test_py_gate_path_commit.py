"""A `git commit -- <paths>` judges the tree it records, not the index beside it (GOH.STAGEDNEW).

On 2026-11-11 a `.loop` commit of named paths, with an unrelated NEW file staged in the index, was
refused by `py_lint_ceiling.py` with E902 "No such file or directory" on the new file: the commit
gate judged an export of the commit's own (temporary) index, but `py_gate.sh` listed its files from
the repository's index, which held a file that export did not. The list now comes from the index
being committed (`goh_bind_hook_index`), and a ceiling failure names the command that reproduces it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT, commit_all, git, hermetic_env, write

pytestmark = pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")

# The shape of .loop's commit gate: export the index being committed, run py_gate.sh inside it.
HOOK = """#!/usr/bin/env bash
. "{goh}/gates/_git_env.sh"
goh_hook_unbind || exit $?
view="$( . "{goh}/gates/_common.sh"; export GOH_NAME="fixture index" GOH_REPO_ROOT="$PWD"
         goh_index_view . >&2 && printf '%s' "$GOH_INDEX_VIEW")"
[ -n "$view" ] && [ -d "$view" ] || exit 9
cd "$view" && bash "{goh}/gates/py_gate.sh" .
"""


def _fixture(repo: Path) -> None:
    write(repo, ".gatesrc", "GOH_PY_LINT_CEILINGS=ceilings.json\n")
    write(repo, "ceilings.json", json.dumps({}) + "\n")
    write(repo, "b.py", "x = 1\n")
    commit_all(repo, "base")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(HOOK.format(goh=REPO_ROOT))
    hook.chmod(0o755)


def _commit(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "t", *args],
        cwd=repo, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
        env=hermetic_env(drop_git=True, GOH_LIVE="1"),
    )  # fmt: skip


def test_a_path_commit_beside_a_staged_new_file_lints_the_tree_it_records(repo: Path) -> None:
    _fixture(repo)
    write(repo, "a.py", "y = 2\n")
    git(repo, "add", "a.py")  # staged, NOT part of the commit below
    write(repo, "b.py", "x = 3\n")
    out = _commit(repo, "--", "b.py")
    text = out.stdout + out.stderr
    assert "E902" not in text, text
    assert "✓ ruff check under ceilings.json" in text, text


def test_the_committed_tree_is_still_judged(repo: Path) -> None:
    """The other direction: a finding in the file the path commit records is red."""
    _fixture(repo)
    write(repo, "a.py", "y = 2\n")
    git(repo, "add", "a.py")
    write(repo, "b.py", "import os\nx = 3\n")
    out = _commit(repo, "--", "b.py")
    text = out.stdout + out.stderr
    assert out.returncode != 0 and "F401" in text, text
    assert "E902" not in text, text


def test_a_ceiling_failure_names_the_command_that_reproduces_it(repo: Path) -> None:
    write(repo, "ceilings.json", "{}\n")
    write(repo, "b.py", "import os\nx = 1\n")
    commit_all(repo, "base")
    ran = subprocess.run(
        [os.environ.get("GOH_PY", "python3"), str(REPO_ROOT / "gates" / "py_lint_ceiling.py"),
         "--check", "--ceilings", "ceilings.json", "--runner", "python3 -m", "--", "b.py"],
        cwd=repo, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
        env=hermetic_env(drop_git=True, GOH_LIVE="1"),
    )  # fmt: skip
    assert ran.returncode == 1, ran.stdout + ran.stderr
    assert (
        'reproduce: python3 "$GOH_DIR/gates/py_lint_ceiling.py" --check --ceilings ceilings.json '
        "--runner 'python3 -m' -- b.py"
    ) in ran.stderr, ran.stderr
    assert "plain `ruff check`" in ran.stderr, ran.stderr
