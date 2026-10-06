"""build-goh.sh never publishes bin/goh from uncommitted goh sources -- by construction.

bin/goh is the LIVE structural gate of every repo whose hooks delegate here.
On 2026-09-23 a port in progress was built into it from uncommitted sources and
refused another repo's commits with a false ceiling verdict for an hour. That
used to be a REFUSAL of a dirty tree (with an override); since C3 the script
builds an export of HEAD, so uncommitted edits cannot reach the binary and a
dirty checkout is no longer a reason to refuse. The script runs against a
throwaway clone.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-goh.sh"


def check(tree: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(tree / "scripts" / "build-goh.sh")],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ["PATH"],
            "HOME": os.environ.get("HOME", ""),
            "GOH_BUILD_PUBLISH_CHECK_ONLY": "1",
            **env,
        },
    )


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    tree = tmp_path / "goh"
    subprocess.run(
        ["git", "clone", "--quiet", "--depth", "1", f"file://{ROOT}", str(tree)], check=True
    )
    shutil.copy2(
        SCRIPT, tree / "scripts" / "build-goh.sh"
    )  # the script under test, committed or not
    return tree


def test_a_clean_tree_publishes(clone: Path) -> None:
    r = check(clone)
    assert r.returncode == 0, r.stderr
    assert "publish ok" in r.stdout


def test_uncommitted_goh_sources_do_not_block_a_head_build(clone: Path) -> None:
    """The build reads HEAD, so a dirty crates/ is excluded rather than refused."""
    main = clone / "crates" / "goh" / "src" / "main.rs"
    main.write_text(main.read_text() + "\n// in progress\n")
    r = check(clone)
    assert r.returncode == 0, r.stderr
    assert "committed source at HEAD" in r.stdout, r.stdout


@pytest.mark.slow
def test_a_broken_uncommitted_edit_never_reaches_the_published_binary(clone: Path) -> None:
    """The real build, end to end: a working tree that does not even COMPILE still publishes
    HEAD's binary, stamped with HEAD's trees, and the stale-check then finds nothing to do."""
    main = clone / "crates" / "goh" / "src" / "main.rs"
    main.write_text(main.read_text() + "\nthis is not rust\n")
    # DRIFT_BUILD_OK: building goh is this test's subject (tests/_drift_guard.py).
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", ""), "DRIFT_BUILD_OK": "1"}
    script = clone / "scripts" / "build-goh.sh"
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=900)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "NOT in it" in r.stderr and "crates/goh/src/main.rs" in r.stderr, r.stderr
    stamp = subprocess.run(
        [str(clone / "bin" / "goh"), "source-tree"], capture_output=True, text=True
    ).stdout.strip()
    inputs = [f"HEAD:{i}" for i in ("crates", "Cargo.toml", "Cargo.lock", "rust-toolchain.toml")]
    head = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", *inputs], capture_output=True, text=True
    ).stdout.split()
    assert stamp == " ".join(head), stamp
    again = subprocess.run(
        ["bash", str(script), "--if-stale"], capture_output=True, text=True, env=env, timeout=120
    )
    assert again.returncode == 0 and "Building goh" not in again.stdout, again.stdout


def test_a_change_outside_the_goh_sources_does_not_block(clone: Path) -> None:
    (clone / "docs" / "config.md").write_text("edited\n")
    assert check(clone).returncode == 0


def test_a_tree_that_is_not_a_checkout_publishes(tmp_path: Path) -> None:
    tree = tmp_path / "tarball"
    (tree / "scripts").mkdir(parents=True)
    (tree / "crates").mkdir()
    shutil.copy2(SCRIPT, tree / "scripts" / "build-goh.sh")
    r = check(tree, GIT_CEILING_DIRECTORIES=str(tmp_path))
    assert r.returncode == 0, r.stderr
