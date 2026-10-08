//! Unit tests for `commit_class` (split out for the line cap).

use super::*;

fn none() -> BTreeSet<String> {
    BTreeSet::new()
}

/// `ZoneWM`'s 75 classes, oldest first (`tests/fixtures/commit_class/zonewm_classes.tsv`).
fn zonewm() -> Vec<(String, String)> {
    include_str!("../../../tests/fixtures/commit_class/zonewm_classes.tsv")
        .lines()
        .filter(|l| !l.starts_with('#'))
        .filter_map(|l| l.split_once('\t'))
        .map(|(sha, class)| (sha.to_owned(), class.to_owned()))
        .collect()
}

/// The replay's verdict on one of `ZoneWM`'s commits: refused or not, against the classes before it.
fn refused(sha: &str, vocabulary: &BTreeSet<String>) -> bool {
    let all = zonewm();
    let at = all
        .iter()
        .position(|(s, _)| s == sha)
        .expect("in the fixture");
    let earlier: Vec<String> = all[..at].iter().map(|(_, c)| c.clone()).collect();
    let message = format!(
        "fix: x\n\nClass: {}\nSiblings: none (grep -rn the pattern)\n",
        all[at].1
    );
    !refusals(&message, &earlier, vocabulary, true).is_empty()
}

/// BACKLOG 2.2: the replay's one wrong refusal shared only `read` and `window`, `ZoneWM`'s own
/// domain nouns. A word in more than an eighth of a repo's classes says nothing about which class.
#[test]
fn domain_frequent_words_do_not_count() {
    let classes: Vec<String> = zonewm().into_iter().map(|(_, c)| c).collect();
    let vocabulary = frequent(&classes);
    assert_eq!(vocabulary.into_iter().collect::<Vec<_>>(), ["read"]);
    let vocabulary = frequent(&classes);
    assert!(refused("078f137f", &none()), "today's rule refuses it");
    assert!(!refused("078f137f", &vocabulary));
    for right in ["6081eb0f", "f1ef8eb1", "5aaf0b95"] {
        assert!(
            refused(right, &vocabulary),
            "{right} is still a third instance"
        );
    }
}

/// A small history makes every word frequent: below the floor nothing is dropped.
#[test]
fn a_small_history_has_no_frequent_words() {
    let classes: Vec<String> = zonewm().into_iter().map(|(_, c)| c).collect();
    assert_eq!(frequent(&classes[..FREQUENT_FLOOR - 1]).len(), 0);
    assert_ne!(frequent(&classes[..FREQUENT_FLOOR]).len(), 0);
}

#[test]
fn the_subjects_it_gates() {
    for s in ["fix: a", "perf(x): a", "fix!: a", "fix(core)!: a"] {
        assert!(gated(s), "{s}");
    }
    for s in [
        "fixup! fix: a",
        "fixes: a",
        "feat: a",
        "fix(x a",
        "docs: fix",
    ] {
        assert!(!gated(s), "{s}");
    }
}

#[test]
fn the_trailer_block_is_the_last_paragraph_only() {
    let lines = cleaned("fix: a\n\nClass: c\n\nSiblings: s\nCo-Authored-By: x\n");
    let t = final_trailers(&lines);
    assert!(t.contains_key("Siblings") && !t.contains_key("Class"));
    assert_eq!(final_trailers(&cleaned("Class: subject only\n")).len(), 0);
    assert_eq!(
        final_trailers(&cleaned("fix: a\n\nprose line\nClass: c\n")).len(),
        0
    );
    let wrapped = final_trailers(&cleaned("fix: a\n\nClass: one\n  two\n"));
    assert_eq!(wrapped.get("Class").map(String::as_str), Some("one two"));
}

/// `ZoneWM`'s 94 commits, labelled by their author (2026-10-08): the pairs it calls one class,
/// and the pairs that share words but are not.
#[test]
fn calibrated_on_zonewm_one_class_pairs_match() {
    let vocabulary = frequent(&zonewm().into_iter().map(|(_, c)| c).collect::<Vec<_>>());
    for (a, b) in [
        (
            "a ceiling recorded over a defect, which then guards the defect",
            "a ceiling recorded before a fix still carries the worst round the fix removed",
        ),
        (
            "a \"mark what is current\" rule run when a notice is HEARD, racing the work",
            "a consumer acting on \"what is current\" at a notice through a copy",
        ),
        (
            "an end-to-end detector that cannot name what it waited on",
            "a detector that can time a slow round but cannot say what the round waited on",
        ),
    ] {
        assert!(same_class(a, b, &none()), "{a} / {b}");
        assert!(
            same_class(a, b, &vocabulary),
            "{a} / {b}, ZoneWM's vocabulary aside"
        );
    }
}

#[test]
fn calibrated_on_zonewm_shared_words_are_not_a_class() {
    for (a, b) in [
        (
            "a teardown closes a surface the product holds",
            "a teardown post-condition that covers what a run raised but not the state it found",
        ),
        (
            "a held surface timed by its appearing",
            "a detector whose own capture cost lands in the reading",
        ),
        (
            "a verdict about framing that never reads what is framed",
            "a picture read at full size to draw something much smaller",
        ),
        (
            "a probe's restore that does not undo what it wrote, or never runs",
            "a verdict about framing that never reads what is framed",
        ),
    ] {
        assert!(!same_class(a, b, &none()), "{a} / {b}");
    }
}

#[test]
fn similarity_is_two_shared_content_words() {
    let n = none();
    assert!(same_class(
        "lock path declared twice",
        "a lock path declared in several files",
        &n
    ));
    assert!(!same_class(
        "window built per show",
        "a lock path declared in several files",
        &n
    ));
    assert!(!same_class("the of", "the of", &n));
}
