"""A release that fails after its bump leaves the checkout as the ship found it (GOH.SHIPUNDO).

ztools (LM.AUTOSWITCH, 2026-11-12): goh ship's release step committed `chore: version 3.28.0`, then
failed at the pre-push gate, and the bump stayed on main, untagged and unpushed; it was harmless
only because ztools' kit skips a bump Cargo already carries. Now, when the release step fails, the
commits it made and that no remote holds are taken back (`git reset --keep` to the hand-off: it
refuses rather than overwrite an edit), with the version tags it cut on them; a commit a remote
holds is never reset, and the result says what was left. The next ship then starts clean.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import git
from test_ship import _result, _ship, make_shipped


@pytest.fixture
def shipped(repo: Path, tmp_path: Path) -> Path:
    return make_shipped(repo, tmp_path)


BUMP = ('printf "%s\\n" "$GOH_SHIP_VERSION" > VERSION && git add VERSION && '
        'git -c user.name=t -c user.email=t@t commit -q -m "chore: version $GOH_SHIP_VERSION" && '
        'git tag -a "v$GOH_SHIP_VERSION" -m "v$GOH_SHIP_VERSION"')  # fmt: skip
PUSH = 'git push -q origin HEAD:refs/heads/main "v$GOH_SHIP_VERSION"'


def test_a_release_failing_after_its_bump_leaves_no_version_commit(shipped, tmp_path):
    handoff = git(shipped, "rev-parse", "HEAD").strip()
    r = _ship(shipped, tmp_path, "--wait", "--bump", "minor",
              GOH_SHIP_RELEASE=f"{BUMP} && echo 'pre-push gate: red' && exit 1")  # fmt: skip
    assert r.returncode == 1, r.stdout + r.stderr
    res = _result(tmp_path)
    assert res["failed_step"] == "release"
    assert git(shipped, "rev-parse", "HEAD").strip() == handoff, "the version commit stayed"
    assert not git(shipped, "tag", "-l", "v1.1.0").strip(), "the release's tag stayed"
    assert not (shipped / "VERSION").exists()
    assert git(shipped, "status", "--porcelain").strip() == ""
    assert any("undone" in ln for ln in res["undo"]), res["undo"]
    # ...and the next ship is clean: the same version, released.
    r = _ship(shipped, tmp_path, "--wait", "--bump", "minor", GOH_SHIP_RELEASE=f"{BUMP} && {PUSH}")
    assert r.returncode == 0, r.stdout + r.stderr
    assert git(shipped, "ls-remote", "origin", "refs/tags/v1.1.0").strip()


def test_a_bump_a_remote_holds_is_never_reset(shipped, tmp_path):
    r = _ship(shipped, tmp_path, "--wait", "--bump", "minor",
              GOH_SHIP_RELEASE=f"{BUMP} && {PUSH} && exit 1")  # fmt: skip
    assert r.returncode == 1, r.stdout + r.stderr
    pushed = git(shipped, "ls-remote", "origin", "refs/heads/main").split()[0]
    assert git(shipped, "rev-parse", "HEAD").strip() == pushed
    assert git(shipped, "tag", "-l", "v1.1.0").strip()
    assert any("on origin" in ln for ln in _result(tmp_path)["undo"]), _result(tmp_path)["undo"]


def test_a_release_that_fails_before_committing_changes_nothing(shipped, tmp_path):
    handoff = git(shipped, "rev-parse", "HEAD").strip()
    r = _ship(shipped, tmp_path, "--wait", "--bump", "minor", GOH_SHIP_RELEASE="exit 1")
    assert r.returncode == 1
    assert git(shipped, "rev-parse", "HEAD").strip() == handoff
    assert _result(tmp_path)["undo"] == []


def test_an_edit_to_a_file_the_bump_changed_is_never_overwritten(shipped, tmp_path):
    """`reset --keep` refuses rather than lose a working-tree edit; the result says so."""
    r = _ship(shipped, tmp_path, "--wait", "--bump", "minor",
              GOH_SHIP_RELEASE=f"{BUMP} && echo edited >> VERSION && exit 1")  # fmt: skip
    assert r.returncode == 1
    assert (shipped / "VERSION").read_text() == "1.1.0\nedited\n"
    assert any("refused" in ln for ln in _result(tmp_path)["undo"]), _result(tmp_path)["undo"]
