"""A commit is never let through on what its push will refuse: the cheap Rust policies run staged.

2026-10-06: a commit with `assert!(x.is_empty())` in a new unit test passed this repo's pre-commit
and was refused minutes later by the push gate's rust layer (`goh empty-assert`). Both policies --
no `#[allow]`/`#[expect]`, no emptiness asserts -- are native scans of milliseconds, so the
structural layer runs them over the STAGED `.rs` files: only what the commit touches, so a repo
with old violations is not made red by an unrelated commit.
"""

import subprocess

from conftest import commit_all, run_gate, stage, write

ALLOW = "#[" + "allow(dead_code)]\nfn f() {}\n"
EMPTY = "#[test]\nfn t() {\n    let v: Vec<u8> = Vec::new();\n    assert!(v.is_" + "empty());\n}\n"


def _staged(repo, rel, text):
    write(repo, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\nedition = "2021"\n')
    commit_all(repo)
    write(repo, rel, text)
    stage(repo, rel)
    return run_gate(repo, "gates/structural.sh", "--staged")


def test_a_staged_allow_is_refused_at_commit(repo) -> None:
    r = _staged(repo, "src/lib.rs", ALLOW)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "src/lib.rs" in r.stdout + r.stderr


def test_a_staged_emptiness_assert_is_refused_at_commit(repo) -> None:
    r = _staged(repo, "src/lib.rs", EMPTY)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "emptiness assert" in r.stdout + r.stderr


def test_an_old_violation_in_an_untouched_file_does_not_block_a_commit(repo) -> None:
    write(repo, "src/old.rs", ALLOW)
    r = _staged(repo, "src/lib.rs", "pub fn g() {}\n")
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_commit_of_only_a_manifest_is_not_a_blind_scan(repo) -> None:
    write(repo, "src/lib.rs", "pub fn g() {}\n")
    commit_all(repo)
    write(repo, "Cargo.toml", '[package]\nname = "x"\nversion = "0.2.0"\nedition = "2021"\n')
    stage(repo, "Cargo.toml")
    r = run_gate(repo, "gates/structural.sh", "--staged")
    assert r.returncode == 0, r.stdout + r.stderr
