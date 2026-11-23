//! A name the tree still names is refused and nothing is written; a clean name is added.

use super::add;
use crate::privnames::{read_list, LIST};

fn names(v: &[&str]) -> Vec<String> {
    v.iter().map(|s| (*s).to_string()).collect()
}

#[test]
fn a_name_the_work_tree_still_names_is_refused_and_nothing_is_written() {
    let r = goh_testkit::repo_with(&[("a.rs", "// found in Acme\n")]).expect("fixture");
    let d = tempfile::tempdir().expect("tmp");
    let list = d.path().join(LIST);
    std::fs::write(&list, "Old\n").expect("write");
    let (code, out) = add(r.path(), &list, &names(&["Other", "Acme"]));
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("work tree: a.rs:1"), "{out}");
    assert!(out.contains("nothing added"), "{out}");
    assert_eq!(read_list(&list), Some(names(&["Old"])), "all or nothing");
    let (code, out) = add(r.path(), &list, &names(&["Other"]));
    assert_eq!(code, 0, "{out}");
    assert_eq!(read_list(&list), Some(names(&["Old", "Other"])));
    let (code, out) = add(r.path(), &list, &names(&["Other"]));
    assert!(code == 0 && out.contains("already listed"), "{out}");
}

#[test]
fn a_name_only_the_land_base_still_names_is_refused() {
    // The 2026-11-15 order: the sweep is done on a lane, main still names it.
    let r = goh_testkit::repo_with(&[("docs/a.md", "see Acme/ws\n")]).expect("fixture");
    r.git(&["commit", "-qm", "main"]).expect("commit");
    r.git(&["update-ref", "refs/remotes/origin/HEAD", "HEAD"])
        .expect("base");
    r.write("docs/a.md", "see a private consumer\n")
        .expect("sweep");
    r.git(&["commit", "-qam", "sweep"]).expect("commit");
    let d = tempfile::tempdir().expect("tmp");
    let list = d.path().join("store").join(LIST);
    let (code, out) = add(r.path(), &list, &names(&["Acme"]));
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("origin/HEAD: docs/a.md:1"), "{out}");
    assert!(!out.contains("work tree:"), "{out}");
    assert_eq!(read_list(&list), None);
    r.git(&["update-ref", "refs/remotes/origin/HEAD", "HEAD"])
        .expect("landed");
    let (code, out) = add(r.path(), &list, &names(&["Acme"]));
    assert_eq!(code, 0, "{out}");
    assert_eq!(read_list(&list), Some(names(&["Acme"])));
}

#[test]
fn a_path_naming_it_is_refused_too() {
    let r = goh_testkit::repo_with(&[("tests/Acme/x.txt", "x\n")]).expect("fixture");
    let d = tempfile::tempdir().expect("tmp");
    let (code, out) = add(r.path(), &d.path().join(LIST), &names(&["Acme"]));
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("tests/Acme/x.txt: its path"), "{out}");
}
