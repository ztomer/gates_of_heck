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
    r = run_check(
        repo,
        CHECKER,
        "--buildsystem",
        "cmake",
        "--tests-dir",
        f"{src.name}/tests",
        "--makefile",
        f"{src.name}/CMakeLists.txt",
    )
    assert r.returncode == 0, r.stderr
    assert (
        "all 2 test file(s)" in r.stdout
    )  # test_alpha + core_test; helper .h is not a test source


def test_unregistered_test_fails(repo):
    src = seed(repo, "cmake_orphan")
    r = run_check(
        repo,
        CHECKER,
        "--buildsystem",
        "cmake",
        "--tests-dir",
        f"{src.name}/tests",
        "--makefile",
        f"{src.name}/CMakeLists.txt",
    )
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
    r = run_check(
        repo,
        CHECKER,
        "--buildsystem",
        "cmake",
        "--tests-dir",
        f"{src.name}/tests",
        "--makefile",
        f"{src.name}/CMakeLists.txt",
    )
    assert r.returncode == 1
    assert "test_delta.cpp" in r.stderr


def test_optout_marker_exempts_with_reason(repo):
    src = seed(repo, "cmake_optout")
    r = run_check(
        repo,
        CHECKER,
        "--buildsystem",
        "cmake",
        "--tests-dir",
        f"{src.name}/tests",
        "--makefile",
        f"{src.name}/CMakeLists.txt",
    )
    assert r.returncode == 0, r.stderr


def test_missing_makefile_is_config_error(repo):
    src = seed(repo, "cmake_ok")
    (src / "CMakeLists.txt").unlink()
    r = run_check(
        repo,
        CHECKER,
        "--buildsystem",
        "cmake",
        "--tests-dir",
        f"{src.name}/tests",
        "--makefile",
        f"{src.name}/CMakeLists.txt",
    )
    assert r.returncode == 2
    assert "not found" in r.stderr


def test_stub_buildsystem_exits_2_with_named_reason(repo):
    src = seed(repo, "cmake_ok")
    for bs in ("xcode", "bazel", "meson", "swift-pm"):
        r = run_check(
            repo,
            CHECKER,
            "--buildsystem",
            bs,
            "--tests-dir",
            f"{src.name}/tests",
            "--makefile",
            f"{src.name}/CMakeLists.txt",
        )
        assert r.returncode == 2, bs
        assert "not implemented" in r.stderr and bs in r.stderr


def test_unknown_buildsystem_rejected_by_argparse(repo):
    r = run_check(repo, CHECKER, "--buildsystem", "make")
    assert r.returncode == 2


def test_red_proof_orphan_fails_then_registered_passes(repo):
    """The gate's whole reason to exist, in one flow: an orphan on disk that
    the build never compiles must FAIL, and registering it must PASS."""
    src = seed(repo, "cmake_orphan")
    args = (
        "--buildsystem",
        "cmake",
        "--tests-dir",
        f"{src.name}/tests",
        "--makefile",
        f"{src.name}/CMakeLists.txt",
    )
    red = run_check(repo, CHECKER, *args)
    assert red.returncode == 1 and "test_gamma.cpp" in red.stderr

    makefile = src / "CMakeLists.txt"
    makefile.write_text(makefile.read_text() + "add_executable(test_gamma tests/test_gamma.cpp)\n")
    green = run_check(repo, CHECKER, *args)
    assert green.returncode == 0, green.stderr


# --- A split CMakeLists: registrations in an include()d .cmake file count ---------------------
# The line cap counts CMakeLists.txt (b85e0af), so a long one MUST be split, and the usual split
# is `include(cmake/Tests.cmake)`. Reading only --makefile then reported every test the split
# moved as an orphan: two house gates that could not both pass (CadGoose, 2026-10-08).


def _cmake_tree(repo, lists: str, extra: dict[str, str]):
    root = repo / "split"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_alpha.cpp").write_text("// a\n")
    (root / "CMakeLists.txt").write_text(lists)
    for rel, body in extra.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    return root


def _check(repo, root):
    return run_check(
        repo, CHECKER, "--buildsystem", "cmake",
        "--tests-dir", f"{root.name}/tests", "--makefile", f"{root.name}/CMakeLists.txt",
    )  # fmt: skip


REGISTERS = "add_executable(T\n    tests/test_alpha.cpp\n)\n"


def test_a_registration_in_an_included_file_counts(repo):
    root = _cmake_tree(
        repo, "project(P)\ninclude(cmake/Tests.cmake)\n", {"cmake/Tests.cmake": REGISTERS}
    )
    r = _check(repo, root)
    assert r.returncode == 0, r.stderr


def test_an_include_spelled_with_a_source_dir_variable_is_followed(repo):
    for var in ("CMAKE_CURRENT_SOURCE_DIR", "CMAKE_CURRENT_LIST_DIR", "PROJECT_SOURCE_DIR"):
        root = _cmake_tree(
            repo, f'include("${{{var}}}/cmake/Tests.cmake")\n', {"cmake/Tests.cmake": REGISTERS}
        )
        r = _check(repo, root)
        assert r.returncode == 0, (var, r.stderr)
        shutil.rmtree(root)


def test_a_nested_include_is_followed_and_a_cycle_ends(repo):
    files = {
        "cmake/A.cmake": "include(${CMAKE_CURRENT_LIST_DIR}/B.cmake)\n",
        "cmake/B.cmake": REGISTERS + "include(cmake/A.cmake)\n",
    }
    root = _cmake_tree(repo, "include(cmake/A.cmake)\n", files)
    r = _check(repo, root)
    assert r.returncode == 0, r.stderr


def test_a_cmake_file_nobody_includes_registers_nothing(repo):
    root = _cmake_tree(repo, "project(P)\n", {"cmake/Tests.cmake": REGISTERS})
    r = _check(repo, root)
    assert r.returncode == 1 and "test_alpha.cpp" in r.stderr


def test_a_commented_include_and_a_module_name_are_not_followed(repo):
    lists = "include(FetchContent)\n# include(cmake/Tests.cmake)\n"
    root = _cmake_tree(repo, lists, {"cmake/Tests.cmake": REGISTERS})
    r = _check(repo, root)
    assert r.returncode == 1 and "test_alpha.cpp" in r.stderr
