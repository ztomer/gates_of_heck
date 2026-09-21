//! The fixtures are code too: every helper's success and failure path.

use std::path::Path;

use goh_testkit::{esc, g, goh_at, repo_with, Out, Repo};

#[test]
fn glyphs_and_escapes_are_built_from_numbers() {
    assert_eq!(g(0x41), "A");
    assert_eq!(g(0x2705).chars().count(), 1);
    assert_eq!(g(0xD800), "\u{FFFD}", "a surrogate is not a scalar value");
    assert_eq!(esc("n"), format!("{}n", char::from(92u8)));
}

#[test]
fn repo_with_stages_files_and_git_reports_failures() {
    let r = repo_with(&[("a.txt", "x\n"), ("sub/b.txt", "y\n")]).expect("fixture");
    assert!(r.path().join(".git").is_dir());
    assert!(r.path().join("sub/b.txt").is_file());
    r.git(&["diff", "--cached", "--quiet", "--exit-code", "--", "a.txt"])
        .expect_err("a.txt is staged, so the quiet diff exits 1");
    r.git(&["status", "--porcelain"]).expect("git runs");
    let err = r
        .git(&["no-such-subcommand"])
        .expect_err("unknown subcommand");
    assert!(err.contains("git [\"no-such-subcommand\"] failed"), "{err}");
    r.write("deep/er/c.txt", "z").expect("write with parents");
    assert_eq!(
        std::fs::read_to_string(r.path().join("deep/er/c.txt")).expect("read"),
        "z"
    );
    // A file where a directory is needed makes write fail.
    r.write("a.txt/impossible", "")
        .expect_err("a.txt is a file");
}

#[test]
fn bare_dirs_and_the_runner() {
    let dir = tempfile::tempdir().expect("tempdir");
    let bare = Repo::bare(dir);
    let err = bare.git(&["status"]).expect_err("not a git repo");
    assert!(err.contains("failed"), "{err}");
    // The runner with a stand-in binary: stdout captured, env applied.
    let out: Out = goh_at(
        Path::new("/bin/sh"),
        Some(&bare),
        &["-c", "echo out-$X; echo err >&2"],
        &[("X", "1")],
    )
    .expect("sh runs");
    assert!(out.status.success());
    assert_eq!(out.stdout, "out-1\n");
    assert_eq!(out.text(), "out-1\nerr\n");
    goh_at(Path::new("/definitely/not/a/binary"), None, &[], &[])
        .expect_err("missing binary is an error");
}
