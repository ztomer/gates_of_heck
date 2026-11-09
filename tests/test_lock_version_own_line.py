"""`goh lock-version` on a private nested workspace's shapes (BACKLOG 6.18).

A line regex read `version = "1.9.0"  # ...` (crates/app-legacy/Cargo.toml:3) as no version, so
the crate read as INHERITING the workspace's 4.8.0 and its correct lock entry as stale; and arm 2
compared `VERSION` against a crate that keeps its own version line. The manifests are now read as
TOML, and arm 2 compares the release only against the members that carry it.
"""

import pytest
from conftest import write
from test_check_lock_version import audit
from tier_kit import both_tiers  # noqa: F401  # a fixture

pytestmark = pytest.mark.usefixtures("both_tiers")

OWN_VERSION = (
    '[package]\nname = "app-legacy"\n'
    'version = "1.9.0"  # this crate keeps its own version line (the CHANGELOG\'s); '
    "the shell runs `app legacy` since D.5\n"
    'edition = "2021"\n'
)


def _zinc(repo):
    """A private nested workspace at a66eb253: VERSION and `[workspace.package]` say 4.8.0, `app-cli` inherits
    it, and `crates/app-legacy/Cargo.toml:3` keeps its own 1.9.0 behind a trailing comment."""
    write(repo, "VERSION", "4.8.0\n")
    write(
        repo,
        "Cargo.toml",
        '[workspace]\nresolver = "2"\nmembers = ["crates/app-cli", "crates/app-legacy"]\n\n'
        '[workspace.package]\nedition = "2024"\nversion = "4.8.0"\n',
    )
    write(
        repo,
        "crates/app-cli/Cargo.toml",
        '[package]\nname = "app-cli"\nversion.workspace = true  # the release line\n',
    )
    write(repo, "crates/app-legacy/Cargo.toml", OWN_VERSION)
    write(
        repo,
        "Cargo.lock",
        '[[package]]\nname = "app-cli"\nversion = "4.8.0"\n\n'
        '[[package]]\nname = "app-legacy"\nversion = "1.9.0"\n',
    )


def test_a_trailing_comment_on_a_version_line_is_the_version(tmp_path):
    """6.18: `version = "1.9.0"  # ...` read as INHERITING 4.8.0, so the lock read as stale."""
    repo = tmp_path / "consumer"
    repo.mkdir()
    _zinc(repo)
    write(repo, "VERSION", "")
    findings, examined, _ = audit(repo)
    assert findings == [] and examined == 2, (findings, examined)


def test_a_member_keeping_its_own_version_line_is_not_the_release(tmp_path):
    """6.18's second arm: VERSION 4.8.0 is the release the INHERITING members carry; a crate that
    writes its own literal under a workspace version has opted out of it."""
    repo = tmp_path / "consumer"
    repo.mkdir()
    _zinc(repo)
    findings, examined, _ = audit(repo)
    assert findings == [] and examined == 2, (findings, examined)


def test_an_inheriting_member_left_behind_by_the_release_is_still_red(tmp_path):
    """The other direction: VERSION moved to 4.9.0 and the workspace did not."""
    repo = tmp_path / "consumer"
    repo.mkdir()
    _zinc(repo)
    write(repo, "VERSION", "4.9.0\n")
    findings, _, _ = audit(repo)
    assert any("no single answer" in f or "4.9.0" in f for f in findings), findings
