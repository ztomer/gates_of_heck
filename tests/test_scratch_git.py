"""checks/_gitutil.py `scratch_git`: git inits a process's first scratch repository, and each later
one is the same empty `.git` written from memory, with no seed directory (GOH.FSEVQUIET)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "checks"))
import _gitutil


def _tree(repo: Path) -> dict[str, bytes | None]:
    top = repo / ".git"
    return {
        p.relative_to(top).as_posix(): p.read_bytes() if p.is_file() else None
        for p in top.rglob("*")
    }


def test_each_scratch_repository_is_gits_own_first_and_one_process_is_spawned(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(_gitutil, "_EMPTY_GIT", {})
    ran, real = [], subprocess.run

    def run(argv, **kw):
        ran.append(argv)
        return real(argv, **kw)

    monkeypatch.setattr(_gitutil.subprocess, "run", run)
    one, two = tmp_path / "one", tmp_path / "two"
    for d in (one, two):
        d.mkdir()
        _gitutil.scratch_git(str(d))
        (d / "f").write_text("x\n")
    inits = [a for a in ran if "init" in a]
    first, later = _tree(one), _tree(two)
    files = {k: v for k, v in first.items() if v is not None}
    assert len(inits) == 1 and later == files | {"objects": None, "refs": None}, inits
    assert "HEAD" in files and set(later) < set(first)  # git's own, less its empty directories
    for d in (one, two):
        for argv in (
            ["add", "-A"],
            ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "m"],
        ):
            real(["git", "-C", str(d), *argv], check=True, capture_output=True,
                 env=hermetic_env(True), stdin=subprocess.DEVNULL)  # fmt: skip
        real(["git", "-C", str(d), "tag", "v1"], check=True, stdin=subprocess.DEVNULL)
