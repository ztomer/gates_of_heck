//! The finder over the estate's shapes, the look-alikes that must stay clean, and the subject.
//! The seed's directions are `seedcount`'s, proven once through `fixedsleep/tests.rs`.

use super::*;

fn found(source: &str) -> Vec<usize> {
    scan::findings(source)
        .unwrap_or_else(|e| panic!("{e}"))
        .into_iter()
        .map(|f| f.line)
        .collect()
}

#[test]
fn every_antigravity_marker_is_found() {
    let src = "print(\"[ ==> ] start\")\nprint('[ Wrn ] slow')\necho \"[ Err ] no\"\nprint(f\"[ Ok  ] {x}\")\n";
    assert_eq!(found(src), [1, 2, 3, 4]);
}

#[test]
fn the_pass_fail_style_is_found() {
    assert_eq!(
        found("echo \"[ PASS ] build\"\necho \"[ FAIL ] test\"\n"),
        [1, 2]
    );
}

#[test]
fn every_spelling_of_a_hand_rolled_escape_is_found() {
    let src = "G='\\033[32m'\nr = \"\\x1b[0m\"\nprintf '\\e[1m'\nlet s = \"\\u{1b}[31m\";\nlet t = \"\\u001B[0m\"\n";
    assert_eq!(found(src), [1, 2, 3, 4, 5]);
}

#[test]
fn a_type_or_a_list_in_brackets_is_not_a_marker() {
    let src = "func f() -> [Error] { [] }\nxs = [OK]\nys = [ ok ]\nlet e: [ Error ] = []\n";
    assert_eq!(found(src), [] as [usize; 0]);
}

#[test]
fn a_comment_quoting_the_style_is_not_a_use() {
    let src = "# never print \"[ PASS ]\" by hand\n// nor \"\\033[\" colours\n * \"[ Ok  ]\"\n";
    assert_eq!(found(src), [] as [usize; 0]);
}

#[test]
fn a_shebang_line_is_not_a_comment() {
    assert_eq!(found("#!/bin/sh -c 'printf \"\\033[0m\"'\n"), [1]);
}

#[test]
fn a_marker_and_an_escape_on_one_line_are_one_site() {
    assert_eq!(found("echo -e \"\\033[32m[ OK ]\\033[0m\"\n"), [1]);
}

#[test]
fn the_subject_is_source_never_docs_or_data() {
    for rel in ["a.py", "b.sh", "c.rs", "d.swift", "e.ZSH"] {
        assert!(is_source(rel), "{rel}");
    }
    for rel in ["README.md", "x.json", "y.txt", "Makefile", "z.toml"] {
        assert!(!is_source(rel), "{rel}");
    }
}

#[test]
fn a_site_names_its_line_and_the_house_lib() {
    let mut scanned = Scanned {
        listed: 1,
        read: 1,
        ..Scanned::default()
    };
    let sites = scan::findings("print(\"[ Ok  ] done\")\n").unwrap_or_else(|e| panic!("{e}"));
    scanned.found.insert("t.py".to_owned(), sites);
    let (code, text) = judge(&scanned, &BTreeMap::new());
    assert_eq!(code, 1);
    assert!(
        text.contains("t.py:1: a hand-rolled status marker `[ Ok  ]`"),
        "{text}"
    );
    assert!(text.contains("tui/lib.sh"), "{text}");
}
