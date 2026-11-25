"""tests/_tree_exports.py: an export made of clones is the export the gates would make."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
from _grown import built
from _tree_clone import process_template
from _tree_exports import MARKS, export_of
from conftest import REPO_ROOT, commit_all, git, write
from timing_bounds import patience


def _holds(out: Path) -> dict[str, tuple[str, bool]]:
    """{path: (its blob's id, executable)} of what the export holds, its own marks apart."""
    files = [p for p in out.rglob("*") if p.is_file() or p.is_symlink()]
    names = sorted(r for r in (str(p.relative_to(out)) for p in files) if r not in MARKS)
    ids = subprocess.run(["git", "hash-object", "--no-filters", "--stdin-paths"], cwd=out,
                         text=True, input="\n".join(names), capture_output=True, check=True,
                         timeout=patience(60)).stdout.split()  # fmt: skip
    return {n: (i, os.access(out / n, os.X_OK)) for n, i in zip(names, ids, strict=True)}


def _tree(repo: Path, rev: str) -> dict[str, tuple[str, bool]]:
    rows = (ln.split("\t") for ln in git(repo, "ls-tree", "-r", rev).splitlines())
    return {path: (meta.split()[2], meta.startswith("100755")) for meta, path in rows}


def _small(tree: Path) -> None:
    fast_init(tree)
    write(tree, "README.md", "the tree\n")
    write(tree, "gates/_export_bin_goh.sh", (REPO_ROOT / "gates/_export_bin_goh.sh").read_text())
    write(tree, "gates/x.sh", "exit 0\n").chmod(0o755)
    for name in ("tools/gone.sh", "lib/gone.py", "lib/kept.py", "tests/t.py"):
        write(tree, name, f"{name}\n")
    commit_all(tree)


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    copy = built(tmp_path / "copy", "export-kit", _small)
    (copy / "README.md").write_text("another\n")
    (copy / "lib" / "a_new_one.py").write_text("NEW = 1\n")
    (copy / "gates" / "x.sh").chmod(0o644)
    git(copy, "rm", "-q", "tools/gone.sh", "lib/gone.py")
    git(copy, "add", "-A")
    return copy


def _export(copy: Path, rev: str, cache: Path, *but: str) -> Path:
    return export_of(copy, rev, cache, *but, of=process_template("export-kit", _small))


def test_the_export_holds_the_trees_files_and_nothing_else(copy: Path, tmp_path: Path) -> None:
    tree = git(copy, "write-tree").strip()
    out = _export(copy, tree, tmp_path / "cache")
    assert out == tmp_path / "cache" / tree
    assert _holds(out) == _tree(copy, tree)
    assert len(_tree(copy, tree)) == 6
    assert not (out / ".git").exists(), "an archive has no history"
    less = _export(copy, tree, tmp_path / "less", "tests", "README.md")
    want = {p: v for p, v in _tree(copy, tree).items() if not p.startswith(("tests/", "README.md"))}
    assert _holds(less) == want
    assert git(copy, "write-tree").strip() == tree, "the repository's own index moved"


def test_the_export_is_marked_as_the_gates_mark_theirs(copy: Path, tmp_path: Path) -> None:
    rev = git(copy, "rev-parse", "HEAD").strip()
    out = _export(copy, rev, tmp_path / "cache")
    script = '. "$1/gates/_from_head.sh"; goh_export_rev "$2" "$3" HEAD; echo "$_goh_export"'
    r = subprocess.run(["bash", "-c", script, "_", str(REPO_ROOT), str(copy), rev], text=True,
                       env=os.environ | {"GOH_HEAD_CACHE": str(tmp_path / "theirs")},
                       capture_output=True, check=True, timeout=patience(60),
                       stdin=subprocess.DEVNULL)  # fmt: skip
    theirs = Path(r.stdout.strip())

    def read(top: Path) -> dict[str, tuple[bytes, bool]]:
        files = [p for p in top.rglob("*") if p.is_file()]
        return {str(p.relative_to(top)): (p.read_bytes(), os.access(p, os.X_OK)) for p in files}

    assert read(out) == read(theirs)
    assert set(MARKS) < set(read(theirs))
