//! The step's specification: every vacuous-pass mode it names RED on the line it says,
//! a sound tree GREEN, and each of those directions watched going red when the step was
//! broken -- a passing run is evidence about the rule, not about a scanner that stopped
//! looking.

use super::*;

/// A repo with one Python file and an EMPTY allowlist. The allowlist is a real file
/// rather than an absent one so these tests exercise the ordinary path -- a repo that
/// has no seeded debt still says so in a file.
fn clean_repo() -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap_or_else(|e| panic!("tempdir: {e}"));
    std::fs::write(dir.path().join("a.py"), "x = 1\n").unwrap_or_else(|e| panic!("{e}"));
    std::fs::write(dir.path().join(allow::ALLOW_FILE), "{\"entries\": []}\n")
        .unwrap_or_else(|e| panic!("{e}"));
    dir
}

/// The scope test reads the allowlist's OWN names, not copies of them: a rename of
/// `ALLOW_FILE` or `ALLOW_DIR` that left this test spelling the old literals would keep it
/// green while the real exclusion silently stopped matching.
#[test]
fn scope_splits_python_from_everything_else_and_from_its_own_allowlist() {
    let shard = format!("{}/01.json", allow::ALLOW_DIR);
    let files: Vec<String> = [
        "tools/a.py",
        "b.rs",
        allow::ALLOW_FILE,
        &shard,
        "nested/dir/c.py",
    ]
    .iter()
    .map(|s| (*s).to_owned())
    .collect();
    assert_eq!(in_scope(&files), ["tools/a.py", "nested/dir/c.py"]);
}

#[test]
fn a_repo_with_no_python_is_not_applicable_and_says_what_it_looked_for() {
    let dir = clean_repo();
    let (code, text) = report(
        &Outcome::default(),
        dir.path(),
        &["a.py".to_owned()],
        false,
        &Policy::default(),
    );
    // With the default `scan` there is no tracked Python, so: not applicable, named,
    // and never a bare pass.
    assert_eq!(code, 0);
    assert!(text.contains("not applicable"), "{text}");
    assert!(text.contains("tracked *.py"), "{text}");
}

#[test]
fn python_that_the_exclusion_removed_is_a_blind_scanner_not_a_clean_one() {
    let dir = clean_repo();
    // `a.py` IS tracked, but GOH_EXCLUDE took it out of scope: `examined` is 0 and
    // `tracked_python` is 1, which must NOT read as "not applicable".
    let outcome = Outcome {
        tracked_python: 1,
        examined: 0,
        ..Outcome::default()
    };
    let policy = Policy {
        exclude: "^tools/".to_owned(),
        ..Policy::default()
    };
    let (code, text) = report(&outcome, dir.path(), &["a.py".to_owned()], false, &policy);
    assert_eq!(code, 1, "{text}");
    assert!(text.contains("read NONE of them"), "{text}");
    assert!(!text.contains("not applicable"), "{text}");
}

#[test]
fn a_floor_below_what_the_tree_has_is_red_and_names_the_key() {
    let dir = clean_repo();
    let outcome = Outcome {
        tracked_python: 1,
        examined: 1,
        seen: 3,
        ..Outcome::default()
    };
    let policy = Policy {
        min_calls: Some(80),
        ..Policy::default()
    };
    let (code, text) = report(&outcome, dir.path(), &["a.py".to_owned()], false, &policy);
    assert_eq!(code, 1, "{text}");
    assert!(text.contains("floor of 80"), "{text}");
    assert!(text.contains("GOH_SUBPROCESS_STDIN_MIN_CALLS"), "{text}");
}

#[test]
fn the_floor_is_full_scope_only_because_a_commit_sees_only_its_own_files() {
    let dir = clean_repo();
    let outcome = Outcome {
        tracked_python: 1,
        examined: 1,
        seen: 1,
        ..Outcome::default()
    };
    let policy = Policy {
        min_calls: Some(80),
        ..Policy::default()
    };
    let listed = ["a.py".to_owned()];
    assert_eq!(report(&outcome, dir.path(), &listed, false, &policy).0, 1);
    assert_eq!(
        report(&outcome, dir.path(), &listed, true, &policy).0,
        0,
        "one staged helper is one call, and red for it would refuse every commit"
    );
}

#[test]
fn an_unreadable_python_file_is_a_finding_rather_than_a_skip() {
    let dir = clean_repo();
    let outcome = Outcome {
        tracked_python: 1,
        examined: 1,
        unreadable: vec![("a.py".to_owned(), "expected `(`".to_owned())],
        ..Outcome::default()
    };
    let (code, text) = report(
        &outcome,
        dir.path(),
        &["a.py".to_owned()],
        false,
        &Policy::default(),
    );
    assert_eq!(code, 1, "{text}");
    assert!(text.contains("could not be read"), "{text}");
}

/// The switch: off and unset skip the step, on runs it, and anything else is an error that
/// names the key -- watched red by making the fallback arm return `Ok(false)`.
#[test]
fn the_switch_is_off_by_default_on_when_asked_and_a_typo_is_an_error() {
    assert_eq!(enabled(None), Ok(false));
    assert_eq!(enabled(Some("")), Ok(false));
    assert_eq!(enabled(Some("off")), Ok(false));
    assert_eq!(enabled(Some(" on ")), Ok(true));
    let typo = enabled(Some("yes")).expect_err("a typo must not read as off");
    assert!(typo.contains("GOH_SUBPROCESS_STDIN"), "{typo}");
}

#[test]
fn an_unset_switch_skips_the_step_without_reading_the_tree() {
    let cfg = crate::gatesrc::Gatesrc::default();
    assert_eq!(step(&cfg, &["a.py".to_owned()], false), None);
}
