"""check_tests_registered.py — contract tests, proven against fixture trees
under tests/fixtures/test_registration/.
"""

import shutil

from conftest import REPO_ROOT, run_check

CHECKER = "checks/check_tests_registered.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "test_registration"


def seed(repo, name: str):
    dest = repo / name
    shutil.copytree(FIXTURES / name, dest)
    return dest


def test_all_registered_passes(repo):
    src = seed(repo, "cmake_ok")
    r = run_check(repo, CHECKER, "--buildsystem", "cmake",
                  "--tests-dir", f"{src.name}/tests",
                  "--makefile", f"{src.name}/CMakeLists.txt")
    assert r.returncode == 0, r.stderr
    assert "all 2 test file(s)" in r.stdout  # test_alpha + core_test; helper .h is not a test source


def test_unregistered_test_fails(repo):
    src = seed(repo, "cmake_orphan")
    r = run_check(repo, CHECKER, "--buildsystem", "cmake",
                  "--tests-dir", f"{src.name}/tests",
                  "--makefile", f"{src.name}/CMakeLists.txt")
    assert r.returncode == 1
    assert "test_gamma.cpp" in r.stderr
    # the REGISTERED sibling must not be named as an orphan
    for line in r.stderr.splitlines():
        if line.strip().startswith(("tests/", f"{src.name}/")):
            assert "test_beta" not in line


def test_comment_or_set_mention_does_not_register(repo):
    # The better-one semantics: registration requires a target BLOCK, not a
    # path mention anywhere in the file. Prove the parser bites.
    src = seed(repo, "cmake_comment")
    r = run_check(repo, CHECKER, "--buildsystem", "cmake",
                  "--tests-dir", f"{src.name}/tests",
                  "--makefile", f"{src.name}/CMakeLists.txt")
    assert r.returncode == 1
    assert "test_delta.cpp" in r.stderr


def test_optout_marker_exempts_with_reason(repo):
    src = seed(repo, "cmake_optout")
    r = run_check(repo, CHECKER, "--buildsystem", "cmake",
                  "--tests-dir", f"{src.name}/tests",
                  "--makefile", f"{src.name}/CMakeLists.txt")
    assert r.returncode == 0, r.stderr


def test_missing_makefile_is_config_error(repo):
    src = seed(repo, "cmake_ok")
    (src / "CMakeLists.txt").unlink()
    r = run_check(repo, CHECKER, "--buildsystem", "cmake",
                  "--tests-dir", f"{src.name}/tests",
                  "--makefile", f"{src.name}/CMakeLists.txt")
    assert r.returncode == 2
    assert "not found" in r.stderr


def test_stub_buildsystem_exits_2_with_named_reason(repo):
    src = seed(repo, "cmake_ok")
    for bs in ("xcode", "bazel", "meson", "swift-pm"):
        r = run_check(repo, CHECKER, "--buildsystem", bs,
                      "--tests-dir", f"{src.name}/tests",
                      "--makefile", f"{src.name}/CMakeLists.txt")
        assert r.returncode == 2, bs
        assert "not implemented" in r.stderr and bs in r.stderr


def test_unknown_buildsystem_rejected_by_argparse(repo):
    r = run_check(repo, CHECKER, "--buildsystem", "make")
    assert r.returncode == 2


def test_red_proof_orphan_fails_then_registered_passes(repo):
    """The gate's whole reason to exist, in one flow: an orphan on disk that
    the build never compiles must FAIL, and registering it must PASS."""
    src = seed(repo, "cmake_orphan")
    args = ("--buildsystem", "cmake",
            "--tests-dir", f"{src.name}/tests",
            "--makefile", f"{src.name}/CMakeLists.txt")
    red = run_check(repo, CHECKER, *args)
    assert red.returncode == 1 and "test_gamma.cpp" in red.stderr

    makefile = src / "CMakeLists.txt"
    makefile.write_text(
        makefile.read_text() + "add_executable(test_gamma tests/test_gamma.cpp)\n"
    )
    green = run_check(repo, CHECKER, *args)
    assert green.returncode == 0, green.stderr
