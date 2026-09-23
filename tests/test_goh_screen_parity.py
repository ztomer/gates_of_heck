"""Screen parity: `goh screen` agrees with `check_no_screen_presentation.py`.

Runs both over the shared fixture corpus plus crafted edge cases, in
explicit-path, `--scope`, and `--staged` modes. Asserts identical exit
codes plus identical stdout/stderr — a pattern, mask, exemption,
message, or stream drifting on either side goes red.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "checks" / "check_no_screen_presentation.py"
FIXTURES = ROOT / "tests" / "fixtures" / "screen_presentation"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


def make_repo(tmp_path: Path, sub: str = "case") -> Path:
    repo = tmp_path / sub
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    shutil.copytree(FIXTURES, repo / "tests")
    _git(repo, "add", "-A")
    return repo


def run_py(repo: Path, *args: str) -> tuple[int, str, str]:
    r = subprocess.run(
        ["python3", str(CHECK), *args], cwd=repo, capture_output=True, text=True
    )
    return r.returncode, r.stdout, r.stderr


def run_goh(goh: Path, repo: Path, *args: str) -> tuple[int, str, str]:
    import os

    env = dict(os.environ, GOH_DIR=str(ROOT))
    r = subprocess.run(
        [str(goh), "screen", *args], cwd=repo, capture_output=True, text=True, env=env
    )
    return r.returncode, r.stdout, r.stderr


def test_corpus_explicit_dir_agrees(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    got = run_goh(goh, repo, "tests")
    assert got == run_py(repo, "tests"), got
    assert got[0] == 1


@pytest.mark.parametrize(
    "name",
    [
        "Bad.swift",
        "bad.m",
        "bad_cametallayer.swift",
        "bad_pyautogui.py",
        "bad_subprocess.py",
        "CleanSwiftUITests.swift",
        "guarded.py",
        "marked_live.py",
        "Marked.swift",
        "prose_only.py",
        "type_positions_clean.swift",
        "nested_comment_clean.swift",
    ],
)
def test_corpus_single_files_agree(goh: Path, tmp_path: Path, name: str) -> None:
    repo = make_repo(tmp_path)
    target = f"tests/{name}"
    assert run_goh(goh, repo, target) == run_py(repo, target), name


def test_scope_mode_agrees(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    for scope in ["tests/*", "tests/bad_*.swift", "tests/*.py", "tests/nothing_here_*"]:
        assert run_goh(goh, repo, "--scope", scope) == run_py(repo, "--scope", scope), scope


def test_staged_indexes_the_violation(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    extra = repo / "tests" / "extra.swift"
    extra.write_text("NSScreen.main\n")
    _git(repo, "add", "tests/extra.swift")
    extra.write_text("let x = 1\n")
    got = run_goh(goh, repo, "--scope", "tests/extra.swift", "--staged")
    assert got == run_py(repo, "--scope", "tests/extra.swift", "--staged"), got
    assert got[0] == 1
    assert "tests/extra.swift:1" in got[2]


def test_no_targets_is_usage_error_on_both(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    got = run_goh(goh, repo)
    ref = run_py(repo)
    assert got[0] == ref[0] == 2
    assert "no targets" in got[2] and "no targets" in ref[2]


def test_a_call_nested_in_a_block_comment_is_prose(goh: Path, tmp_path: Path) -> None:
    """Swift nests block comments; the first `*/` does not end the outer one (2026-09-23)."""
    repo = make_repo(tmp_path)
    target = "tests/nested_comment_clean.swift"
    for code, out, err in (run_py(repo, target), run_goh(goh, repo, target)):
        assert code == 0, out + err
