"""A read-only tree in a consumer's pytest temp root never fails a gate, and never fails silently.

ZeroThunder's push gate (2026-11-12) passed every check, then went red with NO `✗` line: a consumer
test left a tree of directories without `u+w` in its tmp_path, `rm -rf "$PYTEST_DEBUG_TEMPROOT"` hit
1284 "Permission denied" errors, and under `set -e` the failed `rm` ended the gate. pytest's own
cleanup copes with a read-only tree; goh's did not. The helper is `lib/rm_tree.sh`: make the tree
writable, remove it, and if a residue is left say so by name (a warning, not a failure: every check
had already passed, and a leftover temp dir is not a defect in the code under test).
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env, write

pytestmark = pytest.mark.skipif(
    shutil.which("ruff") is None or shutil.which("pytest") is None,
    reason="ruff/pytest not on PATH",
)

LEAVES_A_READ_ONLY_TREE = """\
import os


def test_leaves_a_read_only_tree(tmp_path):
    deep = tmp_path / "export" / "inner"
    deep.mkdir(parents=True)
    (deep / "f.txt").write_text("x")
    os.chmod(deep, 0o555)
    os.chmod(deep.parent, 0o555)
"""


def _make_writable(root: Path) -> None:
    for dirpath, dirnames, _ in os.walk(root):
        for d in [dirpath, *(os.path.join(dirpath, n) for n in dirnames)]:
            Path(d).chmod(0o755)


def _gate(repo: Path, tmpdir: Path) -> subprocess.CompletedProcess:
    write(repo, "pyproject.toml", '[project]\nname = "ro"\nversion = "0"\n')
    write(repo, "app/__init__.py", "")
    write(repo, "tests/test_ro.py", LEAVES_A_READ_ONLY_TREE)
    tmpdir.mkdir()
    return subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "py_gate.sh"), str(repo)],
        cwd=repo,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=hermetic_env(TMPDIR=str(tmpdir)),
        check=False,
    )


def test_a_read_only_tree_in_the_temp_root_does_not_fail_the_gate(repo, tmp_path):
    tmpdir = tmp_path / "tmpdir"
    try:
        r = _gate(repo, tmpdir)
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert "all python gates passed" in r.stdout, out
        assert not list(tmpdir.glob("goh-pytest.*")), "the temp root outlived a passing run"
    finally:
        _make_writable(tmpdir)


def _helper(path: Path, tmpdir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", "-c", '. "$1/tui/lib.sh"; . "$1/lib/rm_tree.sh"; goh_rm_tree "$2"', "_",
         str(REPO_ROOT), str(path)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=hermetic_env(TMPDIR=str(tmpdir)),
        check=False,
    )  # fmt: skip


def test_the_helper_removes_a_read_only_tree(tmp_path):
    tree = tmp_path / "ro"
    (tree / "a" / "b").mkdir(parents=True)
    (tree / "a" / "b" / "f").write_text("x")
    (tree / "a" / "b").chmod(0o555)
    (tree / "a").chmod(0o555)
    r = _helper(tree, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not tree.exists()


def test_a_residue_that_cannot_be_removed_is_a_named_warning_not_a_silent_red(tmp_path):
    """A parent without `w` cannot lose its child however it is chmod-ed: removal fails."""
    parent = tmp_path / "locked"
    child = parent / "goh-pytest.x"
    child.mkdir(parents=True)
    parent.chmod(0o555)
    try:
        r = _helper(child, tmp_path)
        assert r.returncode == 0, r.stdout + r.stderr
        assert str(child) in r.stdout + r.stderr, "the warning must name the residue"
        assert "⚠" in r.stdout + r.stderr or "warn" in (r.stdout + r.stderr).lower()
        assert child.exists()
    finally:
        parent.chmod(stat.S_IRWXU)


def test_no_script_removes_the_pytest_temp_root_with_a_bare_rm():
    """The class: a root a consumer wrote into goes through `goh_rm_tree`, in every runner."""
    bare = []
    for path in sorted([*REPO_ROOT.glob("gates/*.sh"), *REPO_ROOT.glob("tools/*.sh")]):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if "rm -rf" in code and "PYTEST_DEBUG_TEMPROOT" in code:
                bare.append(f"{path.name}:{n}")
    assert not bare, f"bare rm -rf of a pytest temp root (use goh_rm_tree): {bare}"
