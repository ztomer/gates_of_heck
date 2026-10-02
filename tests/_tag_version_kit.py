"""Shared fixture vocabulary for the check_tag_version suites.

The checker is pinned from several angles — the incident it exists for, which refs are in scope,
which layouts count as a declaration, and the push gate that calls it — and those suites are now
one file each. What they share is not a fixture but a VOCABULARY: a throwaway repo in one known
shape, and the ref lines git would hand the hook. Kept here so the shape is defined once; a
divergence in it would make two files disagree about what "the incident" looks like.
"""

from conftest import git, write

CHECKER = "checks/check_tag_version.py"
ZERO = "0" * 40


def _sha(repo, rev):
    return git(repo, "rev-parse", rev).strip()


def _refs(*pairs):
    return "".join(f"{ref} {sha} {ref} {ZERO}\n" for ref, sha in pairs)


def _media_shape(repo, version):
    """media_server's layout: a VERSION file AND a workspace Cargo.toml."""
    write(repo, "VERSION", f"{version}\n")
    write(
        repo,
        "Cargo.toml",
        f'[workspace]\nmembers = ["crates/healthcheck-rs"]\n\n'
        f'[workspace.package]\nversion = "{version}"\n',
    )
    write(
        repo,
        "crates/healthcheck-rs/Cargo.toml",
        f'[package]\nname = "healthcheck"\nversion = "{version}"\n',
    )
