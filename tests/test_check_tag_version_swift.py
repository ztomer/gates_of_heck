"""check_tag_version.py — the `swift` layout, on its own.

The checker reads a repo's declared version from wherever the repo declares it, and each layout is
one registry entry. This file holds the cases for the layout where the version is a Swift constant,
which is the one a SwiftPM package uses and the one ZoneWM was refused a release over.

Split from `test_check_tag_version.py` by concern rather than for length alone: a reader asking "how
does this gate read a Swift package's version" should find one file, not a section of a Rust-shaped
test suite.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT, git, write
from tier_kit import both_tiers, run_tiered  # noqa: F401  # both_tiers: a fixture

pytestmark = pytest.mark.usefixtures("both_tiers")


def run_check(repo, script, *args):
    """The checker over `repo`, on the current tier (Python, or `goh tag-version`)."""
    return run_tiered(repo, script, "tag-version", *args, python_only=("--probe",))


sys.path.insert(0, str(REPO_ROOT / "checks"))
import _version_sources as sources  # noqa: E402

CHECKER = "checks/check_tag_version.py"
ZERO = "0" * 40

VERSION_SWIFT = (
    "public enum ZTVersion {\n"
    '    public static let marketing = "2.73.0"\n'
    '    public static let build = "131"\n'
    "}\n"
)


def _sha(repo, rev):
    return git(repo, "rev-parse", rev).strip()


def _refs(*pairs):
    return "".join(f"{ref} {sha} {ref} {ZERO}\n" for ref, sha in pairs)


def _tagged_repo(tmp_path, name, rel, text):
    """A repo with one commit declaring `text` at `rel`, tagged to match it."""
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, rel, text)
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v2.73.0")
    refs = tmp_path / f"{name}-refs.txt"
    refs.write_text(_refs(("refs/tags/v2.73.0", _sha(repo, "v2.73.0"))))
    return repo, refs


def _run(repo, refs, source):
    return subprocess.run(
        ["python3", str(REPO_ROOT / CHECKER), "--refs-file", str(refs)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=dict(os.environ, GOH_TAG_VERSION_SOURCES=source),
    )


def test_a_swift_constant_is_a_version_source(tmp_path):
    """ZoneWM's layout: `bump.sh` writes the version into a Swift constant, so there is no
    VERSION file and no Cargo.toml. Without a `swift` strategy the gate reported the tag as
    unverifiable and the pre-push refused a correct release."""
    repo = tmp_path / "swiftpkg"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(
        repo,
        "Sources/ZTCore/Version.swift",
        "public enum ZTVersion {\n"
        '    public static let marketing = "2.73.0"\n'
        '    public static let build = "131"\n'
        "}\n",
    )
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v2.73.0")
    refs = tmp_path / "refs.txt"
    refs.write_text(_refs(("refs/tags/v2.73.0", _sha(repo, "v2.73.0"))))

    import os

    # Without the strategy the tag is unverifiable and the push is refused.
    plain = run_check(repo, CHECKER, "--refs-file", str(refs))
    assert plain.returncode == 1, plain.stdout + plain.stderr

    configured = subprocess.run(
        ["python3", str(REPO_ROOT / CHECKER), "--refs-file", str(refs)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=dict(os.environ, GOH_TAG_VERSION_SOURCES="swift:Sources/ZTCore/Version.swift"),
    )
    assert configured.returncode == 0, configured.stdout + configured.stderr


def test_the_swift_strategy_reads_the_release_number_not_the_build_number():
    """The build number is `131`, which is not `x.y.z`. Reading the first string constant
    instead of the first RELEASE number would refuse a correct tag."""
    source = (
        "public enum ZTVersion {\n"
        '    public static let marketing = "2.73.0"\n'
        '    public static let build = "131"\n'
        "}\n"
    )
    assert sources.from_swift(source) == [("(swift:marketing)", "2.73.0")]


def test_a_swift_file_that_declares_nothing_is_a_named_non_run_not_a_pass(tmp_path):
    """A version file with only a build number is not a version file. Silence would let a
    correct-looking tag pass on a repo that declares nothing."""
    repo = tmp_path / "buildonly"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "Version.swift", 'public static let build = "131"\n')
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v2.73.0")
    refs = tmp_path / "refs.txt"
    refs.write_text(_refs(("refs/tags/v2.73.0", _sha(repo, "v2.73.0"))))

    import os

    out = subprocess.run(
        ["python3", str(REPO_ROOT / CHECKER), "--refs-file", str(refs)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=dict(os.environ, GOH_TAG_VERSION_SOURCES="swift:Version.swift"),
    )
    assert out.returncode == 1, out.stdout + out.stderr


def test_two_release_numbers_in_one_swift_file_still_fail_the_gate(tmp_path):
    """Returning EVERY `x.y.z` constant means a file that declares two different release numbers
    is caught, rather than resolved by taking whichever came first."""
    repo = tmp_path / "twonums"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(
        repo,
        "Version.swift",
        'public static let marketing = "2.73.0"\npublic static let other = "9.9.9"\n',
    )
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v2.73.0")
    refs = tmp_path / "refs.txt"
    refs.write_text(_refs(("refs/tags/v2.73.0", _sha(repo, "v2.73.0"))))

    import os

    out = subprocess.run(
        ["python3", str(REPO_ROOT / CHECKER), "--refs-file", str(refs)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=dict(os.environ, GOH_TAG_VERSION_SOURCES="swift:Version.swift"),
    )
    assert out.returncode == 1, out.stdout + out.stderr
