"""Home-paths parity: `goh home-paths` agrees with `check_no_home_paths.py`.

Fixture repos per case; asserts identical exit codes plus identical
stdout/stderr. A guard, message, scope branch, or column drifting on
either side goes red.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "checks" / "check_no_home_paths.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


def make_repo(tmp_path: Path, files: dict[str, bytes]) -> Path:
    repo = tmp_path
    repo.mkdir(exist_ok=True)
    _git(repo, "init", "-q")
    for name, content in files.items():
        dest = repo / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
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
        [str(goh), "home-paths", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )
    return r.returncode, r.stdout, r.stderr


CASES: dict[str, dict[str, bytes]] = {
    "clean": {"a.py": b"x = 1\n"},
    "macos": {"a.py": b'x = "/Users/me/src/x"\n'},
    "linux": {"a.py": b'x = "/home/me/src/x"\n'},
    "tilde": {"a.py": b"cd ~/Projects/x\n"},
    "home_var": {"a.py": b"cd $HOME/Projects/x\n"},
    "home_braced": {"a.py": b"cd ${HOME}/Projects/x\n"},
    "word_guard": {"a.py": b"a/Users/me/x/\n"},
    "dot_guard": {"a.py": b"v1./Users/me/x/\n"},
    "tilde_dot": {"a.py": b"x.~/Projects/x\n"},
    "env_default": {"a.py": b'GOH="${GOH_DIR:-$HOME/Projects/gates_of_heck}"\n'},
    "marker_same_line": {"a.py": b'x = "/Users/m/x";  // path-ok: dev fallback\n'},
    "marker_above": {"a.py": b"// path-ok: recorded incident\nx = \"/Users/m/x\"\n"},
    "marker_bare": {"a.py": b'x = "/Users/m/x";  // path-ok:\n'},
    "unicode_col": {"a.py": "→ /Users/me/x/\n".encode()},
}


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("staged", [False, True])
def test_home_paths_agrees(goh: Path, tmp_path: Path, name: str, staged: bool) -> None:
    repo = make_repo(tmp_path, CASES[name])
    args = ["--staged"] if staged else []
    assert run_goh(goh, repo, *args) == run_py(repo, *args), name


def test_staged_ignores_unstaged_dirt(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"a.py": b"x = 1\n"})
    (repo / "a.py").write_bytes(b'x = "/Users/me/x"\n')
    got = run_goh(goh, repo, "--staged")
    assert got == run_py(repo, "--staged"), got
    assert got[0] == 0
    assert "1 staged files clean" in got[1]


def test_staged_empty_index_is_honest_on_both(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"a.py": b"x = 1\n"})
    _git(repo, "rm", "-q", "--cached", "a.py")
    (repo / "a.py").unlink()
    got = run_goh(goh, repo, "--staged")
    assert got == run_py(repo, "--staged"), got
    assert got[0] == 0
    assert "nothing staged" in got[1]


def test_staged_indexes_the_violation(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"a.py": b"x = 1\n"})
    (repo / "a.py").write_bytes(b'x = "/Users/me/x"\n')
    _git(repo, "add", "a.py")
    (repo / "a.py").write_bytes(b"x = 1\n")
    got = run_goh(goh, repo, "--staged")
    assert got == run_py(repo, "--staged"), got
    assert got[0] == 1


def test_exclude_passes_with_a_clean_file_left(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        {"vendor/a.py": b'x = "/Users/me/x"\n', "src/b.py": b"x = 1\n"},
    )
    args = ["--exclude", "vendor/"]
    got = run_goh(goh, repo, *args)
    assert got == run_py(repo, *args), "exclude"
    assert got[0] == 0
    assert "1 tracked files clean" in got[1]
    # Excluding the only file leaves zero files to check — the empty-scope
    # refusal fires on both sides, never a clean pass over nothing.
    repo = make_repo(tmp_path / "sub", {"vendor/a.py": b'x = "/Users/me/x"\n'})
    args = ["--exclude", "vendor/"]
    got = run_goh(goh, repo, *args)
    assert got == run_py(repo, *args), "exclude"
    assert got[0] == 1
    assert "refusing to report clean over zero files" in got[1]


def test_empty_tree_refuses_on_both(goh: Path, tmp_path: Path) -> None:
    repo = tmp_path
    repo.mkdir(exist_ok=True)
    _git(repo, "init", "-q")
    got = run_goh(goh, repo)
    assert got == run_py(repo), got
    assert got[0] == 1
    assert "refusing to report clean over zero files" in got[1]
