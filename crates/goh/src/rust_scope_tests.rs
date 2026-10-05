//! Unit tests for `rust_scope.rs` and `rust_depinfo.rs` (split for the line cap).

use super::{escapes, prune_nested};
use crate::rust_depinfo::{covered, depinfo_paths};
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

#[test]
fn a_nested_entry_is_covered_by_its_parent() {
    let set: BTreeSet<String> = ["crates/a", "crates/a/Cargo.toml", "crates/b"]
        .into_iter()
        .map(str::to_owned)
        .collect();
    assert_eq!(prune_nested(set), vec!["crates/a", "crates/b"]);
}

#[test]
fn coverage_is_by_path_segment_not_by_prefix() {
    let set = vec!["crates/a".to_owned()];
    assert!(covered(&set, "crates/a/src/lib.rs"));
    assert!(!covered(&set, "crates/ab/src/lib.rs"));
}

#[test]
fn a_runtime_reach_outside_the_crate_is_seen() {
    assert!(escapes(
        r#"let p = concat!(env!("CARGO_MANIFEST_DIR"), "/../x");"#
    ));
    assert!(escapes(r#"std::fs::read("../fixtures/a.json")"#));
    assert!(!escapes("let x = a..b;"));
}

#[test]
fn an_include_is_resolved_from_the_source_file_and_a_runtime_path_from_the_package() {
    let root = Path::new("/r");
    let pkg = Path::new("/r/crates/a");
    let src = Path::new("/r/crates/a/tests/version.rs");
    assert_eq!(
        super::reached(r#"include_str!("../../../VERSION")"#, src, pkg, root),
        Some(vec!["VERSION".to_owned()])
    );
    assert_eq!(
        super::reached(r#"std::fs::read("../shared/x.json")"#, src, pkg, root),
        Some(vec!["crates/shared/x.json".to_owned()])
    );
    assert_eq!(
        super::reached(
            r#"concat!(env!("CARGO_MANIFEST_DIR"), "/../b/f")"#,
            src,
            pkg,
            root
        ),
        Some(vec!["crates/b/f".to_owned()])
    );
}

#[test]
fn an_escaped_quote_inside_a_literal_does_not_hide_the_path() {
    let line = r#""echo \"$*\" >> \"$(dirname \"$0\")/../calls.log\"","#;
    let lits = super::string_literals(line);
    assert_eq!(lits.len(), 1, "{lits:?}");
    assert!(lits[0].contains("/../calls.log"), "{lits:?}");
}

#[test]
fn an_escape_that_cannot_be_named_keys_on_the_whole_tree() {
    let root = Path::new("/r");
    let pkg = Path::new("/r/crates/a");
    let src = Path::new("/r/crates/a/src/lib.rs");
    // A bare `..` joined at run time, and a path leaving the repository.
    assert_eq!(
        super::reached(
            r#"Path::new(env!("CARGO_MANIFEST_DIR")).join("..")"#,
            src,
            pkg,
            root
        ),
        None
    );
    assert_eq!(
        super::reached(r#"read("../../../../etc/x")"#, src, pkg, root),
        None
    );
}

#[test]
fn dep_info_with_escaped_spaces_parses() {
    let text = "/b/deps/a-1.d: /r/src/lib.rs /r/my\\ dir/x.txt\n\n/r/src/lib.rs:\n";
    assert_eq!(
        depinfo_paths(Path::new("/b/deps/a-1.d"), text),
        vec![
            PathBuf::from("/r/src/lib.rs"),
            PathBuf::from("/r/my dir/x.txt")
        ]
    );
}
