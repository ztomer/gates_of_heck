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


# ── G1e (2026-10-25): the release gate's `cargo fmt --check`, at commit ──────────────────────────
#
# app_updates c0d3a57: the commit hook passed a TEST file that the release gate's
# `cargo fmt --all -- --check` refused. The commit-time Rust layer judged policy and never shape,
# so it was weaker than the gate it feeds. Now every staged `.rs` -- tests included -- is judged by
# rustfmt the way `cargo fmt` judges it: the STAGED blob, the package's edition (inherited from
# the workspace when the manifest says so), and the nearest rustfmt.toml.

MISFORMATTED = "#[test]\nfn t(){let x=1;assert_eq!(x,1);}\n"
FORMATTED = "#[test]\nfn t() {\n    let x = 1;\n    assert_eq!(x, 1);\n}\n"


def test_a_misformatted_staged_test_file_is_refused_at_commit(repo) -> None:
    """app_updates' shape: a test file under tests/, staged, not rustfmt-clean."""
    r = _staged(repo, "tests/it.rs", MISFORMATTED)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "tests/it.rs" in r.stdout + r.stderr, r.stdout + r.stderr
    assert "cargo fmt" in r.stdout + r.stderr, r.stdout + r.stderr


def test_a_formatted_staged_test_file_passes(repo) -> None:
    r = _staged(repo, "tests/it.rs", FORMATTED)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_staged_blob_is_judged_not_the_working_tree(repo) -> None:
    """Formatted in the index, dirty on disk: the commit carries the index."""
    write(repo, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\nedition = "2021"\n')
    commit_all(repo)
    write(repo, "tests/it.rs", FORMATTED)
    stage(repo, "tests/it.rs")
    write(repo, "tests/it.rs", MISFORMATTED)
    r = run_gate(repo, "gates/structural.sh", "--staged")
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_packages_edition_is_the_one_rustfmt_is_given(repo) -> None:
    """`async fn` does not parse under rustfmt's own default (2015); cargo fmt passes the
    package's edition, and so must this."""
    r = _staged(repo, "src/lib.rs", "pub async fn f() {}\n")
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_workspace_inherited_edition_is_followed(repo) -> None:
    write(
        repo,
        "Cargo.toml",
        '[workspace]\nmembers = ["crates/a"]\n\n[workspace.package]\nedition = "2021"\n',
    )
    write(
        repo,
        "crates/a/Cargo.toml",
        '[package]\nname = "a"\nversion = "0.1.0"\nedition.workspace = true\n',
    )
    commit_all(repo)
    write(repo, "crates/a/src/lib.rs", "pub async fn f() {}\n")
    stage(repo, "crates/a/src/lib.rs")
    r = run_gate(repo, "gates/structural.sh", "--staged")
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_nearest_rustfmt_toml_is_honoured(repo) -> None:
    """Tabs are refused by rustfmt's defaults and are the shape when the CRATE says hard_tabs.
    The config sits in the crate's directory, not the root: rustfmt reading stdin finds a config
    only from its working directory, so a root config would pass without being looked for."""
    tabbed = "pub fn f() {\n\tlet _x = 1;\n}\n"
    write(repo, "Cargo.toml", '[workspace]\nmembers = ["crates/a"]\n')
    write(
        repo, "crates/a/Cargo.toml", '[package]\nname = "a"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    write(repo, "crates/a/rustfmt.toml", "hard_tabs = true\n")
    commit_all(repo)
    write(repo, "crates/a/src/lib.rs", tabbed)
    stage(repo, "crates/a/src/lib.rs")
    r = run_gate(repo, "gates/structural.sh", "--staged")
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_excluded_path_is_not_judged(repo) -> None:
    """GOH_EXCLUDE (a vendored crate) is the repo's own statement of what its gates judge."""
    write(repo, ".gatesrc", "GOH_EXCLUDE='^vendor/'\n")
    write(
        repo, "vendor/v/Cargo.toml", '[package]\nname = "v"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    r = _staged(repo, "vendor/v/src/lib.rs", "pub fn f(){}\n")
    assert r.returncode == 0, r.stdout + r.stderr
