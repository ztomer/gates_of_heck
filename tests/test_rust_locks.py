"""lib/rust_locks.py: which locks the rust gate audits, and from which directory (GOH.EA2)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import rust_locks


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
