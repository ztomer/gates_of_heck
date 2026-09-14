//! The binary end to end over fixture git repos: every subcommand, both
//! scopes, and every `.gatesrc` knob the structural pipeline reads. The
//! Python parity suites (`tests/test_goh_*_parity.py`) prove native agrees
//! with the reference checkers; this suite is what lets `cargo llvm-cov`
//! SEE that exercise -- a process pytest spawns is invisible to it, a
//! process this test spawns is not.

mod common;

use common::{esc, g, goh, repo_with, Repo};

#[test]
fn every_subcommand_answers_help_and_version() {
    for args in [
        vec!["--help"],
        vec!["structural", "--help"],
        vec!["emoji", "--help"],
        vec!["length", "--help"],
        vec!["markers", "--help"],
        vec!["secrets", "--help"],
    ] {
        let out = goh(None, &args, &[]);
        assert!(out.status.success(), "{args:?}: {}", out.text());
    }
    let out = goh(None, &["--version"], &[]);
    assert_eq!(
        out.stdout.trim(),
        format!("goh {}", env!("CARGO_PKG_VERSION"))
    );
}

#[test]
fn a_clean_repo_passes_every_gate_in_both_scopes() {
    let r = repo_with(&[
        ("README.md", "# fixture\n"),
        ("src/lib.rs", "pub fn f() {}\n"),
    ]);
    let out = goh(Some(&r), &["structural", "--full"], &[]);
    assert!(out.status.success(), "{}{}", out.stdout, out.stderr);
    assert!(
        out.stdout.contains("all structural gates passed"),
        "{}",
        out.stdout
    );
    let out = goh(Some(&r), &["structural", "--staged"], &[]);
    assert!(out.status.success(), "{}{}", out.stdout, out.stderr);
    // No flag means full scope; both flags is a usage error.
    assert!(goh(Some(&r), &["structural"], &[]).status.success());
    let both = goh(Some(&r), &["structural", "--staged", "--full"], &[]);
    assert_eq!(both.status.code(), Some(2), "{}", both.text());
}

#[test]
fn emoji_gate_finds_reports_excludes_and_allows() {
    let r = repo_with(&[
        ("docs/a.md", &format!("done {}\n", g(0x2705))), // check-mark button: disallowed
        ("docs/ok.md", &format!("done {} {}\n", g(0x2713), g(0x2192))), // Kare set: allowed
        ("vendor/v.md", &format!("{}\n", g(0x1F600))),   // pictograph, vendored
    ]);
    let out = goh(Some(&r), &["emoji"], &[]);
    assert_eq!(out.status.code(), Some(1));
    assert!(
        out.text().contains("docs/a.md") && out.text().contains("vendor/v.md"),
        "{}",
        out.stderr
    );
    assert!(!out.text().contains("docs/ok.md"), "{}", out.text());
    // The Kare glyph names appear in the verdict so a reader knows the policy.
    assert!(out.text().contains("U+2705"), "{}", out.text());
    let out = goh(Some(&r), &["emoji", "--exclude", "^vendor/"], &[]);
    assert_eq!(out.status.code(), Some(1));
    assert!(!out.text().contains("vendor/v.md"), "{}", out.text());
    let out = goh(
        Some(&r),
        &["emoji", "--exclude", "^vendor/", "--allow", &g(0x2705)],
        &[],
    );
    assert!(out.status.success(), "{}{}", out.stdout, out.stderr);
    // A bad regex is a usage error, not a pass.
    let out = goh(Some(&r), &["emoji", "--exclude", "("], &[]);
    assert_eq!(out.status.code(), Some(2), "{}", out.text());
    // Staged scope polices the index: an unstaged emoji is not seen.
    r.write("docs/dirty.md", &format!("{}\n", g(0x1F389)));
    let out = goh(
        Some(&r),
        &["emoji", "--staged", "--exclude", "^vendor/|^docs/a"],
        &[],
    );
    assert!(out.status.success(), "{}{}", out.stdout, out.stderr);
    r.git(&["add", "docs/dirty.md"]);
    let out = goh(
        Some(&r),
        &["emoji", "--staged", "--exclude", "^vendor/|^docs/a"],
        &[],
    );
    assert_eq!(out.status.code(), Some(1));
    assert!(out.text().contains("docs/dirty.md"), "{}", out.text());
}

#[test]
fn emoji_gate_reports_escaped_codepoints_and_vs16_forms() {
    // The class the repo-local checkers used to miss: an emoji written as
    // an escape, and a typographic sign in its emoji presentation.
    let r = repo_with(&[
        ("a.py", &format!("ICON = \"{}\"\n", esc("U0001F600"))),
        ("b.md", &format!("copyright {}{}\n", g(0x00A9), g(0xFE0F))),
        ("c.md", &format!("copyright {}\n", g(0x00A9))),
    ]);
    let out = goh(Some(&r), &["emoji"], &[]);
    assert_eq!(out.status.code(), Some(1));
    assert!(out.text().contains("a.py"), "{}", out.text());
    assert!(out.text().contains("b.md"), "{}", out.text());
    assert!(!out.text().contains("c.md"), "{}", out.text());
}

#[test]
fn length_gate_caps_excludes_and_counts_the_worktree() {
    let long = "x\n".repeat(30);
    let r = repo_with(&[
        ("src/big.rs", &long),
        ("src/ok.rs", "fn f() {}\n"),
        ("third_party/t.rs", &long),
    ]);
    let out = goh(Some(&r), &["length", "--max", "10"], &[]);
    assert_eq!(out.status.code(), Some(1));
    assert!(
        out.text().contains("src/big.rs") && out.text().contains("30 lines"),
        "{}",
        out.stderr
    );
    let out = goh(
        Some(&r),
        &["length", "--max", "10", "--exclude", "^third_party/"],
        &[],
    );
    assert_eq!(out.status.code(), Some(1));
    assert!(!out.text().contains("third_party"), "{}", out.text());
    assert!(goh(Some(&r), &["length", "--max", "100"], &[])
        .status
        .success());
    // Full scope is the WORKTREE: a brand-new untracked file is counted.
    r.write("src/new.rs", &long);
    let out = goh(
        Some(&r),
        &["length", "--max", "10", "--exclude", "big|third"],
        &[],
    );
    assert_eq!(out.status.code(), Some(1));
    assert!(out.text().contains("src/new.rs"), "{}", out.text());
    // ...but an ignored one is not.
    r.write(".gitignore", "src/new.rs\n");
    let out = goh(
        Some(&r),
        &["length", "--max", "10", "--exclude", "big|third"],
        &[],
    );
    assert!(out.status.success(), "{}{}", out.stdout, out.stderr);
    // Staged scope: the file in the index, not the one on disk.
    r.git(&["add", "-f", "src/new.rs"]);
    r.write("src/new.rs", "short\n");
    let out = goh(Some(&r), &["length", "--max", "10", "--staged"], &[]);
    assert_eq!(out.status.code(), Some(1), "{}", out.stdout);
    assert!(out.text().contains("src/new.rs"), "{}", out.text());
}

#[test]
fn markers_gate_sees_conflict_markers_but_not_headings_or_rulers() {
    let r = repo_with(&[
        (
            "clean.md",
            "# Title\n\n=======\n\ntext >>>>>>> not at column zero\n",
        ),
        ("ok.rs", "// <<<<<<< in a comment is still a marker\n"),
    ]);
    assert!(goh(Some(&r), &["markers"], &[]).status.success());
    r.write(
        "conflict.rs",
        "<<<<<<< HEAD\nfn a() {}\n=======\nfn b() {}\n>>>>>>> branch\n",
    );
    r.git(&["add", "conflict.rs"]);
    let out = goh(Some(&r), &["markers"], &[]);
    assert_eq!(out.status.code(), Some(1));
    assert!(out.text().contains("conflict.rs"), "{}", out.text());
    let out = goh(Some(&r), &["markers", "--staged"], &[]);
    assert_eq!(out.status.code(), Some(1));
}

#[test]
fn secrets_gate_finds_credentials_honours_secret_ok_and_excludes() {
    // Vectors are ASSEMBLED so this source file carries none of them: the
    // secrets gate scans this repo too, and a planted vector in a test is a
    // finding, not a fixture (the Python suite builds its the same way).
    let key = format!("-----BEGIN RSA PRIVATE {}-----\nMIIE\n", "KEY");
    let slack = format!(
        "xox{}-1234567890-1234567890123-aBcDeFgHiJkLmNoPqRsTuVwX",
        "b"
    );
    let aws = format!("AKIA{}", "IOSFODNN7EXAMPLE");
    let r = repo_with(&[
        ("deploy/id_rsa", &key),
        ("src/token.py", &format!("TOKEN = \"{slack}\"\n")),
        (
            "tests/fixture.py",
            &format!("PLANTED = \"{aws}\"  # secret-ok: documentation example key\n"),
        ),
        ("vendor/x.py", &format!("AWS = \"{aws}\"\n")),
    ]);
    let out = goh(Some(&r), &["secrets"], &[]);
    assert_eq!(out.status.code(), Some(1));
    assert!(
        out.text().contains("deploy/id_rsa") && out.text().contains("src/token.py"),
        "{}",
        out.stderr
    );
    assert!(
        !out.text().contains("tests/fixture.py"),
        "secret-ok must exempt: {}",
        out.stderr
    );
    let out = goh(Some(&r), &["secrets", "--exclude", "^vendor/"], &[]);
    assert!(!out.text().contains("vendor/x.py"), "{}", out.text());
    let out = goh(Some(&r), &["secrets", "--staged"], &[]);
    assert_eq!(out.status.code(), Some(1));
}

#[test]
fn structural_reads_every_gatesrc_knob() {
    let long = "l\n".repeat(20);
    let r = repo_with(&[
        ("src/big.rs", &long),
        ("vendor/v.md", &format!("{}\n", g(0x1F600))),
        (
            "docs/prompt.md",
            &format!("type {} to continue\n", g(0x276F)),
        ), // heavy angle quote: allowed via GOH_ALLOW
        ("base.txt", "# ceilings\n20\tsrc/big.rs\n"),
    ]);
    r.write(
        ".gatesrc",
        &format!(
            "# comments and blank lines are fine\n\nGOH_MAX_LINES=10\nexport GOH_EXCLUDE='^vendor/'\nGOH_LINE_EXCLUDE=\"src/big\\.rs\"\nGOH_LINE_BASELINE=base.txt\nGOH_ALLOW='{}'\n",
            g(0x276F)
        ),
    );
    r.git(&["add", "-A"]);
    let out = goh(Some(&r), &["structural", "--full"], &[]);
    assert!(out.status.success(), "{}{}", out.stdout, out.stderr);
    for step in [
        "no disallowed emoji",
        "no conflict markers",
        "file length <= 10",
        "line-cap exemptions carry a ceiling",
        "cap-exempt files within their ceilings",
        "shell lint",
        "no committed secrets",
    ] {
        assert!(
            out.stdout.contains(step),
            "missing step {step:?} in\n{}",
            out.stdout
        );
    }
    // Grow the exempt file past its ceiling: the ratchet bites through the pipeline.
    r.write("src/big.rs", &"l\n".repeat(21));
    r.git(&["add", "-A"]);
    let out = goh(Some(&r), &["structural", "--full"], &[]);
    assert_eq!(out.status.code(), Some(1), "{}{}", out.stdout, out.stderr);
    assert!((out.stdout + &out.stderr).contains("src/big.rs"));
}

#[test]
fn structural_stops_at_the_first_red_gate_and_names_it() {
    let r = repo_with(&[("a.md", &format!("{}\n", g(0x1F600)))]);
    let out = goh(Some(&r), &["structural", "--full"], &[]);
    assert_eq!(out.status.code(), Some(1));
    assert!(
        out.text().contains("no disallowed emoji failed"),
        "{}",
        out.stderr
    );
    assert!(
        !out.stdout.contains("no conflict markers"),
        "later steps must not run: {}",
        out.stdout
    );
}

#[test]
fn a_bad_gatesrc_is_a_usage_error_not_a_pass() {
    let r = repo_with(&[("a.md", "x\n")]);
    r.write(".gatesrc", "GOH_MAX_LINES=ten\n");
    r.git(&["add", "-A"]);
    let out = goh(Some(&r), &["structural", "--full"], &[]);
    assert_eq!(out.status.code(), Some(2), "{}{}", out.stdout, out.stderr);
    assert!(out.text().contains("GOH_MAX_LINES"), "{}", out.text());
}

#[test]
fn no_cap_set_warns_and_skips_the_length_gate() {
    let r = repo_with(&[("src/a.rs", &"x\n".repeat(600))]);
    let out = goh(Some(&r), &["structural", "--full"], &[]);
    assert!(out.status.success(), "{}{}", out.stdout, out.stderr);
    assert!(
        (out.stdout.clone() + &out.stderr).contains("file-length cap not set"),
        "{}{}",
        out.stdout,
        out.stderr
    );
}

#[test]
fn a_line_exclude_without_a_baseline_warns_that_nothing_bounds_it() {
    let r = repo_with(&[("src/a.rs", "x\n")]);
    r.write(
        ".gatesrc",
        "GOH_MAX_LINES=500\nGOH_LINE_EXCLUDE='src/a\\.rs'\n",
    );
    r.git(&["add", "-A"]);
    let out = goh(Some(&r), &["structural", "--full"], &[]);
    assert!(out.status.success(), "{}{}", out.stdout, out.stderr);
    assert!(out.text().contains("bounded by nothing"), "{}", out.text());
}

#[test]
fn the_skills_corpus_gate_runs_when_declared_and_a_rooted_corpus_exists() {
    let r = repo_with(&[("README.md", "x\n")]);
    let corpus = r.path().join("corpus");
    for i in 0..6 {
        let d = corpus.join(format!("skill-{i}"));
        std::fs::create_dir_all(&d).expect("mkdir");
        std::fs::write(
            d.join("SKILL.md"),
            "---\nname: s\ndescription: d\n---\n\n# S\n\nbody\n",
        )
        .expect("write");
    }
    r.write(
        ".gatesrc",
        &format!(
            "GOH_MAX_LINES=500\nGOH_SKILLS_CORPUS=1\nGOH_SKILLS_ROOT={}\n",
            corpus.display()
        ),
    );
    r.git(&["add", "-A"]);
    let out = goh(Some(&r), &["structural", "--full"], &[]);
    assert!(
        out.stdout.contains("skills corpus"),
        "{}{}",
        out.stdout,
        out.stderr
    );
}

#[test]
fn outside_a_git_repo_every_command_says_so_by_name() {
    // The same contract as the Python checkers: nothing to police, and the
    // skip is NAMED -- a bare exit 0 would be indistinguishable from clean.
    let dir = tempfile::tempdir().expect("tempdir");
    let r = Repo::bare(dir);
    for args in [
        vec!["emoji"],
        vec!["length", "--max", "5"],
        vec!["markers"],
        vec!["secrets"],
    ] {
        let out = goh(Some(&r), &args, &[]);
        assert!(out.status.success(), "{args:?}: {}", out.text());
        assert!(
            out.text().contains("not a git repo"),
            "{args:?} did not name the skip: {}",
            out.text()
        );
    }
}

#[test]
fn the_platform_gate_refuses_macos_intel() {
    let r = repo_with(&[("a.md", "x\n")]);
    let out = goh(
        Some(&r),
        &["markers"],
        &[("GOH_BUILD_OS", "Darwin"), ("GOH_BUILD_ARCH", "x86_64")],
    );
    assert_eq!(out.status.code(), Some(1));
    assert!(out.text().contains("Intel"), "{}", out.text());
}
