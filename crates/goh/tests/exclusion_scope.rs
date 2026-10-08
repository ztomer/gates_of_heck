//! What `GOH_EXCLUDE` may and may not take out of scope.
//!
//! Two defects found from `app_updates` on 2026-10-08, when it vendored a third-party crate under
//! `vendor/camoufox-rs/` and excluded it:
//!
//! 1. The commit-time Rust step decided it had work BEFORE applying the exclusion, then ran the
//!    empty-scope alarm AFTER it: a commit whose only staged Rust was excluded was refused as a
//!    blind scanner. An exclusion that removed every candidate is nothing to judge; the alarm is
//!    for a scanner whose scope matched none of the repo's OWN source, at full scope.
//! 2. The exclusion also took the tree out of the SECRETS scan (6 staged files scanned of 63).
//!    A path exemption is for house style; a committed credential is a leak wherever it sits.
//!
//! Credentials are assembled at runtime, so this file carries none.

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

const VENDORED_GATESRC: &str = "GOH_MAX_LINES=500\nGOH_EXCLUDE='^vendor/'\n";

/// A repo whose only Rust is a vendored crate under `vendor/x/`, all staged.
fn vendored_only(extra: &[(&str, &str)]) -> Repo {
    let mut files = vec![
        (".gatesrc", VENDORED_GATESRC),
        ("vendor/x/Cargo.toml", "[package]\nname = \"x\"\n"),
        ("vendor/x/src/lib.rs", "pub fn f() {}\n"),
    ];
    files.extend_from_slice(extra);
    repo_with(&files).expect("fixture")
}

#[test]
fn a_commit_of_only_excluded_rust_is_nothing_to_judge() {
    let r = vendored_only(&[]);
    let out = goh(&r, &["structural", "--staged"]);
    assert!(out.status.success(), "{}", out.text());
    assert!(
        !out.text().contains("matched NO compiled source"),
        "{}",
        out.text()
    );
    // The same verdict from each scanner run alone, at both scopes: the repo's Rust is all
    // third-party, so there is no source of its own for an empty scan to have missed.
    for args in [
        &["empty-assert", "--staged", "--exclude", "^vendor/"][..],
        &["empty-assert", "--exclude", "^vendor/"],
        &["no-allow", "--staged", "--exclude", "^vendor/"],
        &["no-allow", "--exclude", "^vendor/"],
    ] {
        let out = goh(&r, args);
        assert!(out.status.success(), "{args:?}: {}", out.text());
    }
}

#[test]
fn a_scanner_blind_to_the_repos_own_rust_is_still_red() {
    // A Cargo.toml of the repo's own, and source in a layout the scope does not match: that is
    // the blind scanner the alarm exists for, and it must still refuse at full scope.
    let r = repo_with(&[
        ("Cargo.toml", "[package]\nname = \"own\"\n"),
        ("lib/a.rs", "pub fn a() {}\n"),
        ("vendor/x/Cargo.toml", "[package]\nname = \"x\"\n"),
        ("vendor/x/src/lib.rs", "pub fn f() {}\n"),
    ])
    .expect("fixture");
    for check in ["empty-assert", "no-allow"] {
        let out = goh(&r, &[check, "--exclude", "^vendor/"]);
        assert_eq!(out.status.code(), Some(1), "{check}: {}", out.text());
        assert!(
            out.stderr.contains("matched NO compiled source"),
            "{check}: {}",
            out.text()
        );
    }
}

#[test]
fn an_excluded_path_is_still_scanned_for_secrets() {
    let token = format!("AKIA{}", "IOSFODNN7EXAMPLE");
    let leak = format!("KEY = \"{token}\"\n");
    let r = vendored_only(&[("vendor/x/cfg.py", &leak)]);
    for (scope, label) in [
        ("--staged", "no committed secrets (staged)"),
        ("--full", "no committed secrets"),
    ] {
        let out = goh(&r, &["structural", scope]);
        assert!(!out.status.success(), "{scope}: {}", out.text());
        assert!(
            out.stderr
                .contains(&format!("✗ structural: {label} failed")),
            "{scope}: {}",
            out.text()
        );
        assert!(out.text().contains("vendor/x/cfg.py"), "{}", out.text());
    }
    // Even an exclusion of EVERYTHING leaves the secrets scan whole.
    let r = repo_with(&[
        ("cfg.py", leak.as_str()),
        (".gatesrc", "GOH_MAX_LINES=500\nGOH_EXCLUDE='.'\n"),
    ])
    .expect("fixture");
    let out = goh(&r, &["structural", "--staged"]);
    assert!(out.text().contains("cfg.py"), "{}", out.text());
    assert!(!out.status.success(), "{}", out.text());
}

#[test]
fn the_secrets_scanner_takes_no_path_exemption_at_all() {
    // Not ignored, refused: a script passing `--exclude` to the secrets scan believes it narrowed
    // it, and a usage error says it did not, where a silently ignored flag would not.
    let r = vendored_only(&[]);
    let out = goh(&r, &["secrets", "--exclude", "^vendor/"]);
    assert_eq!(out.status.code(), Some(2), "{}", out.text());
}

#[test]
fn a_direct_check_judges_a_repo_as_its_own_gate_does() {
    // 3. A direct `goh <check>` ignored the repo's `.gatesrc`: in app_updates, `goh.sh md-links`
    //    reported the vendored crate's dead anchor that its structural gate exempts, and every
    //    sweep that calls checks directly (the R3 estate sweep, gate calibration, the empty-scope
    //    sweep) judged the repo differently from the repo's own gate (2026-10-08).
    let dead = "See [nothing](#no-such-anchor).\n";
    let vendored_allow = format!("#[{}(dead_code)]\nfn f() {{}}\n", "allow");
    let r = vendored_only(&[
        ("README.md", "# readme\n"),
        ("vendor/x/PROTOCOL.md", dead),
        ("vendor/x/src/more.rs", &vendored_allow),
        ("Cargo.toml", "[package]\nname = \"own\"\n"),
        ("src/lib.rs", "pub fn own() {}\n"),
    ]);
    for args in [
        &["md-links"][..],
        &["no-allow"],
        &["no-allow", "--staged"],
        &["empty-assert"],
    ] {
        let out = goh(&r, args);
        assert!(out.status.success(), "{args:?}: {}", out.text());
        assert!(
            !out.text().contains("vendor/x/"),
            "{args:?}: {}",
            out.text()
        );
    }
    // An explicit `--exclude` still wins, and an empty one opts out of the declared exemption.
    let out = goh(&r, &["md-links", "--exclude", ""]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(
        out.text().contains("vendor/x/PROTOCOL.md"),
        "{}",
        out.text()
    );
    let out = goh(&r, &["no-allow", "--exclude", ""]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    // The AMBIENT environment is not the repo's declaration: an inherited GOH_EXCLUDE changes
    // nothing (the hostile-environment rule of tests/test_goh_structural.py).
    let plain =
        repo_with(&[("README.md", "# r\n"), ("vendor/x/PROTOCOL.md", dead)]).expect("fixture");
    let out = goh_at(
        std::path::Path::new(env!("CARGO_BIN_EXE_goh")),
        Some(&plain),
        &["md-links"],
        &[("GOH_EXCLUDE", "^vendor/")],
    )
    .expect("goh runs");
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
}
