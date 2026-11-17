//! `goh seed-settle` and the seeded checks' journals within the grace (GOH.TUIMARKSSETTLE).

// Test-only crate: helpers `expect` fixture writes, and a fixture that
// cannot be built is a broken test, not a result.
#![cfg(test)]

use goh_testkit::{goh_at, repo_with, Out, Repo};

fn goh(repo: Option<&Repo>, args: &[&str], envs: &[(&str, &str)]) -> Out {
    goh_at(
        std::path::Path::new(env!("CARGO_BIN_EXE_goh")),
        repo,
        args,
        envs,
    )
    .expect("goh runs")
}

/// A commit within the grace that fixes a seeded hand-rolled mark journals the settle, and
/// `goh seed-settle` retires the entry (GOH.TUIMARKSSETTLE): the grace changes the verdict, never
/// the record, or the stale entries pile up unseen and meet each consumer as a wall of red at its
/// first push after the grace.
#[test]
fn a_commit_within_the_grace_that_fixes_a_seeded_tui_mark_retires_its_entry() {
    let store = tempfile::tempdir().expect("store");
    let pending = tempfile::tempdir().expect("pending");
    let r = repo_with(&[("setup.sh", "#!/bin/bash\necho \"✗ adb not found\"\n")]).expect("fixture");
    r.git(&["commit", "-q", "-m", "root"]).expect("commit");
    let root = goh_testkit::git_command()
        .args(["rev-list", "--max-parents=0", "HEAD"])
        .current_dir(r.path())
        .output()
        .expect("git");
    let root = String::from_utf8_lossy(&root.stdout).trim().to_string();
    let file = store.path().join("tui_marks_seed.json");
    std::fs::write(
        &file,
        format!(
            "{{\"entries\": [{{\"root\": \"{root}\", \"repo\": \"r\", \"path\": \"setup.sh\", \
             \"count\": 1, \"reason\": \"reviewed: a test\"}}]}}\n"
        ),
    )
    .expect("seed");
    r.write("setup.sh", "#!/bin/bash\nerr \"adb not found\"\n")
        .expect("fix");
    r.git(&["add", "-A"]).expect("git");
    let st = store.path().to_str().expect("utf-8");
    let pe = pending.path().to_str().expect("utf-8");
    let out = goh(
        Some(&r),
        &["tui-marks", "--staged"],
        &[
            ("GOH_SEED_DIR", st),
            ("GOH_SEED_PENDING", pe),
            ("GOH_GRACE", "within"),
        ],
    );
    assert!(out.status.success(), "{}", out.text());
    let out = goh(
        Some(&r),
        &["seed-settle", pe],
        &[("GOH_SEED_DIR", st), ("GOH_GRACE", "within")],
    );
    assert!(out.status.success(), "{}", out.text());
    let held: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&file).expect("read")).expect("json");
    assert_eq!(held["entries"].as_array().map(Vec::len), Some(0), "{held}");
}

/// `goh seed-settle --help` says how a journal is produced, so a lane clearing its seeds need not
/// find it by reading the hook (GOH.TUIMARKSSETTLE).
#[test]
fn seed_settle_help_names_the_journal_and_its_producers() {
    let out = goh(None, &["seed-settle", "--help"], &[]);
    assert!(out.status.success(), "{}", out.text());
    for want in ["GOH_SEED_PENDING", "tui-marks --staged", "GOH_SEED_DIR"] {
        assert!(out.stdout.contains(want), "{want}: {}", out.stdout);
    }
}
