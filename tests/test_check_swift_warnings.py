"""checks/check_swift_warnings.py — every warning in the repo's own Swift sources fails.

The class behind ZoneWM's blind warning gates (2026-09-26): `swift build` colours its output and
wraps each diagnostic group in an OSC 8 hyperlink even into a pipe, so a grep for `#<Group>` or a
match anchored on plain text misses warnings that are plainly there. The gate must go RED on a
hyperlinked, a coloured and a plain warning in the repo's own dirs, stay GREEN on a dependency's
warning and on a clean log, and honour --dirs.
"""

import subprocess
from pathlib import Path

from conftest import REPO_ROOT

CHECK = "checks/check_swift_warnings.py"
ESC = "\x1b"


def run_check(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["python3", str(REPO_ROOT / CHECK), *args],
                          cwd=repo, capture_output=True, text=True)


def log_with(repo: Path, *lines: str) -> Path:
    path = repo / "build.log"
    path.write_text("\n".join(lines) + "\nBuild complete!\n", encoding="utf-8")
    return path


def hyperlinked(repo: Path, rel: str) -> str:
    return (f"{repo}/{rel}:19:26: {ESC}[1;33mwarning: {ESC}[1;39mcapture of 'self' in a '@Sendable' "
            f"closure [#{ESC}]8;;https://docs.swift.org/x{ESC}\\SendableClosureCaptures{ESC}]8;;{ESC}\\]{ESC}[0;0m")


def test_selftest_passes():
    r = run_check(REPO_ROOT, "--selftest")
    assert r.returncode == 0, r.stdout + r.stderr


def test_clean_log_passes(repo):
    r = run_check(repo, "--log", str(log_with(repo, "Compiling A.swift")))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no compiler warnings" in r.stdout


def test_hyperlinked_warning_in_tests_is_red_and_named_a_trap(repo):
    r = run_check(repo, "--log", str(log_with(repo, hyperlinked(repo, "Tests/T.swift"))))
    assert r.returncode == 1
    assert "runtime trap" in r.stdout
    assert "Tests/T.swift:19:26" in r.stdout


def test_coloured_warning_without_a_group_is_red(repo):
    line = f"{repo}/Sources/A.swift:60:13: {ESC}[1;33mwarning: {ESC}[1;39m'nonisolated(unsafe)' is unnecessary{ESC}[0;0m"
    r = run_check(repo, "--log", str(log_with(repo, line)))
    assert r.returncode == 1
    assert "Sources/A.swift:60:13" in r.stdout


def test_dependency_warning_is_exempt(repo):
    line = f"{repo}/.build/checkouts/Dep/X.swift:1:1: warning: something in a dependency"
    r = run_check(repo, "--log", str(log_with(repo, line)))
    assert r.returncode == 0, r.stdout


def test_dirs_decides_what_is_ours(repo):
    log = log_with(repo, hyperlinked(repo, "App/T.swift"))
    assert run_check(repo, "--log", str(log)).returncode == 0
    assert run_check(repo, "--log", str(log), "--dirs", "App").returncode == 1
