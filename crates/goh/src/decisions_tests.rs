//! Every rule red on a planted record and green on the clean one, at both scopes.

use super::{is_path, judge, parse, tokens, tracked, FIELDS};
use goh_testkit::repo_with;
use std::collections::BTreeSet;

const REC: &str = "# D-0001 \u{2014} a record\n\n\
- **Question:** Is the sky blue?\n\
- **Evidence:** Looked\n  twice.\n\
- **Options priced:**\n  1. Blue. TAKEN.\n  2. Grey. Rejected.\n\
- **Ruling:** Blue.\n\
- **Falsifier:** A grey day.\n\
- **Re-open when:** Night.\n\
- **Lives in:** `src/sky.rs`, `tests/`,\n  `paint_sky`, `goh land`\n\n\
Amended later: `gone/elsewhere.rs` is prose, not a field.\n";

fn ledger(rec: &str) -> goh_testkit::Repo {
    repo_with(&[
        ("docs/decisions/D-0001-a-record.md", rec),
        ("src/sky.rs", "fn paint_sky() {}\n"),
        ("tests/t.py", "goh land\n"),
    ])
    .expect("fixture")
}

fn findings(rec: &str, staged: bool) -> Vec<String> {
    let repo = ledger(rec);
    judge(repo.path(), staged).expect("judge").1
}

#[test]
fn a_wrapped_field_is_read_whole_and_prose_after_a_blank_line_is_not_a_field() {
    let rec = parse(REC);
    assert_eq!(rec.id.as_deref(), Some("D-0001"));
    assert_eq!(
        rec.fields["Options priced"],
        "1. Blue. TAKEN. 2. Grey. Rejected."
    );
    assert_eq!(
        tokens(&rec.fields["Lives in"]),
        ["src/sky.rs", "tests/", "paint_sky", "goh land"]
    );
    for f in FIELDS {
        assert!(!rec.fields[f].is_empty(), "{f}");
    }
}

#[test]
fn a_path_is_a_slash_or_an_extension_and_a_command_is_a_symbol() {
    assert!(is_path("a/b") && is_path("x.py") && is_path("docs/"));
    assert!(!is_path("paint_sky") && !is_path("goh land"));
    let files: BTreeSet<String> = ["a/b/c.rs".to_owned()].into();
    assert!(tracked("a/b/c.rs", &files) && tracked("a/b", &files) && tracked("a/", &files));
    assert!(!tracked("a/b/c", &files) && !tracked("a/bb", &files));
}

#[test]
fn the_clean_record_passes_at_both_scopes() {
    assert_eq!(findings(REC, false), Vec::<String>::new());
    assert_eq!(findings(REC, true), Vec::<String>::new());
}

#[test]
fn a_missing_or_empty_falsifier_is_red() {
    for planted in [
        REC.replace("- **Falsifier:** A grey day.\n", ""),
        REC.replace("- **Falsifier:** A grey day.", "- **Falsifier:**"),
    ] {
        let got = findings(&planted, true);
        assert!(
            got.iter()
                .any(|f| f.contains("`Falsifier` is missing or empty")),
            "{got:?}"
        );
    }
}

#[test]
fn a_lives_in_naming_a_deleted_path_or_an_unknown_symbol_is_red() {
    let got = findings(&REC.replace("src/sky.rs", "src/gone.rs"), true);
    assert!(
        got.iter()
            .any(|f| f.contains("`src/gone.rs`, which is not in the tree")),
        "{got:?}"
    );
    // The record itself names the symbol: that is not evidence it exists.
    let got = findings(&REC.replace("paint_sky", "paint_sea"), false);
    assert!(
        got.iter()
            .any(|f| f.contains("`paint_sea`, which nothing outside")),
        "{got:?}"
    );
}

#[test]
fn a_commit_that_deletes_a_named_path_is_red_at_staged() {
    let repo = ledger(REC);
    repo.git(&["rm", "-q", "--cached", "src/sky.rs"])
        .expect("rm");
    let got = judge(repo.path(), true).expect("judge").1;
    assert!(
        got.iter()
            .any(|f| f.contains("`src/sky.rs`, which is not in the tree")),
        "{got:?}"
    );
}

#[test]
fn an_id_mismatch_a_twin_question_a_dangling_cite_and_an_empty_ledger_are_red() {
    let got = findings(&REC.replace("# D-0001", "# D-0009"), true);
    assert!(
        got.iter().any(|f| f.contains("the heading says D-0009")),
        "{got:?}"
    );
    let got = findings(&REC.replace("Night.", "Night (D-0042)."), true);
    assert!(got.iter().any(|f| f.contains("cites D-0042")), "{got:?}");
    let repo = ledger(REC);
    repo.write(
        "docs/decisions/D-0002-twin.md",
        &REC.replace("D-0001", "D-0002"),
    )
    .expect("write");
    repo.git(&["add", "-A"]).expect("add");
    let got = judge(repo.path(), true).expect("judge").1;
    assert!(
        got.iter().any(|f| f.contains("the same Question")),
        "{got:?}"
    );
    let empty = repo_with(&[("README.md", "x\n")]).expect("fixture");
    let (n, got) = judge(empty.path(), true).expect("judge");
    assert_eq!(n, 0);
    assert!(
        got[0].contains("an empty ledger is not a clean one"),
        "{got:?}"
    );
}

#[test]
fn the_key_is_on_or_off_and_a_typo_is_named() {
    assert_eq!(super::enabled(None), Ok(false));
    assert_eq!(super::enabled(Some("off")), Ok(false));
    assert_eq!(super::enabled(Some("on")), Ok(true));
    assert!(super::enabled(Some("yes"))
        .unwrap_err()
        .contains("GOH_DECISIONS"));
}

#[test]
fn a_record_with_no_heading_and_two_records_with_one_id_are_red() {
    let repo = ledger(REC);
    repo.write(
        "docs/decisions/D-0001-twin.md",
        &format!("preamble\n{}", REC.replace("blue?", "grey?")),
    )
    .expect("write");
    repo.write("docs/decisions/D-0002-bare.md", "no heading here\n")
        .expect("write");
    repo.git(&["add", "-A"]).expect("add");
    let got = judge(repo.path(), true).expect("judge").1;
    assert!(got.iter().any(|f| f.contains("is already")), "{got:?}");
    assert!(got.iter().any(|f| f.contains("no `# D-NNNN")), "{got:?}");
}

#[test]
fn the_verdict_outside_a_repository_is_a_refusal_and_the_step_obeys_its_key() {
    let bare = tempfile::tempdir().expect("tmp");
    assert_eq!(super::verdict(bare.path(), false).0, 2);
    let repo = ledger(REC);
    let mut cfg = crate::gatesrc::Gatesrc::default();
    assert_eq!(super::step(repo.path(), &cfg, true), None, "unset is off");
    cfg.decisions = Some("yes".to_owned());
    assert_eq!(super::step(repo.path(), &cfg, true), Some(2));
    cfg.decisions = Some("on".to_owned());
    assert_eq!(
        super::step(repo.path(), &cfg, true),
        None,
        "a whole ledger passes"
    );
    repo.write(
        "docs/decisions/D-0001-a-record.md",
        &REC.replace("src/sky.rs", "gone.rs"),
    )
    .expect("write");
    repo.git(&["add", "-A"]).expect("add");
    assert_eq!(super::step(repo.path(), &cfg, true), Some(1));
}
