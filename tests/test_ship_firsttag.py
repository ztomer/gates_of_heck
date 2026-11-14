"""A tagless repo's first `goh ship` never hands its release kit an empty version (GOH.FIRSTTAG).

agent-feed (AF.CORE, 2026-11-12): `next_version` returned "" when the repo had no earlier v-tag, so
`goh ship --bump patch` ran the release with `{version}` as nothing. There is no earlier version to
bump, and 0.0.0 + patch is a guess about what the first release is called -- so the rule is: a
first release NAMES its version. With a release kit declared and no tag, --bump (or no version
at all) is refused before anything starts, naming `--version X.Y.Z`; --version ships it; a repo
that declares no release kit still ships with no version, as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import git
from test_ship import _result, _ship


@pytest.fixture
def tagless(repo: Path, tmp_path: Path) -> Path:
    """`repo` with a bare `origin` it tracks, NO tag, and one commit on top."""
    remote = tmp_path / "remote.git"
    git(repo, "clone", "-q", "--bare", str(repo), str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    (repo / "f.txt").write_text("change\n")
    git(repo, "add", "f.txt")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "feat: first")
    (tmp_path / "loopctl.py").write_text("import sys\nsys.exit(0)\n")
    return repo


RECORD = ('printf "[%s]" "$GOH_SHIP_VERSION" > "$SHIP_SAW" && git tag "v$GOH_SHIP_VERSION" '
          '&& git push -q origin "v$GOH_SHIP_VERSION"')  # fmt: skip


@pytest.mark.parametrize(
    "args", [("--bump", "patch"), ("--bump", "minor"), ("--bump", "major"), ()]
)
def test_a_first_ship_without_a_version_is_refused_naming_version(tagless, tmp_path, args):
    saw = tmp_path / "saw"
    r = _ship(tagless, tmp_path, "--wait", *args, GOH_SHIP_RELEASE=RECORD, SHIP_SAW=str(saw))
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "--version X.Y.Z" in out and "no v-tag" in out, out
    assert not saw.exists(), f"the release ran with version {saw.read_text()!r}"
    assert not (tmp_path / "ship.json").exists(), "a refused ship started"


def test_a_first_ship_with_a_version_releases_it(tagless, tmp_path):
    saw = tmp_path / "saw"
    r = _ship(tagless, tmp_path, "--wait", "--version", "0.1.0", GOH_SHIP_RELEASE=RECORD,
              SHIP_SAW=str(saw))  # fmt: skip
    assert r.returncode == 0, (r.stdout + r.stderr, _result(tmp_path)["tail"])
    assert saw.read_text() == "[0.1.0]"
    assert _result(tmp_path)["version"] == "0.1.0"


def test_a_repo_with_no_release_kit_still_ships_with_no_version(tagless, tmp_path):
    r = _ship(tagless, tmp_path, "--wait", GOH_SHIP_RELEASE="")
    assert r.returncode == 0, r.stdout + r.stderr
    res = _result(tmp_path)
    assert res["version"] == "" and ("release", "skipped") in [
        (s["name"], s["status"]) for s in res["steps"]
    ]
