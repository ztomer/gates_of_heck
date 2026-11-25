"""`export_of(repo, rev, cache)`: the export of REV a gate run from REPO finds in its CACHE.

A gate of a goh checkout runs from `git archive` of the commit (gates/_from_head.sh): the whole
tree, 2,600 files, a file event to write and one to remove each (GOH.FSEVQUIET). For a test whose
subject is what the gates SAY from there, never how the export is made (tests/test_gates_from_head
.py, tests/test_self_gate.py), it is clones of the run's tree as it stands (tests/_this_tree.py),
put to REV's own bytes by git: what git finds another is checked out, what REV has not is removed.
Pinned by tests/test_tree_exports.py.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from _this_tree import tree_template
from _tree_clone import clone_tree
from timing_bounds import patience

# What gates/_from_head.sh `goh_export_rev` adds to the tree.
MARKS = (".goh-head", ".goh-root", ".goh-live", ".goh-used", "bin/goh")


def _git(repo: Path, *args: str, **env: str) -> str:
    base = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, stdin=subprocess.DEVNULL, timeout=patience(60),
                          env=base | env).stdout  # fmt: skip


def _mark(repo: Path, rev: str, out: Path) -> None:
    (out / ".goh-head").write_text(f"{rev}\n")
    (out / ".goh-root").write_text(_git(repo, "rev-list", "--max-parents=0", "HEAD"))
    (out / "bin").mkdir(exist_ok=True)
    shutil.copy2(out / "gates" / "_export_bin_goh.sh", out / "bin" / "goh")
    (out / "bin" / "goh").chmod(0o755)
    (out / ".goh-live").write_text(f"{repo.resolve()}\n")
    (out / ".goh-used").write_text("")


def export_of(repo: Path, rev: str, cache: Path, *but: str, of: Path | None = None) -> Path:
    """The export at CACHE/REV, without its top-level names BUT (what no gate run from it reads);
    cloned from the template OF, the run's own tree where none is named."""
    tree = of or tree_template()
    out, git_dir = cache / rev, (repo / ".git").resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if but:
        out.mkdir()
        for part in tree.iterdir():
            if part.name not in (".git", *but):
                clone_tree(part, out / part.name)
    else:  # whole: the tree the suite's other clones of it are pooled with
        clone_tree(tree, out)
        shutil.rmtree(out / ".git")
    index = git_dir / "export-index"
    env = {"GIT_DIR": str(git_dir), "GIT_WORK_TREE": str(out), "GIT_INDEX_FILE": str(index)}
    _git(out, "read-tree", rev, **env)
    if but:
        _git(out, "rm", "-q", "-r", "--cached", "--ignore-unmatch", "--", *but, **env)
    subprocess.run(["git", "-C", str(out), "update-index", "-q", "--refresh"], check=False,
                   capture_output=True, stdin=subprocess.DEVNULL, timeout=patience(60),
                   env=os.environ | env)  # fmt: skip
    for name in _git(out, "ls-files", "-z", "--others", **env).split("\0"):
        if name:
            (out / name).unlink()
    _git(out, "checkout-index", "-q", "-f", "-a", **env)
    index.unlink()
    _mark(repo, rev, out)
    return out
