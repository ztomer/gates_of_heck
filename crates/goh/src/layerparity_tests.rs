//! The layer rule over the estate's gate shapes, the class pin against the commit gate, and the
//! verdict over a real repository (red, seeded, stale, within the grace).

use super::{counts, gaps, judge_at, key, push_text, swiftlint_excluded, LAYERS};
use std::collections::BTreeSet;

fn all(n: usize) -> [usize; LAYERS.len()] {
    [n; LAYERS.len()]
}

fn missing(text: &str) -> Vec<String> {
    gaps(text, &all(3)).into_keys().collect()
}

#[test]
fn every_commit_twin_has_a_row() {
    // THE CLASS: a language the commit gate lints is a language this check judges. A staged twin
    // added to commit_gate.sh for a new extension with no row in LAYERS is red here.
    let gate = include_str!("../../../gates/commit_gate.sh");
    let twins: Vec<&str> = gate
        .lines()
        .filter_map(|l| l.split("twin_due ").nth(1))
        .filter_map(|rest| rest.split("'*").nth(1))
        .filter_map(|rest| rest.split('\'').next())
        .collect();
    assert!(twins.len() >= 2, "the reader found the twins: {twins:?}");
    for ext in twins {
        assert!(
            LAYERS.iter().any(|l| l.ext == ext),
            "commit_gate.sh lints staged *{ext} and no LAYERS row judges it at push"
        );
    }
    let rust = include_str!("steps_rust.rs");
    assert!(rust.contains("rust source policies (staged)") && rust.contains(r#"b".rs""#));
    assert!(LAYERS.iter().any(|l| l.ext == ".rs"));
}

#[test]
fn the_necrohand_gate_before_its_fix_is_a_swift_gap() {
    // necrohand tools/gate.sh before NH.SWIFTLAYER (2026-11-12): py_staged at both scopes, no Swift.
    let text = "\"$GOH/gates/structural.sh\" \"$@\"\n\"$GOH/gates/py_staged.sh\" .\n\
                case \"$1\" in\n  --full)\n    tools/local_ci.sh\n    ;;\nesac\n";
    assert_eq!(
        missing(text).len(),
        3,
        "py_staged without --all is the commit half only"
    );
    let fixed = format!("{text}\"$GOH/gates/swift_gate.sh\" .\n");
    assert!(!missing(&fixed).contains(&key(&LAYERS[2])));
}

#[test]
fn each_push_layer_is_found_and_its_look_alikes_are_not() {
    let shapes = [
        ("\"$GOH/gates/rust_gate.sh\" .", "rust"),
        ("GOH_CI_STEPS='x:\"$GOH_DIR/gates/rust_gate.sh\":y'", "rust"),
        ("exec \"$GOH/gates/py_gate.sh\" .", "python"),
        ("\"$GOH/gates/py_staged.sh\" --all .", "python"),
        ("a:\"$GOH_DIR/gates/py_staged.sh\" --all .:b", "python"),
        ("bash $GOH/gates/swift_gate.sh ./ui", "swift"),
    ];
    for (text, lang) in shapes {
        let gone = missing(text);
        assert!(
            !gone.contains(&format!("tools/gate.sh:{lang}")),
            "{text}: {gone:?}"
        );
        assert_eq!(gone.len(), 2, "{text} runs only {lang}: {gone:?}");
    }
    for look in [
        "\"$GOH/gates/py_staged.sh\" .", // the commit half, not the push half
        "a:\"$GOH/gates/py_staged.sh\" .:b --all", // --all belongs to another step
        "my_rust_gate.sh",
        "swift_gate.sh.bak",
        "make lint-swift",
    ] {
        assert_eq!(missing(look).len(), 3, "{look} runs no goh push layer");
    }
}

#[test]
fn a_language_the_tree_does_not_hold_needs_no_layer() {
    assert_eq!(
        gaps("", &[0, 0, 0]).len(),
        0,
        "a tree with none of the languages"
    );
    assert_eq!(
        gaps("", &[0, 5, 0]).into_keys().collect::<Vec<_>>(),
        [key(&LAYERS[1])]
    );
    let line = &gaps("", &[0, 5, 0])[&key(&LAYERS[1])][0];
    assert!(
        line.contains("5 tracked .py files") && line.contains("py_staged.sh"),
        "{line}"
    );
}

#[test]
fn the_root_swiftlint_excludes_are_read() {
    let yml =
        "# fixtures\nexcluded:\n  - tests/fixtures\n  - \"build/\"\n  - ./Vendor # third party\n\
               opt_in_rules:\n  - empty_count\n";
    assert_eq!(
        swiftlint_excluded(yml),
        ["tests/fixtures", "build", "Vendor"]
    );
}

fn repo(files: &[(&str, &str)]) -> goh_testkit::Repo {
    let r = goh_testkit::repo_with(files).expect("fixture");
    r.git(&["commit", "-qm", "root"]).expect("commit");
    r
}

fn seed(r: &goh_testkit::Repo, path: &str) {
    let root = crate::gitutil::root_commit(r.path()).expect("root");
    let body = format!(
        "{{\"entries\": [{{\"root\": \"{root}\", \"repo\": \"x\", \"path\": \"{path}\", \
         \"count\": 1, \"reason\": \"unreviewed\"}}]}}\n"
    );
    r.write("layer_parity_seed.json", &body).expect("seed");
    r.git(&["add", "layer_parity_seed.json"]).expect("add");
    r.git(&["commit", "-qm", "seed"]).expect("commit");
}

const GATE_NO_SWIFT: &str = "#!/bin/bash\n\"$GOH/gates/structural.sh\" \"$@\"\n\
                             # \"$GOH/gates/swift_gate.sh\" .   (a comment is not a call)\n";

#[test]
fn an_unseeded_gap_is_red_once_the_grace_is_over_and_nothing_within_it() {
    let r = repo(&[
        ("tools/gate.sh", GATE_NO_SWIFT),
        ("App/Main.swift", "let x = 1\n"),
    ]);
    let (code, _, err) = judge_at(r.path(), "", false, true, None);
    assert_eq!(code, 1, "{err}");
    assert!(
        err.contains("no swift layer at push") && err.contains("swift_gate.sh"),
        "{err}"
    );
    let (code, _, err) = judge_at(r.path(), "", true, true, None);
    assert_eq!(code, 1, "the commit scope reads the whole index: {err}");
    // Nothing is judged within the grace, and the gap the day will judge is shown (GOH.GRACESHAPES).
    let (code, out, err) = judge_at(r.path(), "", false, false, None);
    assert_eq!((code, out.as_str()), (0, ""));
    assert!(
        err.contains("2026-11-23") && err.contains("no swift layer at push"),
        "{err}"
    );
}

#[test]
fn a_layer_in_a_script_the_gate_runs_counts_and_an_exempt_tree_needs_none() {
    let r = repo(&[
        (
            "tools/gate.sh",
            "#!/bin/bash\ncase x in --full) bash tools/full.sh ;; esac\n",
        ),
        (
            "tools/full.sh",
            "#!/bin/bash\n\"$GOH/gates/swift_gate.sh\" .\n",
        ),
        ("App/Main.swift", "let x = 1\n"),
    ]);
    let (code, out, err) = judge_at(r.path(), "", false, true, None);
    assert_eq!(code, 0, "{err}");
    assert!(out.contains("every language linted at commit"), "{out}");
    let r = repo(&[
        ("tools/gate.sh", GATE_NO_SWIFT),
        (".swiftlint.yml", "excluded:\n  - tests/fixtures\n"),
        ("tests/fixtures/Bad.swift", "let x = 1\n"),
        ("vendor/lib.py", "x = 1\n"),
    ]);
    assert_eq!(judge_at(r.path(), "^vendor/", false, true, None).0, 0);
    assert_eq!(
        judge_at(r.path(), "", false, true, None).0,
        1,
        "vendor/lib.py, not exempt"
    );
}

#[test]
fn a_seeded_gap_warns_and_a_closed_one_is_stale() {
    let r = repo(&[
        ("tools/gate.sh", GATE_NO_SWIFT),
        ("App/Main.swift", "let x = 1\n"),
    ]);
    seed(&r, "tools/gate.sh:swift");
    let (code, out, err) = judge_at(r.path(), "", false, true, None);
    assert_eq!(code, 0, "{err}");
    assert!(
        err.contains("GOH.LAYERPARITY") && err.contains("tools/gate.sh:swift"),
        "{err}"
    );
    assert!(
        out.contains("nothing beyond the GOH.LAYERPARITY seed"),
        "{out}"
    );
    r.write(
        "tools/gate.sh",
        "#!/bin/bash\n\"$GOH/gates/swift_gate.sh\" .\n",
    )
    .expect("fix");
    r.git(&["commit", "-qam", "fix"]).expect("commit");
    let (code, _, err) = judge_at(r.path(), "", false, true, None);
    assert_eq!(code, 1, "{err}");
    assert!(
        err.contains("stale") && err.contains("tools/gate.sh:swift"),
        "{err}"
    );
}

#[test]
fn push_text_follows_two_levels_of_the_repos_own_scripts_and_no_further() {
    let r = repo(&[
        ("tools/gate.sh", "bash tools/a.sh\n"),
        ("tools/a.sh", "./tools/b.sh\n"),
        ("tools/b.sh", "tools/c.sh\n"),
        ("tools/c.sh", "\"$GOH/gates/rust_gate.sh\" .\n"),
        (".gatesrc", "GOH_CI_STEPS='python3 tools/d.py:cargo test'\n"),
        (
            "tools/d.py",
            "# py_gate.sh in a comment\nrun('swift_gate.sh')\n",
        ),
    ]);
    let tree: BTreeSet<String> = crate::gitutil::listed_files(r.path(), false)
        .expect("ls")
        .into_iter()
        .collect();
    let text = push_text(r.path(), &tree, false);
    assert!(
        text.contains("tools/c.sh") && !text.contains("rust_gate.sh"),
        "{text}"
    );
    assert!(
        text.contains("swift_gate.sh") && !text.contains("py_gate.sh"),
        "{text}"
    );
    assert_eq!(counts(r.path(), &tree, None, false), [0, 1, 0]);
}
