"""Shared fixtures: build throwaway git repos to run the checkers against.

Every disallowed glyph in this suite is built with chr(0x...) — never as a
literal — so the suite cannot trip gates_of_heck's own emoji gate.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Disallowed-by-policy glyphs, by number (see checks/check_no_emoji.py).
EMOJI_SMILE = chr(0x1F600)      # pictograph
CHECK_MARK_BUTTON = chr(0x2705)  # dingbat check-mark-button (NOT the allowed U+2713)
VS16 = chr(0xFE0F)              # emoji variation selector
KEYCAP_COMBINE = chr(0x20E3)    # combining enclosing keycap
DOUBLE_ARROW = chr(0x21D2)      # rejected by number, never written literally
STAR = chr(0x2B50)

# Allowed glyphs may appear literally.
ALLOWED = "→ ✓ ✗ ⚠ ↔ ↑ ↓ ← ⌘ ⌥ ⌨"


def git(repo: Path, *args: str) -> str:
    """Run a git command in `repo`, return stdout; raise on failure."""
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


def git_ok(repo: Path, *args: str) -> bool:
    """Run a git command in `repo`; True iff it exited 0."""
    return (
        subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True
        ).returncode
        == 0
    )


def write(repo: Path, rel: str, content: str) -> Path:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def stage(repo: Path, *rels: str) -> None:
    git(repo, "add", "--", *rels)


def commit_all(repo: Path, msg: str = "fixture") -> None:
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", msg)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """An initialized git repo with one committed file."""
    r = tmp_path / "proj"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    write(r, "README.md", "# fixture\n")
    commit_all(r)
    return r


def run_check(repo: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    """Run a checker from this repo against `repo` (cwd=repo)."""
    return subprocess.run(
        ["python3", str(REPO_ROOT / script), *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def run_gate(repo: Path, gate: str, *args: str) -> subprocess.CompletedProcess:
    """Run one of this repo's gate scripts against `repo` (bash, cwd=repo)."""
    return subprocess.run(
        ["/bin/bash", str(REPO_ROOT / gate), *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )
