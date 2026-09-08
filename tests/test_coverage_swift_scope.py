"""What the Swift coverage gate measures, and which binary it measures it with.

Two defects fixed together on 2026-09-07, both of which made the gate report
confidently about the wrong thing:

  - the .xctest bundle's Contents/MacOS holds a `.dSYM` DIRECTORY beside the
    executable whenever the package is built with debug symbols, which is how
    `swift test` builds by default. The old selection took the first glob hit,
    handed llvm-cov the .dSYM, and llvm-cov died with "Is a directory" -- so
    coverage_swift.py could not measure an ordinary SPM package at all. The
    existing selection tests missed it because their fixtures put the
    executable in Contents/MacOS and nothing else: a harness that builds its
    own inputs only ever tests the shapes it thought to build.

  - coverage_swift.py applied NO scope filter, so Tests/ counted toward the
    floor. Test files are ~100% covered by definition -- they are the thing
    doing the running -- so on antiknob it read 10.54% where the same tree
    measured 4.78% of its actual sources. A floor set on the first number can
    be met by writing tests that assert nothing.
"""

import importlib.util
import os
import sys
from pathlib import Path

from conftest import REPO_ROOT


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SCOPE = _load(REPO_ROOT / "lib" / "swift_coverage_scope.py", "swift_coverage_scope")


def _bundle_executables():
    """coverage_swift.bundle_executables, loaded without running main()."""
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    sys.path.insert(0, str(REPO_ROOT / "gates"))
    return _load(REPO_ROOT / "gates" / "coverage_swift.py",
                 "coverage_swift_under_test").bundle_executables


def _make_bundle(tmp_path, *, with_dsym=True):
    macos = tmp_path / "AppTests.xctest" / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    exe = macos / "AppTests"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    if with_dsym:
        # A directory, exactly as dsymutil leaves it.
        (macos / "AppTests.dSYM" / "Contents" / "Resources").mkdir(parents=True)
    return macos, exe


def test_a_dsym_directory_is_never_chosen_as_the_binary(tmp_path):
    macos, exe = _make_bundle(tmp_path)
    found = _bundle_executables()(str(macos))
    assert found == [str(exe)], (
        "the .dSYM directory sorts before the executable and used to be "
        "picked, which made llvm-cov fail with 'Is a directory'"
    )


def test_a_non_executable_file_beside_the_binary_is_not_chosen(tmp_path):
    macos, exe = _make_bundle(tmp_path, with_dsym=False)
    plain = macos / "Info.plist"
    plain.write_text("<plist/>")
    plain.chmod(0o644)
    assert _bundle_executables()(str(macos)) == [str(exe)]


def test_an_empty_bundle_yields_nothing_rather_than_a_directory(tmp_path):
    macos = tmp_path / "Empty.xctest" / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "Only.dSYM").mkdir()
    assert _bundle_executables()(str(macos)) == []


def test_test_sources_are_outside_the_measured_scope():
    # The property the floor depends on: a test file is ~100% covered by
    # definition, so counting it lets assertion-free tests raise coverage.
    assert not SCOPE.is_code_under_test("/p/Tests/AppTests/FooTests.swift")
    assert not SCOPE.is_code_under_test("/p/tests/foo.swift")
    assert SCOPE.is_code_under_test("/p/Sources/App/Foo.swift")


def test_generated_and_build_products_are_outside_the_measured_scope():
    for path in (
        "/p/.build/arm64-apple-macosx/debug/AppTests.derived/runner.swift",
        "/p/.build/x/DerivedSources/Gen.swift",
    ):
        assert not SCOPE.is_code_under_test(path), path


def _measured_files():
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    sys.path.insert(0, str(REPO_ROOT / "gates"))
    return _load(REPO_ROOT / "gates" / "coverage_swift.py",
                 "coverage_swift_under_test").measured_files


def test_the_gate_actually_drops_tests_from_the_denominator():
    """The behavioural check, not just "the import is present".

    An earlier version of this file asserted only that the scope module was
    imported, which stayed true when the filter itself was removed -- an
    assertion about a line of source rather than about what the gate does.
    """
    records = [
        {"filename": "/p/Sources/App/Foo.swift"},
        {"filename": "/p/Tests/AppTests/FooTests.swift"},
        {"filename": "/p/.build/x/AppTests.derived/runner.swift"},
    ]
    kept = _measured_files()(records)
    assert [f["filename"] for f in kept] == ["/p/Sources/App/Foo.swift"]


def test_both_measurement_paths_share_one_scope_definition():
    """The drift guard.

    check_swift_coverage.py had the rule and coverage_swift.py did not, and
    the two disagreed by more than a factor of two on the same tree. One
    copy and one absence is worse than two copies: the second path looked
    like it agreed. Both now import this module, so a change to the rule
    cannot reach one and miss the other.
    """
    checker = (REPO_ROOT / "checks" / "check_swift_coverage.py").read_text()
    gate = (REPO_ROOT / "gates" / "coverage_swift.py").read_text()
    assert "from swift_coverage_scope import" in checker
    assert "from swift_coverage_scope import" in gate
    # And neither may carry its own private copy of the marker list.
    for name, src in (("check_swift_coverage.py", checker),
                      ("coverage_swift.py", gate)):
        assert '"/Tests/",' not in src, (
            f"{name} has re-grown a local copy of the exclusion list; "
            "that is how the two drifted apart in the first place"
        )
