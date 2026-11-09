//! GOH.SKILLSPATH (2026-11-08): `goh skills <path>` stopped with "unexpected argument". A path given
//! positionally is the corpus root, the same as `--root`; giving both is a usage error.

// Test-only crate: a fixture that cannot be built is a broken test, not a result.
#![cfg(test)]

use std::path::Path;

use goh_testkit::{goh_at, Out};

fn goh(args: &[&str]) -> Out {
    goh_at(Path::new(env!("CARGO_BIN_EXE_goh")), None, args, &[]).expect("goh runs")
}

#[test]
fn skills_takes_the_corpus_root_as_a_positional_path() {
    let c = tempfile::tempdir().expect("tempdir");
    for i in 1..=5 {
        let dir = c.path().join(format!("s{i}"));
        std::fs::create_dir_all(&dir).expect("mkdir");
        std::fs::write(
            dir.join("SKILL.md"),
            format!("---\nname: s{i}\ndescription: d\n---\n\n# s{i}\n\nbody\n"),
        )
        .expect("write");
    }
    let root = c.path().to_string_lossy().into_owned();
    let out = goh(&["skills", root.as_str()]);
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.stdout
            .contains("[skills_corpus] OK — 5 skills, 5 files"),
        "{}",
        out.stdout
    );
    let out = goh(&["skills", root.as_str(), "--root", root.as_str()]);
    assert_eq!(out.status.code(), Some(2), "{}", out.text());
}
