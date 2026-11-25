"""A commit is never let through on what its push will refuse: the cheap Rust policies run staged.

2026-10-06: a commit with `assert!(x.is_empty())` in a new unit test passed this repo's pre-commit
and was refused minutes later by the push gate's rust layer (`goh empty-assert`). Both policies --
no `#[allow]`/`#[expect]`, no emptiness asserts -- are native scans of milliseconds, so the
structural layer runs them over the STAGED `.rs` files: only what the commit touches, so a repo
with old violations is not made red by an unrelated commit.
"""

from conftest import commit_all, run_gate, stage, write

ALLOW = "#[" + "allow(dead_code)]\nfn f() {}\n"
EMPTY = "#[test]\nfn t() {\n    let v: Vec<u8> = Vec::new();\n    assert!(v.is_" + "empty());\n}\n"


MISFORMATTED = "#[test]\nfn t(){let x=1;assert_eq!(x,1);}\n"
FORMATTED = "#[test]\nfn t() {\n    let x = 1;\n    assert_eq!(x, 1);\n}\n"
PACKAGE = '[package]\nname = "{0}"\nversion = "0.1.0"\n{1}\n'
ED = 'edition = "2021"'


# ── G1e (2026-10-25): the release gate's `cargo fmt --check`, at commit ──────────────────────────
#
# app_updates c0d3a57: the commit hook passed a TEST file that the release gate's
# `cargo fmt --all -- --check` refused. The commit-time Rust layer judged policy and never shape,
# so it was weaker than the gate it feeds. Now every staged `.rs` -- tests included -- is judged by
# rustfmt the way `cargo fmt` judges it: the STAGED blob, the package's edition (inherited from
# the workspace when the manifest says so), and the nearest rustfmt.toml.


def test_each_staged_violation_is_refused_at_commit_by_name(repo) -> None:
    """app_updates' shape among them: a test file under tests/, not rustfmt-clean."""
    write(repo, "Cargo.toml", PACKAGE.format("x", ED))
    commit_all(repo)
    # the step says its first refusal, shape before policy: each is put right for the next
    for rel, bad, said, good in (
        ("tests/it.rs", MISFORMATTED, "cargo fmt", FORMATTED),
        ("src/allow.rs", ALLOW, "allow(dead_code)", "pub fn f() {}\n"),
        ("src/empty.rs", EMPTY, "emptiness assert", FORMATTED),
    ):
        write(repo, rel, bad)
        stage(repo, rel)
        r = run_gate(repo, "gates/structural.sh", "--staged")
        out = r.stdout + r.stderr
        assert r.returncode == 1 and rel in out and said in out, out
        write(repo, rel, good)
        stage(repo, rel)


def test_what_cargo_fmt_and_the_repo_allow_commits(repo) -> None:
    """One commit of every shape the gate must not refuse; each was refused alone by a gate that
    missed its own rule."""
    root = '[workspace]\nmembers = ["crates/*"]\n\n[workspace.package]\nedition = "2021"\n\n'
    write(repo, "Cargo.toml", root + PACKAGE.format("x", ED))
    write(repo, "src/old.rs", ALLOW)  # an old violation in a file the commit does not touch
    # `async fn` does not parse under rustfmt's own default (2015); cargo fmt passes the package's
    # edition, its own or the workspace's, and so must this.
    write(repo, "crates/a/Cargo.toml", PACKAGE.format("a", "edition.workspace = true"))
    # Tabs are refused by rustfmt's defaults and are the shape when the CRATE says hard_tabs. The
    # config sits in the crate's directory, not the root: rustfmt reading stdin finds a config only
    # from its working directory, so a root config would pass without being looked for.
    write(repo, "crates/b/Cargo.toml", PACKAGE.format("b", ED))
    write(repo, "crates/b/rustfmt.toml", "hard_tabs = true\n")
    # GOH_EXCLUDE (a vendored crate) is the repo's own statement of what its gates judge.
    write(repo, ".gatesrc", "GOH_EXCLUDE='^vendor/'\n")
    write(repo, "vendor/v/Cargo.toml", PACKAGE.format("v", ED))
    commit_all(repo)
    good = {
        "src/lib.rs": "pub async fn f() {}\n",
        "tests/it.rs": FORMATTED,
        "crates/a/src/lib.rs": "pub async fn f() {}\n",
        "crates/b/src/lib.rs": "pub fn f() {\n\tlet _x = 1;\n}\n",
        "vendor/v/src/lib.rs": "pub fn f(){}\n",
    }
    for rel, text in good.items():
        write(repo, rel, text)
    stage(repo, *good)
    write(repo, "tests/it.rs", MISFORMATTED)  # dirty on disk only: the commit carries the index
    r = run_gate(repo, "gates/structural.sh", "--staged")
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_commit_of_only_a_manifest_is_not_a_blind_scan(repo) -> None:
    write(repo, "src/lib.rs", "pub fn g() {}\n")
    commit_all(repo)
    write(repo, "Cargo.toml", PACKAGE.format("x", ED))
    stage(repo, "Cargo.toml")
    r = run_gate(repo, "gates/structural.sh", "--staged")
    assert r.returncode == 0, r.stdout + r.stderr
