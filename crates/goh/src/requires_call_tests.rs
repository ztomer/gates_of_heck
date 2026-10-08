//! Unit tests for `requires_call` and `requires_call_py` (split out for the line cap).

use super::*;
use crate::requires_call_py::{calls, Spec};

fn names(src: &str) -> Vec<(usize, String, Option<String>)> {
    calls(src)
        .expect("parses")
        .into_iter()
        .map(|s| (s.line, s.last, s.qualified))
        .collect()
}

#[test]
fn a_call_resolves_through_the_files_own_imports() {
    let got = names(
        "import window_placement as p\nfrom window_placement import verify as judge\n\
         import os.path\np.guarded(f)\njudge(1)\nos.path.join(a)\nh.verify()\nverify()\n",
    );
    assert_eq!(
        got,
        vec![
            (4, "guarded".into(), Some("window_placement.guarded".into())),
            (5, "judge".into(), Some("window_placement.verify".into())),
            (6, "join".into(), Some("os.path.join".into())),
            (7, "verify".into(), None),
            (8, "verify".into(), None),
        ]
    );
}

#[test]
fn a_name_in_a_string_or_a_docstring_is_not_a_call() {
    assert_eq!(
        names("\"\"\"calls relaunch()\"\"\"\nX = 'relaunch()'\n").len(),
        0
    );
}

#[test]
fn a_dotted_spec_needs_the_import_and_a_bare_one_any_receiver() {
    let sites = calls("import hashlib as h\nh.verify()\npresence.check()\n").expect("parses");
    let spec = |s: &str| Spec::parse(s).expect("a spec");
    assert!(!sites[0].matches(&spec("window_placement.verify")));
    assert!(sites[0].matches(&spec("hashlib.verify")) && sites[1].matches(&spec("check")));
}

#[test]
fn globs_keep_star_within_a_directory() {
    assert!(glob_matches("tools/*.py", "tools/a.py"));
    assert!(!glob_matches("tools/*.py", "tools/sub/a.py"));
    assert!(
        glob_matches("tools/**/*.py", "tools/sub/a.py")
            && glob_matches("tools/**/*.py", "tools/a.py")
    );
    assert!(!glob_matches("tools/*.py", "xtools/a.py"));
}

#[test]
fn a_rule_without_why_or_must_call_is_refused() {
    assert!(parse_rules("[[rule]]\nname='x'\nfiles=['*.py']\nmust_call=['f']\n").is_err());
    assert!(parse_rules("[[rule]]\nname='x'\nwhy='y'\nfiles=['*.py']\n").is_err());
    assert_eq!(
        parse_rules("[[rule]]\nname='x'\nwhy='y'\nfiles=['*.py']\nmust_call=['f']\n")
            .expect("valid")
            .len(),
        1
    );
}
