"""check_tag_version.py — the `xcconfig` layout, on its own.

The layout a project reaches for when the version has to be readable by Xcode AND by a shell: one
build-settings file is the declaration, and everything else is derived from it. The gate has to
read THAT file. Pointed at the generated Swift instead it would police a projection, and pointed at
the same file as `file:` it fails the worst way available — see `test_the_file_strategy_would_have
_read_this_layout_wrong`.

Split out by concern rather than length: a reader asking "how does this gate read a build-settings
file" should find one file, not a section of the other layouts' suites.
"""

import os
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, git, write
from tier_kit import both_tiers, run_tiered  # noqa: F401  # both_tiers: a fixture

pytestmark = pytest.mark.usefixtures("both_tiers")


def run_check(repo, script, *args):
    """The checker over `repo`, on the current tier (Python, or `goh tag-version`)."""
    return run_tiered(repo, script, "tag-version", *args)


sys.path.insert(0, str(REPO_ROOT / "checks"))
from _tag_version_kit import extract  # noqa: E402

CHECKER = "checks/check_tag_version.py"
ZERO = "0" * 40

VERSION_XCCONFIG = (
    "// The one place the version is written. See D-0195.\n"
    "MARKETING_VERSION = 2.73.0\n"
    "CURRENT_PROJECT_VERSION = 131\n"
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


def test_the_xcconfig_strategy_reads_the_release_number_not_the_build_number():
    """`CURRENT_PROJECT_VERSION = 131` is not `x.y.z`. Taking the first assignment would
    refuse every correct tag; taking the first ASSIGNMENT rather than the first release number
    would read `MARKETING_VERSION = 2.73.0` whole and refuse them too."""
    assert extract("xcconfig", VERSION_XCCONFIG) == [("(xcconfig:MARKETING_VERSION)", "2.73.0")]


def test_the_file_strategy_would_have_read_this_layout_wrong():
    """The reason this is a strategy and not a pointer at the same file. `file:` skips `#`
    comments; Xcode comments start with `//`, so a commented settings file hands the gate a
    SENTENCE as its version, and an uncommented one hands it `MARKETING_VERSION = 2.73.0`
    whole. Either way a correct release reads as a mismatch against its own tag — the one
    failure mode nobody investigates."""
    assert extract("file", VERSION_XCCONFIG) == [
        ("(file)", "// The one place the version is written. See D-0195.")
    ]
    assert extract("file", "MARKETING_VERSION = 2.73.0\n") == [
        ("(file)", "MARKETING_VERSION = 2.73.0")
    ]


def test_a_trailing_comment_and_an_sdk_condition_are_not_part_of_the_value():
    """Real settings files carry both, and a version read as `2.73.0 // bump me` or
    `2.73.0[sdk=macosx*]` is a correct tag reported as a mismatch."""
    source = (
        "MARKETING_VERSION[sdk=macosx*] = 2.73.0 // bumped by tools/gen_version.py\n"
        "CURRENT_PROJECT_VERSION = 131\n"
    )
    assert extract("xcconfig", source) == [("(xcconfig:MARKETING_VERSION)", "2.73.0")]


def test_a_settings_file_that_declares_no_release_number_is_a_named_non_run(tmp_path):
    """A file carrying only a build number declares no version. Silence must not read as
    compliance on a repo that says nothing."""
    repo, refs = _tagged_repo(
        tmp_path, "buildonly", "Config/Version.xcconfig", "CURRENT_PROJECT_VERSION = 131\n"
    )
    result = _run(repo, refs, "xcconfig:Config/Version.xcconfig")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "NO version source declares" in (result.stdout + result.stderr)


def test_a_build_settings_file_is_a_version_source(tmp_path):
    """End to end: the layout a project uses so one file serves both Xcode and a shell. With the
    strategy the tag verifies; without it the source is unreadable and the push is refused."""
    repo, refs = _tagged_repo(tmp_path, "xcode", "Config/Version.xcconfig", VERSION_XCCONFIG)

    plain = run_check(repo, CHECKER, "--refs-file", str(refs))
    assert plain.returncode == 1, plain.stdout + plain.stderr

    configured = _run(repo, refs, "xcconfig:Config/Version.xcconfig")
    assert configured.returncode == 0, configured.stdout + configured.stderr


def test_a_tag_that_disagrees_with_the_settings_file_is_still_refused(tmp_path):
    """The strategy has to be able to say no. A gate that accepts whatever the file says is
    not a gate."""
    repo, refs = _tagged_repo(
        tmp_path, "wrong", "Config/Version.xcconfig", "MARKETING_VERSION = 2.73.0\n"
    )
    git(repo, "tag", "v9.9.9")
    refs.write_text(_refs(("refs/tags/v9.9.9", _sha(repo, "v9.9.9"))))
    result = _run(repo, refs, "xcconfig:Config/Version.xcconfig")
    assert result.returncode == 1, result.stdout + result.stderr


def test_a_settings_file_disagreeing_with_itself_fails_rather_than_picking_one(tmp_path):
    """Two `x.y.z` settings cannot both be the release. Returning both means the gate sees the
    disagreement; returning the first would resolve it silently and read as truth."""
    source = "MARKETING_VERSION = 2.73.0\nMARKETING_VERSION_OVERRIDE = 9.9.9\n"
    assert len(extract("xcconfig", source)) == 2
    repo, refs = _tagged_repo(tmp_path, "selfdisagree", "Config/Version.xcconfig", source)
    result = _run(repo, refs, "xcconfig:Config/Version.xcconfig")
    assert result.returncode == 1, result.stdout + result.stderr
