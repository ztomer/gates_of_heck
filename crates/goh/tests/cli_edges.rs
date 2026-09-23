//! Edge branches of the scanners and the `.gatesrc` reader that only a
//! particular input reaches: Swift type shapes the screen scanner must
//! tell from live uses, glob syntax, a wrapped suppression on a bare
//! continuation line, the dotenv-style value forms, and usage errors on
//! the older single-checker subcommands. Driven as a process so
//! `cargo llvm-cov` sees them; suppression tokens are assembled at runtime.

// Test-only crate: helpers `expect` fixture writes, and a fixture that
// cannot be built is a broken test, not a result.
#![cfg(test)]

use goh_testkit::{goh_at, repo_with, Out, Repo};

fn goh(repo: &Repo, args: &[&str]) -> Out {
    goh_at(
        std::path::Path::new(env!("CARGO_BIN_EXE_goh")),
        Some(repo),
        args,
        &[],
    )
    .expect("goh runs")
}

#[test]
fn screen_tells_swift_type_shapes_from_live_uses() {
    let swift = [
        "let d: Dictionary<Array<Int>, NSScreen> = [:]", // 1: generic argument, exempt
        "let s = pick(NSScreen)",                        // 2: call argument, live
        "let t = NSScreen",                              // 3: plain expression, live
        "/* x /* NSScreen */",                           // 4: inside a comment
        "let u = \"a\\\"NSScreen\"",                     // 5: inside a literal
    ]
    .join("\n");
    let r = repo_with(&[
        ("tests/shapes.swift", &format!("{swift}\n")),
        ("tests/run.py", "CMD = \"osascript -e beep\"\n"),
        ("NOTES", "NSScreen.main\n"),
    ])
    .expect("fixture");
    let out = goh(
        &r,
        &["screen", "tests/shapes.swift", "tests/run.py", "NOTES"],
    );
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(
        out.stderr.contains("2 presentation call(s)"),
        "{}",
        out.stderr
    );
    assert!(
        out.stderr.contains("tests/shapes.swift:2:"),
        "{}",
        out.stderr
    );
    assert!(
        out.stderr.contains("tests/shapes.swift:3:"),
        "{}",
        out.stderr
    );
    // Prose naming a live command executes nothing; an extensionless file
    // is no test target.
    assert!(!out.stderr.contains("run.py"), "{}", out.stderr);
    assert!(!out.stderr.contains("NOTES"), "{}", out.stderr);
}

#[test]
fn screen_scope_globs_translate_like_fnmatch() {
    let r = repo_with(&[
        ("tests/a.swift", "NSScreen.main\n"),
        ("tests/]b.swift", "NSScreen.main\n"),
    ])
    .expect("fixture");
    // `?` is one character.
    let out = goh(&r, &["screen", "--scope", "tests/?.swift"]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out.stderr.contains("tests/a.swift"), "{}", out.text());
    assert!(!out.stderr.contains("]b.swift"), "{}", out.text());
    // A leading `]` in a class is a literal member.
    let out = goh(&r, &["screen", "--scope", "tests/[]]b.swift"]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out.stderr.contains("tests/]b.swift"), "{}", out.text());
    assert!(!out.stderr.contains("tests/a.swift"), "{}", out.text());
}

#[test]
fn no_allow_sees_a_wrapped_suppression_on_a_bare_continuation_line() {
    let wrapped = format!(
        "#[{}(\n    test,\n{}(dead_code)\n)]\nfn f() {{}}\n",
        "cfg_attr", "allow"
    );
    let r = repo_with(&[("Cargo.toml", "[package]\n"), ("src/w.rs", &wrapped)]).expect("fixture");
    let out = goh(&r, &["no-allow"]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out.stdout.contains("src/w.rs:3:"), "{}", out.stdout);
}

#[test]
fn gatesrc_values_read_the_way_bash_reads_them() {
    // A tab before the comment ends the value; a line with no key is
    // skipped; a lone `$` is a literal dollar, not an expansion.
    let r = repo_with(&[
        (
            ".gatesrc",
            "GOH_MAX_LINES=3\t# the cap\n=orphan\nGOH_NOTE='cost: $ 5'\n",
        ),
        ("long.py", "1\n2\n3\n4\n"),
    ])
    .expect("fixture");
    let out = goh(&r, &["structural", "--full"]);
    assert!(!out.status.success(), "{}", out.text());
    assert!(out.stdout.contains("file length <= 3"), "{}", out.text());
    assert!(out.stderr.contains("long.py"), "{}", out.text());
}

#[test]
fn older_subcommands_refuse_a_bad_exclude_and_secrets_says_clean() {
    let r = repo_with(&[("a.md", "fine\n")]).expect("fixture");
    for args in [
        vec!["length", "--max", "5", "--exclude", "("],
        vec!["secrets", "--exclude", "("],
    ] {
        let out = goh(&r, &args);
        assert_eq!(out.status.code(), Some(2), "{args:?}: {}", out.text());
        assert!(out.stderr.contains("regex"), "{args:?}: {}", out.text());
    }
    let out = goh(&r, &["secrets"]);
    assert!(out.status.success(), "{}", out.text());
    assert_eq!(out.stdout, "✓ [no_secrets] OK — 1 tracked files clean\n");
    let out = goh(&r, &["secrets", "--staged"]);
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.stdout.contains("1 staged files clean"),
        "{}",
        out.text()
    );
}

#[test]
fn the_ratchet_measures_a_vanished_ceiling_as_zero() {
    let r = repo_with(&[("a.md", "1\n"), ("b.txt", "5\tghost.rs\n3\ta.md\n")]).expect("fixture");
    let out = goh(&r, &["ceiling", "--baseline", "b.txt"]);
    assert!(out.status.success(), "{}", out.text());
    assert!(out.stdout.contains("ghost.rs 5-> 0"), "{}", out.stdout);
}

#[test]
fn structural_outside_a_git_repo_is_an_error_not_a_pass() {
    // Unlike the single checkers (which skip by name), the whole pipeline
    // has nothing to enumerate and must not report "all gates passed".
    let r = Repo::bare(tempfile::tempdir().expect("tempdir"));
    let out = goh(&r, &["structural", "--full"]);
    assert_eq!(out.status.code(), Some(2), "{}", out.text());
    assert!(!out.stdout.contains("all structural gates passed"));
}
