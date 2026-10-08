"""goh tag-version -- the `xcodegen` and `gradle` layouts, which no strategy could read.

An app built with XcodeGen declares its release as the `MARKETING_VERSION` build setting in
`project.yml`, a YAML key; its Android half declares `versionName` in `build.gradle.kts`. Measured on
koffee_big (2026-10-08): its `v2.11.5` push was refused with "NO version source declares a version
at this commit" -- correct by this gate's rule, because the release WAS declared, in two files, in
two shapes none of `file`/`cargo`/`swift`/`xcconfig`/`plist`/`pyproject` reads (`xcconfig` wants
`NAME = value`, and a YAML key is `NAME: value`). The same class as the `plist` suite: a gate that
cannot see a member of its own population.

Both strategies read ONE name. These files also pin package, SDK and dependency versions in the
same shape, and every declaration found must equal the tag, so a broad YAML or Gradle reader would
refuse correct releases.
"""

import pytest
from _fast_git import fast_init
from _tag_version_kit import extract, push_tag
from conftest import git, write

PROJECT_YML = """name: BigKoff
options:
  deploymentTarget:
    macOS: "26.0"
packages:
  GRDB:
    url: https://github.com/groue/GRDB.swift
    from: 7.4.1
settings:
  base:
    MARKETING_VERSION: "{version}"
    CURRENT_PROJECT_VERSION: 30
    SWIFT_VERSION: "6.0.3"
"""

GRADLE_KTS = """android {{
    defaultConfig {{
        applicationId = "com.bigkoff.companion"
        versionCode = 30
        versionName = "{version}"
    }}
}}
dependencies {{
    implementation("androidx.core:core-ktx:1.17.0")
    val composeVersion = "1.9.3"
}}
"""


def test_xcodegen_reads_the_marketing_version_and_nothing_else():
    found = extract("xcodegen", PROJECT_YML.format(version="2.11.5"))
    assert found == [("(xcodegen:MARKETING_VERSION)", "2.11.5")], (
        "the package pin 7.4.1 and SWIFT_VERSION 6.0.3 are semver too; reading either would refuse "
        "every correct release"
    )


@pytest.mark.parametrize(
    "line",
    [
        "MARKETING_VERSION: 2.11.5",
        "MARKETING_VERSION: '2.11.5'",
        "MARKETING_VERSION: 2.11.5  # the release",
    ],
)
def test_xcodegen_reads_every_yaml_spelling(line):
    assert extract("xcodegen", f"settings:\n  base:\n    {line}\n") == [
        ("(xcodegen:MARKETING_VERSION)", "2.11.5")
    ]


def test_gradle_reads_the_version_name_and_nothing_else():
    found = extract("gradle", GRADLE_KTS.format(version="2.11.5"))
    assert found == [("(gradle:versionName)", "2.11.5")], (
        "a dependency coordinate and a compose version are semver too"
    )


def test_gradle_reads_the_groovy_spelling():
    assert extract("gradle", "defaultConfig {\n    versionName '2.11.5'\n}\n") == [
        ("(gradle:versionName)", "2.11.5")
    ]


@pytest.mark.parametrize("kind", ["xcodegen", "gradle"])
def test_a_file_with_no_release_declares_nothing(kind):
    assert extract(kind, "name: x\nversionCode = 3\nMARKETING_VERSION: $(VERSION)\n") == []


def _app(tmp_path, version):
    repo = tmp_path / "app"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "project.yml", PROJECT_YML.format(version=version))
    write(repo, "android/app/build.gradle.kts", GRADLE_KTS.format(version=version))
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    return repo


SOURCES = "xcodegen:project.yml gradle:android/app/build.gradle.kts"


def test_a_matching_tag_is_accepted(tmp_path):
    r = push_tag(_app(tmp_path, "2.11.5"), "v2.11.5", SOURCES)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_tag_that_disagrees_is_a_finding(tmp_path):
    """The direction that matters: a strategy that only ever accepts is not a check."""
    r = push_tag(_app(tmp_path, "2.11.5"), "v2.11.6", SOURCES)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "2.11.6" in r.stdout + r.stderr and "2.11.5" in r.stdout + r.stderr


def test_one_half_lagging_is_a_finding(tmp_path):
    """The Mac half bumped and the phone half not: the tag names a release one build disowns."""
    repo = _app(tmp_path, "2.11.5")
    write(repo, "android/app/build.gradle.kts", GRADLE_KTS.format(version="2.11.4"))
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "lag")
    r = push_tag(repo, "v2.11.5", SOURCES)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "gradle" in r.stdout + r.stderr
