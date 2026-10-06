"""The proven cache's identity is computed once per process tree (2026-10-06).

`proven_identity` (gates/_proven.sh) -- the gates checkout's identity and every toolchain's version
(`rustc -V`, `cargo -V`, `python3 -V`, `swift --version`) -- took ~110 ms, and each step's key was
computed twice (looked up before, re-keyed after), in every group of every gate: a media_server
push computes it ~174 times. Its run-constant half is now asked once, when the gate validates its
proven settings, and exported for the same working directory, PATH, toolchain override, gates
root and GOH_LIVE mode; anything else is asked again.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT, commit_all, hermetic_env, write

pytestmark = pytest.mark.skipif(shutil.which("rustc") is None, reason="rustc not installed")


def _shimmed_rustc(tmp_path: Path) -> tuple[Path, Path]:
    shim, log = tmp_path / "shim", tmp_path / "rustc.log"
    shim.mkdir()
    real = shutil.which("rustc")
    (shim / "rustc").write_text(f'#!/bin/sh\necho "$*" >> "{log}"\nexec "{real}" "$@"\n')
    (shim / "rustc").chmod(0o755)
    return shim, log


def test_a_two_step_local_ci_asks_the_toolchain_once(tmp_path: Path, repo: Path) -> None:
    write(repo, "src/lib.rs", "pub fn f() {}\n")
    write(repo, ".gatesrc", 'GOH_CI_STEPS="true;true --second"\n')
    commit_all(repo)
    shim, log = _shimmed_rustc(tmp_path)
    env = hermetic_env(drop_git=True)
    env["PATH"] = f"{shim}:{env['PATH']}"
    r = subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "local_ci.sh"), str(repo)],
        cwd=repo, env=env, capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    asked = log.read_text().splitlines() if log.exists() else []
    assert asked.count("-V") == 1, asked


def test_an_inherited_identity_for_another_directory_is_not_taken(
    tmp_path: Path, repo: Path
) -> None:
    write(repo, "src/lib.rs", "pub fn f() {}\n")
    commit_all(repo)
    shim, log = _shimmed_rustc(tmp_path)
    env = hermetic_env(
        drop_git=True, GOH_PROVEN_IDENTITY_FOR="/elsewhere|x", GOH_PROVEN_IDENTITY="tool rustc FAKE"
    )
    env["PATH"] = f"{shim}:{env['PATH']}"
    script = f'. "{REPO_ROOT}/gates/_proven.sh"; proven_settings; proven_identity'
    r = subprocess.run(["bash", "-c", script], cwd=repo, env=env, capture_output=True, text=True)
    assert "FAKE" not in r.stdout and "tool rustc rustc" in r.stdout, r.stdout + r.stderr
