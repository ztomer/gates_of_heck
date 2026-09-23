//! The line-cap ceiling and ratchet steps at `--staged` read the INDEX (an
//! `IndexView` with a `.git` pointer), natively, so `cargo llvm-cov` sees
//! the export; `tests/test_line_ceiling_staged_scope.py` drives both
//! pipelines, including real `git commit` hooks.

use goh_testkit::{goh_at, repo_with, Out, Repo};

/// The binary this test run built; the `#[test]` caller unwraps.
fn goh(repo: &Repo, args: &[&str], envs: &[(&str, &str)]) -> std::io::Result<Out> {
    goh_at(
        std::path::Path::new(env!("CARGO_BIN_EXE_goh")),
        Some(repo),
        args,
        envs,
    )
}

fn lines(n: usize) -> String {
    "line\n".repeat(n)
}

/// big.txt: 8 lines, exempt from the 5-line cap, bounded by a ceiling of 8,
/// committed. The `#[test]` caller unwraps.
fn ceiling_repo() -> Result<Repo, String> {
    let r = repo_with(&[
        (
            ".gatesrc",
            "GOH_MAX_LINES=5\nGOH_LINE_EXCLUDE='big\\.txt'\nGOH_LINE_BASELINE=.b.txt\n",
        ),
        ("big.txt", &lines(8)),
        (".b.txt", "8\tbig.txt\n"),
    ])?;
    r.git(&["commit", "-qm", "ceiling"])?;
    Ok(r)
}

const GREW: &str = "big.txt: 8 -> 12";

#[test]
fn the_staged_ratchet_ignores_unstaged_growth() {
    let r = ceiling_repo().expect("fixture");
    r.write("small.txt", "x\n").expect("write");
    r.git(&["add", "small.txt"]).expect("git");
    r.write("big.txt", &lines(12)).expect("write");
    let out = goh(&r, &["structural", "--staged"], &[]).expect("goh runs");
    assert!(out.status.success(), "{}", out.text());
    let out = goh(&r, &["structural", "--full"], &[]).expect("goh runs");
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out.text().contains(GREW), "{}", out.text());
}

#[test]
fn the_staged_ratchet_fails_staged_growth_whose_fix_is_unstaged() {
    let r = ceiling_repo().expect("fixture");
    r.write("big.txt", &lines(12)).expect("write");
    r.git(&["add", "big.txt"]).expect("git");
    r.write("big.txt", &lines(8)).expect("write");
    let out = goh(&r, &["structural", "--staged"], &[]).expect("goh runs");
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out.text().contains(GREW), "{}", out.text());
    let out = goh(&r, &["structural", "--full"], &[]).expect("goh runs");
    assert!(out.status.success(), "{}", out.text());
}

#[test]
fn the_staged_ceiling_fails_a_dropped_ceiling_restored_only_in_the_tree() {
    let r = ceiling_repo().expect("fixture");
    r.write(".b.txt", "0\t__under_cap_sentinel__\n")
        .expect("write");
    r.git(&["add", ".b.txt"]).expect("git");
    r.write(".b.txt", "8\tbig.txt\n").expect("write");
    let out = goh(&r, &["structural", "--staged"], &[]).expect("goh runs");
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out.text().contains("with NO ceiling"), "{}", out.text());
}

#[test]
fn a_relative_git_index_file_from_a_plain_commit_hook_still_reads_the_index() {
    // `git commit` hands its hook GIT_INDEX_FILE=.git/index. Inside the view
    // `.git` is a FILE, so unpinned that path is "Not a directory".
    let r = ceiling_repo().expect("fixture");
    r.write("big.txt", &lines(12)).expect("write");
    r.git(&["add", "big.txt"]).expect("git");
    r.write("big.txt", &lines(8)).expect("write");
    let env = [("GIT_INDEX_FILE", ".git/index")];
    let out = goh(&r, &["structural", "--staged"], &env).expect("goh runs");
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out.text().contains(GREW), "{}", out.text());
}

#[test]
fn a_set_git_work_tree_is_refused_with_the_reason() {
    let r = ceiling_repo().expect("fixture");
    let tree = r.path().display().to_string();
    let env = [("GIT_WORK_TREE", tree.as_str())];
    let out = goh(&r, &["structural", "--staged"], &env).expect("goh runs");
    assert_eq!(out.status.code(), Some(2), "{}", out.text());
    assert!(
        out.stderr.contains("GIT_WORK_TREE is set"),
        "{}",
        out.text()
    );
}
