"""No vendored copy of a house checker or of the house TUI (SUPERSOTA R5, GOH.VENDOR,
`crates/goh/src/vendored.rs`).

A copy of a house file in a consumer is frozen at the day it was copied: four repos carried a
`check_no_allow.py` until 2026-09-14, and divoom-control carried `tui/lib.sh`, `tui/lib.py` and
`tui/stylerc` until 8e05425 -- already drifted, so no NAME or HASH rule would have seen it as a copy
once renamed. A copy is known by its name OR by its content (most of a house file's lines), and it is
refused at BOTH scopes: the estate held none when the rule moved from naming to refusing (measured
2026-11-08 over 44 repos), so a full-scope refusal turns no repo red.
"""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, hermetic_env, write

LABEL = "no vendored copies of house files"


def _structural(goh: Path, repo: Path, *args: str):
    import subprocess

    env = hermetic_env(GOH_DIR=str(REPO_ROOT))
    return subprocess.run(
        [str(goh), "structural", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
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


def test_an_existing_copy_is_refused_at_full_scope_too(goh: Path, repo: Path) -> None:
    """The house rule is never vendor; the grace an existing copy once had ended with the estate's last."""
    write(repo, "tools/check_no_allow.py", "print('ok')\n")
    commit_all(repo, "an old vendored copy")
    full = _structural(goh, repo, "--full", "--keep-going")
    assert f"structural: {LABEL} failed" in full.stderr, full.stderr
    assert "tools/check_no_allow.py: a copy of a house checker" in full.stderr + full.stdout
    write(repo, "tools/check_no_allow.py", "print('still ok')\n")
    git(repo, "add", "-A")
    staged = _structural(goh, repo, "--staged")
    assert f"structural: {LABEL} failed" in staged.stderr, "an EDIT to a copy is still a copy"


HOUSE_TUI = ("lib.sh", "lib.py", "stylerc")


def _house(name: str) -> str:
    return (REPO_ROOT / "tui" / name).read_text(encoding="utf-8")


def _lightly_edited(text: str) -> str:
    """A copy someone made their own: every comment stripped (half of lib.sh's distinctive lines),
    a header and a line of their own. It still parses, so the gate's earlier steps pass it."""
    kept = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    return (
        "#!/usr/bin/env bash\n# our own style helpers\n"
        + "\n".join(kept)
        + "\nexport OUR_EXTRA=1\n"
    )


def test_a_planted_tui_copy_is_refused_at_commit(goh: Path, repo: Path) -> None:
    for name in HOUSE_TUI:
        write(repo, f"tui/{name}", _house(name))
    git(repo, "add", "-A")
    r = _structural(goh, repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    for name in HOUSE_TUI:
        assert f"tui/{name}: a copy of the house's tui/{name}" in r.stderr + r.stdout, r.stderr


def test_a_renamed_and_edited_tui_copy_is_still_a_copy(goh: Path, repo: Path) -> None:
    write(repo, "scripts/style_helpers.sh", _lightly_edited(_house("lib.sh")))
    write(repo, "tools/pretty.py", _lightly_edited(_house("lib.py")))
    write(repo, "etc/colours.conf", _lightly_edited(_house("stylerc")))
    git(repo, "add", "-A")
    r = _structural(goh, repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    said = r.stderr + r.stdout
    assert "scripts/style_helpers.sh: a copy of the house's tui/lib.sh" in said, said
    assert "tools/pretty.py: a copy of the house's tui/lib.py" in said, said
    assert "etc/colours.conf: a copy of the house's tui/stylerc" in said, said


def test_a_committed_tui_copy_is_refused_at_full_scope(goh: Path, repo: Path) -> None:
    write(repo, "vendor/kare.sh", _lightly_edited(_house("lib.sh")))
    commit_all(repo, "a vendored TUI")
    r = _structural(goh, repo, "--full", "--keep-going")
    assert f"structural: {LABEL} failed" in r.stderr, r.stderr
    assert "vendor/kare.sh: a copy of the house's tui/lib.sh" in r.stderr + r.stdout


def test_a_renamed_copy_of_a_house_checker_is_a_copy_by_content(goh: Path, repo: Path) -> None:
    write(
        repo,
        "tools/env_guard.py",
        _lightly_edited((REPO_ROOT / "checks" / "check_git_env.py").read_text()),
    )
    git(repo, "add", "-A")
    r = _structural(goh, repo, "--staged")
    assert (
        "tools/env_guard.py: a copy of the house's checks/check_git_env.py" in r.stderr + r.stdout
    ), r.stderr


def test_a_script_that_shares_a_few_lines_is_not_a_copy(goh: Path, repo: Path) -> None:
    """A fallback that mirrors a handful of the library's lines (scripts' tui_fallback.sh measured
    0.11 of lib.sh's lines, the highest share any estate file holds) is a script of its own."""
    lines = [line for line in _house("lib.sh").splitlines() if len(line.strip()) >= 12][:8]
    write(repo, "lib/tui_fallback.sh", "#!/usr/bin/env bash\n" + "\n".join(lines) + "\n")
    git(repo, "add", "-A")
    r = _structural(goh, repo, "--staged")
    assert f"✓ {LABEL}" in r.stdout, r.stdout + r.stderr


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
