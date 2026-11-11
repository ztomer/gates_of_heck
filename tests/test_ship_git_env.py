"""goh ship's own git reads name the repo they ship; a caller's GIT_DIR answers for none.

GOH.SHIPGITDIR, 2026-11-11.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from conftest import git
from test_ship import _result, _ship, make_shipped


@pytest.fixture
def shipped(repo: Path, tmp_path: Path) -> Path:
    return make_shipped(repo, tmp_path)


def test_a_git_dir_in_the_callers_env_never_answers_for_the_shipped_repo(shipped, tmp_path):
    """GOH.SHIPGITDIR: a hook that starts `goh ship` exports GIT_DIR (and GIT_INDEX_FILE), and git
    ignores `-C` once GIT_DIR is set, so ship's OWN reads of the repo it names answered about the
    hook's repository: a dangling GIT_DIR refused the ship as "is not a git checkout", a live one
    named the other repo's HEAD. The reads go through `gitrun.run_reader(repo=)`, which drops git's
    local-env list; both shapes are shipped here."""
    other = tmp_path / "other"
    shutil.copytree(shipped, other, symlinks=True)
    git(other, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
        "-m", "other's own head")  # fmt: skip
    for leaked in (str(tmp_path / "gone" / ".git"), str(other / ".git")):
        (tmp_path / "ship.json").unlink(missing_ok=True)
        r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE", GIT_DIR=leaked,
                  GIT_INDEX_FILE=str(other / ".git" / "index"))  # fmt: skip
        assert "is not a git checkout" not in r.stderr + r.stdout, (leaked, r.stderr)
        res = _result(tmp_path)
        assert res["status"] == "ok", (leaked, res, r.stderr[-800:])
        assert res["sha"] == git(shipped, "rev-parse", "HEAD").strip(), (leaked, res)
