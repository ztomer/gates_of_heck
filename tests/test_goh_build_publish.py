"""build-goh.sh never publishes bin/goh from uncommitted goh sources.

bin/goh is the LIVE structural gate of every repo whose hooks delegate here.
On 2026-09-23 a port in progress was built into it from uncommitted sources and
refused another repo's commits with a false ceiling verdict for an hour. The
script runs against a throwaway clone so both directions can be driven.
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
        env={"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", ""),
             "GOH_BUILD_PUBLISH_CHECK_ONLY": "1", **env},
    )


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    tree = tmp_path / "goh"
    subprocess.run(["git", "clone", "--quiet", "--depth", "1", f"file://{ROOT}", str(tree)], check=True)
    shutil.copy2(SCRIPT, tree / "scripts" / "build-goh.sh")  # the script under test, committed or not
    return tree


def test_a_clean_tree_publishes(clone: Path) -> None:
    r = check(clone)
    assert r.returncode == 0, r.stderr
    assert "publish ok" in r.stdout


def test_uncommitted_goh_sources_are_refused_and_named(clone: Path) -> None:
    main = clone / "crates" / "goh" / "src" / "main.rs"
    main.write_text(main.read_text() + "\n// in progress\n")
    r = check(clone)
    assert r.returncode == 1
    assert "refusing to publish bin/goh" in r.stderr
    assert "crates/goh/src/main.rs" in r.stderr
    assert "GOH_BIN" in r.stderr, "the refusal says how to try the dev build in one repo"


def test_the_override_publishes_on_purpose(clone: Path) -> None:
    (clone / "Cargo.toml").write_text((clone / "Cargo.toml").read_text() + "\n")
    assert check(clone).returncode == 1
    assert check(clone, GOH_BUILD_DIRTY="1").returncode == 0


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
