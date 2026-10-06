"""No vendored copy of a house checker (SUPERSOTA R5, `crates/goh/src/vendored.rs`).

A copy of a house checker in a consumer is frozen at the day it was copied: four repos carried a
`check_no_allow.py` until 2026-09-14 and the rest were not checked at all; antiknob still carries a
`tools/_gitutil.py`. A NEW copy is refused at the commit that adds it; an existing one is named at
full scope, so adopting the rule turns no repo red on the day it lands.
"""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, hermetic_env, write

LABEL = "no vendored copies of house checkers"


def _structural(goh: Path, repo: Path, *args: str):
    import subprocess

    env = hermetic_env(GOH_DIR=str(REPO_ROOT))
    return subprocess.run(
        [str(goh), "structural", *args], cwd=repo, capture_output=True, text=True, env=env
    )


def test_a_new_copy_is_refused_at_the_commit_that_adds_it(goh: Path, repo: Path) -> None:
    write(repo, "tools/_gitutil.py", "def repo_root():\n    return '.'\n")
    git(repo, "add", "tools/_gitutil.py")
    r = _structural(goh, repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"structural: {LABEL} failed" in r.stderr, r.stderr
    assert "tools/_gitutil.py: a copy of a house checker" in r.stderr + r.stdout


def test_a_retired_checkers_name_is_a_house_name_too(goh: Path, repo: Path) -> None:
    """The most frozen copy of all: one of a checker the shared checkout no longer ships."""
    write(repo, "tools/check_no_conflict_markers.py", "print('ok')\n")
    git(repo, "add", "-A")
    r = _structural(goh, repo, "--staged")
    assert r.returncode == 1 and "check_no_conflict_markers.py" in r.stderr + r.stdout, r.stderr


def test_an_existing_copy_is_named_not_failed(goh: Path, repo: Path) -> None:
    write(repo, "tools/check_no_allow.py", "print('ok')\n")
    commit_all(repo, "an old vendored copy")
    full = _structural(goh, repo, "--full")
    assert "tools/check_no_allow.py: a vendored copy of a house checker" in full.stderr, full.stderr
    assert f"✓ {LABEL}" in full.stdout, full.stdout
    write(repo, "tools/check_no_allow.py", "print('still ok')\n")
    git(repo, "add", "-A")
    staged = _structural(goh, repo, "--staged")
    assert f"✓ {LABEL}" in staged.stdout, "an EDIT to an existing copy is not a new one"


def test_a_file_that_only_resembles_a_house_name_is_not_a_copy(goh: Path, repo: Path) -> None:
    write(repo, "tests/test_check_no_emoji.py", "def test_x():\n    pass\n")
    git(repo, "add", "-A")
    r = _structural(goh, repo, "--staged")
    assert f"✓ {LABEL}" in r.stdout, r.stdout + r.stderr


def test_the_retired_list_is_the_one_in_retired_py() -> None:
    import re

    sys.path.insert(0, str(REPO_ROOT / "checks"))
    from _retired import NATIVE

    src = (REPO_ROOT / "crates" / "goh" / "src" / "vendored.rs").read_text(encoding="utf-8")
    block = src[src.index("pub const RETIRED") : src.index("];", src.index("pub const RETIRED"))]
    assert set(re.findall(r'"(check_[a-z_]+)"', block)) == set(NATIVE)
