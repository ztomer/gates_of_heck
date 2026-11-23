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

#[test]
fn an_empty_or_commented_name_a_bad_list_dir_and_no_repository_are_refused() {
    let r = goh_testkit::repo_with(&[("a.rs", "// AcmeTender only\n")]).expect("fixture");
    let d = tempfile::tempdir().expect("tmp");
    let list = d.path().join(LIST);
    let (code, out) = add(r.path(), &list, &names(&["", "x # y"]));
    assert_eq!(code, 1, "{out}");
    assert_eq!(out.matches("is no list entry").count(), 2, "{out}");
    // a word-edge miss on a grep hit is no finding; a list with no last newline gets one
    std::fs::write(&list, "Old").expect("write");
    let (code, out) = add(r.path(), &list, &names(&["Acme"]));
    assert_eq!(code, 0, "{out}");
    assert_eq!(read_list(&list), Some(names(&["Old", "Acme"])));
    // the list's directory cannot be made (a file stands there), or the list is a directory
    std::fs::write(d.path().join("file"), "x").expect("write");
    let (code, _) = add(
        r.path(),
        &d.path().join("file").join(LIST),
        &names(&["New"]),
    );
    assert_eq!(code, 2);
    std::fs::create_dir(d.path().join("dir.txt")).expect("mkdir");
    let (code, _) = add(r.path(), &d.path().join("dir.txt"), &names(&["New"]));
    assert_eq!(code, 2);
    // outside a repository the tree cannot be read: a refusal, never a clean add
    let bare = tempfile::tempdir().expect("tmp");
    let (code, out) = add(bare.path(), &list, &names(&["New"]));
    assert_eq!(code, 2, "{out}");
    assert!(super::naming(r.path(), Some("no-such-rev"), "x").is_err());
}
