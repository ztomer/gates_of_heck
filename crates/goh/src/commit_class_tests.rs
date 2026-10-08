//! Unit tests for `commit_class` (split out for the line cap).

use super::*;

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
        assert!(same_class(a, b), "{a} / {b}");
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
        assert!(!same_class(a, b), "{a} / {b}");
    }
}

#[test]
fn similarity_is_two_shared_content_words() {
    assert!(same_class(
        "lock path declared twice",
        "a lock path declared in several files"
    ));
    assert!(!same_class(
        "window built per show",
        "a lock path declared in several files"
    ));
    assert!(!same_class("the of", "the of"));
}
