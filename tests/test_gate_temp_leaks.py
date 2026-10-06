"""A gate leaves nothing in $TMPDIR (the class ZoneWM measured, 2026-10-06).

ZoneWM counted ~4,700 `goh-rust-scope.*` and ~2,200 other `goh*` entries in the shared temp dir,
every one from a gate that had finished. Two causes, both here: the EXIT trap removed its logs with
an unquoted `for`, so a gate named `python (staged)` split its own log name on the space and
removed nothing; and the rust gate's scope file was registered with no cleanup at all. Every gate
now registers what it creates with ONE helper (`goh_cleanup_add`), read back line by line.
Each test runs a real gate under a private TMPDIR and asserts it ends EMPTY.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT, stage, write
from test_rust_gate import CURRENT, clone, warm_crate  # noqa: F401  # warm_crate is a fixture


def _left(tmpdir: Path) -> list[str]:
    return sorted(p.name for p in tmpdir.iterdir())


def test_a_gate_whose_name_has_a_space_removes_its_log(repo, tmp_path) -> None:
    tmpdir = tmp_path / "t"
    tmpdir.mkdir()
    write(repo, "a.py", "x = 1\n")
    stage(repo, "a.py")
    subprocess.run(["bash", str(REPO_ROOT / "gates" / "py_staged.sh"), "."], cwd=repo,
                   capture_output=True, text=True, env={**os.environ, "TMPDIR": str(tmpdir)})  # fmt: skip
    assert _left(tmpdir) == []


@pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed")
def test_the_rust_gate_removes_its_scope_file(tmp_path, warm_crate) -> None:  # noqa: F811
    tmpdir = tmp_path / "t"
    tmpdir.mkdir()
    c = clone(warm_crate, tmp_path / "crate")
    r = subprocess.run(["/bin/bash", str(CURRENT), str(c)], cwd=c, capture_output=True,
                       text=True, env={**os.environ, "TMPDIR": str(tmpdir)})  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert [n for n in _left(tmpdir) if n.startswith("goh")] == []


def test_structural_full_leaves_nothing_in_tmpdir(tmp_path) -> None:
    """The whole layer 1, every self-proof and the empty-scope sweep included, on this repo."""
    tmpdir = tmp_path / "t"
    tmpdir.mkdir()
    r = subprocess.run(["bash", str(REPO_ROOT / "gates" / "structural.sh"), "--full"],
                       cwd=REPO_ROOT, capture_output=True, text=True, timeout=600,
                       env={**os.environ, "TMPDIR": str(tmpdir) + "/"})  # fmt: skip
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert _left(tmpdir) == []
