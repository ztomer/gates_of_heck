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

// ── scope() and depinfo_escapes() on real temporary cargo workspaces ──────────

fn write(root: &Path, rel: &str, text: &str) {
    let p = root.join(rel);
    std::fs::create_dir_all(p.parent().expect("parent")).expect("mkdir");
    std::fs::write(p, text).expect("write");
}

/// crates/a uses crates/b by path; a's test reads the repo's VERSION at compile time.
fn estate() -> tempfile::TempDir {
    let dir = tempfile::tempdir().expect("tempdir");
    let root = dir.path();
    let pkg = |name: &str, deps: &str| {
        format!("[package]\nname = \"{name}\"\nversion = \"0.1.0\"\nedition = \"2021\"\n\n[dependencies]\n{deps}")
    };
    write(root, "VERSION", "1.0.0\n");
    write(root, "crates/b/Cargo.toml", &pkg("b", ""));
    write(root, "crates/b/src/lib.rs", "pub fn f() {}\n");
    write(
        root,
        "crates/a/Cargo.toml",
        &pkg("a", "b = { path = \"../b\" }\n"),
    );
    write(
        root,
        "crates/a/src/lib.rs",
        "pub fn g() {\n    b::f();\n}\n",
    );
    write(
        root,
        "crates/a/tests/version.rs",
        "const V: &str = include_str!(\"../../../VERSION\");\n#[test]\nfn v() {\n    assert!(!V.is_empty());\n}\n",
    );
    goh_testkit::git_in(root, &["init", "-q"]).expect("git init");
    let lock = std::process::Command::new("cargo")
        .args(["generate-lockfile", "--offline"])
        .current_dir(root.join("crates/a"))
        .status()
        .expect("cargo");
    assert!(lock.success());
    goh_testkit::git_in(root, &["add", "-A"]).expect("git add");
    dir
}

#[test]
fn the_scope_names_the_package_its_path_dep_its_config_and_what_it_includes() {
    let dir = estate();
    let entries = super::scope(&dir.path().join("crates/a")).expect("scope");
    for want in [
        "crates/a",
        "crates/b",
        "VERSION",
        "Cargo.lock",
        ".gatesrc",
        "crates/a/clippy.toml",
    ] {
        assert!(
            entries
                .iter()
                .any(|e| e == want || want.starts_with(&format!("{e}/"))),
            "{want} missing from {entries:?}"
        );
    }
    assert!(!entries.contains(&".".to_owned()), "{entries:?}");
}

#[test]
fn an_unnameable_reach_scopes_the_whole_tree() {
    let dir = estate();
    write(
        dir.path(),
        "crates/a/src/up.rs",
        "pub fn up() -> std::path::PathBuf {\n    std::path::Path::new(env!(\"CARGO_MANIFEST_DIR\")).join(\"..\")\n}\n",
    );
    goh_testkit::git_in(dir.path(), &["add", "-A"]).expect("git add");
    assert_eq!(
        super::scope(&dir.path().join("crates/a")).expect("scope"),
        vec!["."]
    );
}

#[test]
fn a_dep_info_read_outside_the_scope_is_named_and_one_inside_is_not() {
    let dir = estate();
    let cargo_dir = dir.path().join("crates/a");
    let meta = super::metadata(&cargo_dir, true).expect("metadata");
    let build = PathBuf::from(
        meta["build_directory"]
            .as_str()
            .or_else(|| meta["target_directory"].as_str())
            .expect("a build directory"),
    );
    let deps = build.join("debug").join("deps");
    std::fs::create_dir_all(&deps).expect("mkdir deps");
    let root = std::fs::canonicalize(dir.path()).expect("canonical");
    let d = deps.join("a-0123.d");
    std::fs::write(
        &d,
        format!(
            "{}: {} {}\n",
            d.display(),
            root.join("crates/a/src/lib.rs").display(),
            root.join("secret.txt").display()
        ),
    )
    .expect("write .d");
    let entries = vec!["crates/a".to_owned(), "crates/b".to_owned()];
    let bad = crate::rust_depinfo::depinfo_escapes(&cargo_dir, &entries).expect("depinfo");
    std::fs::remove_file(&d).ok();
    assert_eq!(bad, vec!["secret.txt".to_owned()]);
}

#[test]
fn the_command_prints_a_scope_checks_a_file_and_refuses_a_non_workspace() {
    let dir = estate();
    let cargo_dir = dir.path().join("crates/a");
    assert_eq!(super::run_command(&cargo_dir, None), 0);
    let scope_file = dir.path().join("scope.txt");
    std::fs::write(&scope_file, "crates/a\ncrates/b\n").expect("write scope");
    assert_eq!(super::run_command(&cargo_dir, Some(&scope_file)), 0);
    let empty = tempfile::tempdir().expect("tempdir");
    assert_eq!(super::run_command(empty.path(), None), 2);
}
