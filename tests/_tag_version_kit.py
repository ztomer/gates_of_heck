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


# The retired `_version_sources` module, as the native reads it (Phase N3): one strategy over one
# text through `goh tag-version --extract KIND`, and the strategy names it answers to.
KINDS = ("file", "cargo", "swift", "xcconfig", "plist", "pyproject", "xcodegen", "gradle")


def extract(kind, text):
    """`[(table, version), ...]` the `kind` strategy reads in `text`."""
    import json
    import subprocess

    from conftest import native_goh_path

    r = subprocess.run(
        [str(native_goh_path()), "tag-version", "--extract", kind],
        input=text,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    return [tuple(row) for row in json.loads(r.stdout)]


def push_tag(repo, tag, sources=None):
    """`goh tag-version` judging a push of `refs/tags/<tag>` at HEAD, as the hook hands it."""
    import os
    import subprocess

    from conftest import native_goh_path

    env = {k: v for k, v in os.environ.items() if k != "GOH_TAG_VERSION_SOURCES"}
    if sources is not None:
        env["GOH_TAG_VERSION_SOURCES"] = sources
    return subprocess.run(
        [str(native_goh_path()), "tag-version", "--root", str(repo)],
        input=_refs((f"refs/tags/{tag}", _sha(repo, "HEAD"))),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
