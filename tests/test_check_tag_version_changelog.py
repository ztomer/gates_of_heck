"""`goh tag-version`'s `changelog:` source: a release commit names its version in the stanza it cut.

GOH.SHIPRELREUSE: goh's version is its tag (lib/goh_version.sh), so no file a build reads carries
it, and the release commit changes only CHANGELOG.md. What that commit DOES declare is its newest
`## vX.Y.Z` heading, and that is what a pushed tag must match: `v0.125.0` on a commit whose
changelog tops out at `## v0.124.0` is a release that reports itself differently from its name.
`## Unreleased` and a heading that names no semver are not declarations.
"""

import pytest
from _tag_version_kit import extract, push_tag
from conftest import git, write

LOG = """# CHANGELOG

## Unreleased

* **Next.** pending

## v0.125.0 -- the release title

* **One.** a

## v0.124.0 -- the one before

* **Two.** b
"""


def test_the_newest_versioned_heading_is_the_declaration() -> None:
    assert extract("changelog", LOG) == [("(changelog:## heading)", "0.125.0")]


@pytest.mark.parametrize(
    "text", ["# CHANGELOG\n\n## Unreleased\n\n* x\n", "## a heading\n\n* x\n", ""]
)
def test_a_changelog_with_no_release_heading_declares_nothing(text: str) -> None:
    assert extract("changelog", text) == []


def _commit(repo, text: str) -> None:
    write(repo, "CHANGELOG.md", text)
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "release")


def test_a_tag_matching_its_commits_stanza_is_green(repo) -> None:
    _commit(repo, LOG)
    r = push_tag(repo, "v0.125.0", "changelog:CHANGELOG.md")
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_tag_the_stanza_does_not_name_is_red(repo) -> None:
    _commit(repo, LOG)
    r = push_tag(repo, "v0.126.0", "changelog:CHANGELOG.md")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "0.125.0" in r.stdout + r.stderr, r.stdout + r.stderr


def test_goh_names_its_changelog_as_its_version_source() -> None:
    from conftest import REPO_ROOT

    assert (
        "GOH_TAG_VERSION_SOURCES='changelog:CHANGELOG.md'" in (REPO_ROOT / ".gatesrc").read_text()
    )


# GOH.TAGVERSIONRC: `--root X` names the repo to judge, so the repo's `.gatesrc` is its source list.
# `loopctl done`'s tag gate runs `goh tag-version --root <scratch checkout>` with no `.gatesrc`
# sourced, and goh's own Cargo.toml says the placeholder 0.0.0, so every done between releases was
# refused "Cargo.toml says 0.0.0 but no tag peels to it".


def _placeholder_repo(repo) -> None:
    write(repo, ".gatesrc", "GOH_TAG_VERSION_SOURCES='changelog:CHANGELOG.md'\n")
    write(repo, "Cargo.toml", '[workspace]\n\n[workspace.package]\nversion = "0.0.0"\n')
    _commit(repo, LOG)


def test_root_reads_the_repos_own_gatesrc_when_the_environment_names_nothing(repo) -> None:
    _placeholder_repo(repo)
    r = push_tag(repo, "v0.125.0")
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_environment_still_overrides_the_repos_gatesrc(repo) -> None:
    _placeholder_repo(repo)
    r = push_tag(repo, "v0.125.0", "cargo:Cargo.toml")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "0.0.0" in r.stdout + r.stderr, r.stdout + r.stderr
