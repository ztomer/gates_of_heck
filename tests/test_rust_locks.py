"""lib/rust_locks.py: which locks the rust gate audits, and from which directory (GOH.EA2)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import rust_locks
from conftest import git


def _cfgs(*dirs: str):
    return lambda d: d in dirs


def test_a_lock_runs_from_its_own_dir_without_a_config():
    assert rust_locks.plan(["a/b/Cargo.lock", "Cargo.lock"], _cfgs()) == [
        (".", "Cargo.lock"),
        ("a/b", "a/b/Cargo.lock"),
    ]


def test_the_nearest_config_at_or_above_wins():
    has = _cfgs(".", "fuzz")
    assert rust_locks.plan(["fuzz/Cargo.lock", "crates/x/Cargo.lock", "Cargo.lock"], has) == [
        (".", "Cargo.lock"),
        (".", "crates/x/Cargo.lock"),
        ("fuzz", "fuzz/Cargo.lock"),
    ]


def test_each_lock_once():
    assert rust_locks.plan(["Cargo.lock", "Cargo.lock"], _cfgs()) == [(".", "Cargo.lock")]


def test_an_untracked_lock_beside_a_tracked_manifest_is_planned(repo: Path):
    """A library crate gitignores its Cargo.lock. Before the advisory step moved to the repo group
    (6cb4623) every crate's run audited the lock beside its manifest; `--each-crate` now runs that
    step ONCE, for its first crate, so a second crate's ignored lock was audited by nothing."""
    for d in ("lib", "app", "nolock"):
        (repo / d).mkdir()
        (repo / d / "Cargo.toml").write_text(f'[package]\nname = "{d}"\n')
    (repo / "lib" / ".gitignore").write_text("Cargo.lock\n")
    (repo / "lib" / "Cargo.lock").write_text("version = 4\n")
    (repo / "app" / "Cargo.lock").write_text("version = 4\n")
    (repo / "target").mkdir()  # an untracked lock with no tracked manifest is nobody's
    (repo / "target" / "Cargo.lock").write_text("version = 4\n")
    (repo / ".gitignore").write_text("target/\n")
    git(repo, "add", "-A")
    assert rust_locks.repo_plan(repo) == [
        ("app", "app/Cargo.lock"),
        ("lib", "lib/Cargo.lock"),
    ]
