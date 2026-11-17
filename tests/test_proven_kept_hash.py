"""A proven key's kept inputs are hashed in a BOUNDED number of processes (GOH.RUSTPROVENKEY).

game_asset_factory's push gate on 8a630b8 (2026-11-15, load 18-47) spent 107 s, 148 s and 151 s in
silent gaps at the boundaries of rust_gate.sh's proven groups: the same tree with GOH_PROVEN=0 ran
the whole Rust layer in 21 s. The time was `_proven_identity_live` (gates/_proven.sh): it keys each
file of every GOH_EXPORT_KEEP entry -- the 89 MB, 3,037-file `.venv` -- by hashing it in a
`$(hash_hex "$p")` of its own (a subshell, `shasum` and `cut` per file: three spawns, 72 s per
call at load 78), and a group asks for it three or four times (the key before, the key after, the
kept-rewrite and moved checks). The instrument that tells the two apart is a `shasum` shim that
counts its own launches: the old loop launched it once per file, the batched one a handful of
times whatever the file count.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env

FILES = 150


def _kept(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    for i in range(FILES):
        d = repo / ".venv" / f"pkg{i % 7}" / "sub dir"  # a space: names are not words
        d.mkdir(parents=True, exist_ok=True)
        (d / f"mod_{i}.py").write_text(f"x = {i}\n")
    (repo / ".venv" / "bin").mkdir()
    (repo / ".venv" / "bin" / "empty").write_text("")
    fast_init(repo)
    shim, log = tmp_path / "shim", tmp_path / "shasum.log"
    shim.mkdir()
    real = shutil.which("shasum")
    (shim / "shasum").write_text(f'#!/bin/sh\necho x >> "{log}"\nexec "{real}" "$@"\n')
    (shim / "shasum").chmod(0o755)
    return repo, shim


def _live(repo: Path, shim: Path) -> subprocess.CompletedProcess:
    env = hermetic_env(drop_git=True, GOH_EXPORT_KEEP=".venv missing.bin")
    env["PATH"] = f"{shim}:{env['PATH']}"
    script = (
        f'. "{REPO_ROOT}/gates/_hash.sh"; . "{REPO_ROOT}/gates/_proven.sh"; '
        "unset PROVEN_ENV_DEFAULT GOH_PROVEN_ENV; _proven_identity_live"
    )
    return subprocess.run(
        ["bash", "-c", script], cwd=repo, env=env, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip


def test_the_kept_inputs_are_hashed_in_a_bounded_number_of_processes(tmp_path: Path) -> None:
    repo, shim = _kept(tmp_path)
    r = _live(repo, shim)
    assert r.returncode == 0, r.stderr
    launched = len((tmp_path / "shasum.log").read_text().splitlines())
    assert launched <= 5, f"{launched} shasum launches for {FILES + 1} kept files"


def test_the_kept_lines_are_unchanged_one_per_file_sorted_by_path(tmp_path: Path) -> None:
    """The format is the record's: a key made before this change must still be this change's."""
    repo, shim = _kept(tmp_path)
    r = _live(repo, shim)
    files = sorted(
        (p for p in (repo / ".venv").rglob("*") if p.is_file()), key=lambda p: str(p).encode()
    )
    want = [
        f"keep {p.relative_to(repo)} {hashlib.sha256(p.read_bytes()).hexdigest()}" for p in files
    ] + ["keep missing.bin absent"]
    assert r.stdout.splitlines() == want, r.stdout[:600]
