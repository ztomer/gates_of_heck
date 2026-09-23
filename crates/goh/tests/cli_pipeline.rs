//! The structural pipeline's red paths, one step at a time: each step that
//! fails must stop the run, name itself and exit non-zero, and each
//! `.gatesrc` value it cannot use must be a usage error, never a pass.
//! `cli.rs` pins the green run and the first (emoji) red; this file covers
//! the rest, plus the emoji scanner's escape forms and report cap. Forbidden
//! glyphs are built from their numbers (`g`), and credentials are assembled
//! at runtime, so this file carries neither.

// Test-only crate: helpers `expect` fixture writes, and a fixture that
// cannot be built is a broken test, not a result.
#![cfg(test)]

use goh_testkit::{esc, g, goh_at, repo_with, Out, Repo};

fn goh(repo: &Repo, args: &[&str], envs: &[(&str, &str)]) -> Out {
    goh_at(
        std::path::Path::new(env!("CARGO_BIN_EXE_goh")),
        Some(repo),
        args,
        envs,
    )
    .expect("goh runs")
}

/// A repo whose only content is `.gatesrc` plus `files`, all staged.
fn configured(gatesrc: &str, files: &[(&str, &str)]) -> Repo {
    let mut all = vec![(".gatesrc", gatesrc)];
    all.extend_from_slice(files);
    repo_with(&all).expect("fixture")
}

fn assert_red(out: &Out, step: &str) {
    assert!(!out.status.success(), "{step}: {}", out.text());
    assert!(
        out.stderr.contains(&format!("✗ structural: {step} failed")),
        "{step} was not named: {}",
        out.text()
    );
}

#[test]
fn each_native_step_stops_the_pipeline_by_name() {
    let r = configured(
        "GOH_MAX_LINES=500\n",
        &[("conflict.txt", "<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n")],
    );
    assert_red(
        &goh(&r, &["structural", "--full"], &[]),
        "no conflict markers",
    );

    let r = configured("GOH_MAX_LINES=3\n", &[("long.py", "1\n2\n3\n4\n5\n")]);
    let out = goh(&r, &["structural", "--full"], &[]);
    assert_red(&out, "file length <= 3");
    assert!(out.stderr.contains("long.py"), "{}", out.text());

    let token = format!("AKIA{}", "IOSFODNN7EXAMPLE");
    let r = configured(
        "GOH_MAX_LINES=500\n",
        &[("cfg.py", &format!("KEY = \"{token}\"\n"))],
    );
    let out = goh(&r, &["structural", "--staged"], &[]);
    assert_red(&out, "no committed secrets (staged)");
    assert!(out.text().contains("cfg.py"), "{}", out.text());
}

#[test]
fn a_failing_delegated_step_shows_its_tail_and_timing_is_optional() {
    let r = configured("GOH_MAX_LINES=500\n", &[("bad.sh", "if then fi\n")]);
    let out = goh(&r, &["structural", "--full"], &[("GOH_TAIL", "5")]);
    assert_red(&out, "shell lint");
    assert!(out.text().contains("bad.sh"), "{}", out.text());
    let r = configured("GOH_MAX_LINES=500\n", &[("ok.md", "x\n")]);
    let out = goh(&r, &["structural", "--staged"], &[("GOH_TIME", "1")]);
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.stdout.contains("✓ no disallowed emoji (staged) (0s)"),
        "{}",
        out.stdout
    );
}

#[test]
fn unusable_gatesrc_values_are_usage_errors() {
    let r = configured("GOH_MAX_LINES=500\nGOH_EXCLUDE='('\n", &[("a.md", "x\n")]);
    let out = goh(&r, &["structural", "--full"], &[]);
    assert_eq!(out.status.code(), Some(2), "{}", out.text());
    assert!(out.stderr.contains("[no_emoji]"), "{}", out.text());

    let corpus = tempfile::tempdir().expect("tempdir");
    let r = configured(
        &format!(
            "GOH_MAX_LINES=500\nGOH_SKILLS_CORPUS=1\nGOH_SKILLS_ROOT={}\nGOH_SKILLS_MAX_WORDS=lots\n",
            corpus.path().display()
        ),
        &[("a.md", "x\n")],
    );
    let out = goh(&r, &["structural", "--full"], &[]);
    assert_eq!(out.status.code(), Some(2), "{}", out.text());
    assert!(
        out.stderr
            .contains("bad GOH_SKILLS_MAX_WORDS value: \"lots\""),
        "{}",
        out.text()
    );
}

#[test]
fn an_absent_corpus_is_skipped_by_name_and_a_thin_one_is_red() {
    let r = configured(
        "GOH_MAX_LINES=500\nGOH_SKILLS_CORPUS=1\nGOH_SKILLS_ROOT=/nonexistent/goh-corpus\n",
        &[("a.md", "x\n")],
    );
    let out = goh(&r, &["structural", "--staged"], &[]);
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.stderr
            .contains("skills corpus not present at /nonexistent/goh-corpus"),
        "{}",
        out.text()
    );
    // A corpus in a SUBDIRECTORY of the repo is read from the index at
    // --staged: two staged skills are under the floor, whatever the tree says.
    let skill = |n: &str| format!("---\nname: {n}\ndescription: d\n---\n\nbody\n");
    let (one, two) = (skill("one"), skill("two"));
    let r = configured(
        "GOH_MAX_LINES=500\nGOH_SKILLS_CORPUS=1\nGOH_SKILLS_ROOT=corpus\nGOH_SKILLS_MAX_WORDS=100\n",
        &[("corpus/one/SKILL.md", &one), ("corpus/two/SKILL.md", &two)],
    );
    let out = goh(&r, &["structural", "--staged"], &[]);
    assert_red(&out, "skills corpus");
    assert!(out.text().contains("only 2 skill(s)"), "{}", out.text());
}

#[test]
fn home_paths_in_the_pipeline_say_when_there_is_nothing_to_judge() {
    // Everything excluded at --full: a refusal, never compliance.
    let r = configured(
        "GOH_MAX_LINES=500\nGOH_NO_HOME_PATHS=1\nGOH_EXCLUDE='.'\n",
        &[("a.md", "x\n")],
    );
    let out = goh(&r, &["structural", "--full"], &[]);
    assert_red(&out, "no hard-coded home paths");
    assert!(
        out.stderr
            .contains("refusing to report clean over zero files"),
        "{}",
        out.text()
    );
    // Nothing staged at --staged: an honest pass that says so.
    let r = configured(
        "GOH_MAX_LINES=500\nGOH_NO_HOME_PATHS=1\n",
        &[("a.md", "x\n")],
    );
    r.git(&["commit", "-qm", "x"]).expect("git");
    let out = goh(&r, &["structural", "--staged"], &[]);
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.stdout.contains("nothing staged — 0 files to check"),
        "{}",
        out.text()
    );
    // A staged violation at --staged is red and named as staged.
    r.write("RUN.md", "cd ~/Projects/x\n").expect("write");
    r.git(&["add", "RUN.md"]).expect("git");
    let out = goh(&r, &["structural", "--staged"], &[]);
    assert_red(&out, "no hard-coded home paths (staged)");
}

#[test]
fn emoji_escape_forms_and_the_report_cap() {
    let brace = format!("x = \"{}\"\n", esc("u{1F600}"));
    let decoys = [
        esc("u{}"),        // no digits
        esc("u{1F600"),    // unterminated brace
        esc("u{1234567}"), // seven digits
        esc("uZZZZ"),      // not hex
        esc("u0041"),      // permitted: a letter
        esc("uD800"),      // a surrogate is no character
        esc("n"),          // not a unicode escape at all
    ]
    .join(" ");
    let many = format!("{}\n", g(0x1F389)).repeat(203);
    let r = repo_with(&[
        ("brace.rs", &brace),
        ("decoys.rs", &format!("{decoys}\n")),
        ("many.md", &many),
    ])
    .expect("fixture");
    std::fs::write(r.path().join("blob.bin"), b"\xff\xfe\x00").expect("write");
    r.git(&["add", "blob.bin"]).expect("git");
    let out = goh(&r, &["emoji", "--allow", &g(0x2B50)], &[]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(
        out.stdout.contains("brace.rs:1:6: U+1F600")
            && out.stdout.contains("(written as an escape)"),
        "{}",
        out.stdout
    );
    assert!(!out.stdout.contains("decoys.rs"), "{}", out.stdout);
    assert!(
        out.stdout.contains("… and 4 more"),
        "204 hits, 200 shown: {}",
        out.stdout
    );
    assert!(
        out.stdout.contains(&format!(" + {}", g(0x2B50))),
        "--allow is named: {}",
        out.stdout
    );
}
