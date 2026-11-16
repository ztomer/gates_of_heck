"""The proven cache's identity is computed once per process tree (2026-10-06).

`proven_identity` (gates/_proven.sh) -- the gates checkout's identity and every toolchain's version
(`rustc -V`, `cargo -V`, `python3 -V`, `swift --version`) -- took ~110 ms, and each step's key was
computed twice (looked up before, re-keyed after), in every group of every gate: a private server
workspace push computes it ~174 times. Its run-constant half is now asked once, when the gate
validates its proven settings, and exported for the same working directory, PATH, toolchain
override, gates root and GOH_LIVE mode; anything else is asked again.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
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
        cwd=repo, env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    asked = log.read_text().splitlines() if log.exists() else []
    assert asked.count("-V") == 1, asked


def test_an_inherited_identity_for_another_directory_is_not_taken(
    tmp_path: Path, repo: Path
) -> None:
    write(repo, "src/lib.rs", "pub fn f() {}\n")
    commit_all(repo)
    shim, _log = _shimmed_rustc(tmp_path)
    env = hermetic_env(
        drop_git=True, GOH_PROVEN_IDENTITY_FOR="/elsewhere|x", GOH_PROVEN_IDENTITY="tool rustc FAKE"
    )
    env["PATH"] = f"{shim}:{env['PATH']}"
    script = f'. "{REPO_ROOT}/gates/_proven.sh"; proven_settings; proven_identity'
    r = subprocess.run(
        ["bash", "-c", script],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert "FAKE" not in r.stdout and "tool rustc rustc" in r.stdout, r.stdout + r.stderr


# ── R3.13: the roots the identity KEYS, named for `goh rust-scope` (antiknob) ────────────────────


def _keyed_roots(tmp_path, gates_root, goh_dir, head_cache):

    script = (
        f'. "{REPO_ROOT}/gates/_proven.sh"; PROVEN_GOH_ROOT="$1"; GOH_DIR="$2"; '
        'GOH_HEAD_CACHE="$3"; proven_keyed_roots'
    )
    out = subprocess.run(
        ["bash", "-c", script, "probe", str(gates_root), str(goh_dir), str(head_cache)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    return out.stdout.strip(), out.stderr


def test_every_gates_root_is_keyed_by_the_paths_its_content_covers(tmp_path):
    """A path dependency may be left to the identity only where the identity carries its CONTENT:
    the gates and the goh binary's build inputs (lib/gate_identity.py PARTS), read by bytes in a
    checkout, a head export and a plain directory alike (GOH.PROVENIDENTITY). Anything else in a
    gates root (its docs/) is no part of the key, so it is not named."""

    checkout = tmp_path / "checkout"
    (checkout / "crates" / "goh-tui").mkdir(parents=True)
    (checkout / "docs").mkdir()
    fast_init(checkout)
    head_cache = tmp_path / "head"
    export = head_cache / ("a" * 40)
    (export / "gates").mkdir(parents=True)
    (export / "Cargo.lock").write_text("version = 4\n")
    roots, err = _keyed_roots(tmp_path, checkout, export, head_cache)
    want = [str(checkout.resolve() / "crates"), str(export.resolve() / "gates"),
            str(export.resolve() / "Cargo.lock")]  # fmt: skip
    assert roots.split(":") == want, (roots, err)
    bare = tmp_path / "bare"
    (bare / "crates").mkdir(parents=True)
    roots, err = _keyed_roots(tmp_path, checkout, bare, head_cache)
    assert roots.split(":") == [
        str(checkout.resolve() / "crates"),
        str(bare.resolve() / "crates"),
    ], (roots, err)
