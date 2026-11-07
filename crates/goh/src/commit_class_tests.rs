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

/// A wrapped line with no indent in the trailer paragraph keeps git from reading ANY of it; the
/// refusal names that line rather than sending the writer "beside Co-Authored-By", where the keys
/// already were (`ZoneWM`, 2026-10-12: a two-line `Red-run:`).
#[test]
fn a_non_trailer_line_in_the_last_paragraph_is_named() {
    let message = "fix: a\n\nbody\n\nRed-run: a long line\nthat wrapped.\nClass: c\n\
                   Siblings: none (grep -rn pattern)\nCo-Authored-By: x\n";
    let got = refusals(message, &[], &none(), true);
    assert_eq!(got.len(), 1, "{got:?}");
    assert!(got[0].contains("`that wrapped.` is not"), "{got:?}");
    let above = "fix: a\n\nClass: c\nSiblings: none (grep -rn pattern)\n\nCo-Authored-By: x\n";
    let got = refusals(above, &[], &none(), true);
    assert!(got.iter().any(|r| r.contains("sits above")), "{got:?}");
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
        // `run` as a verb, and `work`, were all these shared (ZoneWM 3b457ec1, 2026-10-12).
        (
            "per-pixel colour work run on the CPU that the GPU does within a level",
            "a \"mark what is current\" rule run when a notice is HEARD, racing the work the event caused",
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

/// ZW.REV2 (2026-11-07): `ZoneWM`'s `54b348de` was refused as "the instance after 2 earlier ones" on
/// `hold`+`narrower` (a fixed-width column) and `form`+`value` (a settings row) -- two common words
/// in a fifteen-word class. A rarity weight cannot see it: `narrower` and `form` are in ONE class of
/// the registry each, so they weigh MORE than the words true restatements share.
const REFUSED: &str =
    "a guard that judges a value by its printed form, or catches a narrower base \
    than the failures it holds, lets the shape it did not expect straight through";
const COLUMN: &str = "a fixed-width column narrower than the text it holds clips it mid-glyph";
const SETTINGS_ROW: &str = "a settings row draws prose, a value or a width the system does not \
    have - a caption that restates its control, a slider value the strip is not using, a \
    fixed-width grid wider than the Form column";

#[test]
fn two_common_words_in_a_long_class_are_not_a_class() {
    let n = none();
    assert!(!same_class(REFUSED, COLUMN, &n), "hold, narrower: 2 of 9");
    assert!(
        !same_class(REFUSED, SETTINGS_ROW, &n),
        "form, value: 2 of 15"
    );
    let message = format!("fix: x\n\nClass: {REFUSED}\nSiblings: none (grep -rn the pattern)\n");
    let earlier = [COLUMN.to_owned(), SETTINGS_ROW.to_owned()];
    assert_eq!(refusals(&message, &earlier, &n, true), Vec::<String>::new());
}

/// The other direction: `ZoneWM`'s own restatements, none of them labelled -- the registry's
/// near-duplicates. A share high enough to refuse the pair above must not lose these.
#[test]
fn restatements_in_the_registry_still_match() {
    let n = none();
    for (a, b) in [
        // 5 of 7 words.
        (
            "a fixed sleep stands in for a readiness signal from a spawned process",
            "a fixed sleep stands in for a readiness signal, so a slow-but-ready thing reads as not ready",
        ),
        // 2 of 7: the thinnest overlap a real restatement has in the registry (a quarter of the smaller class).
        (
            "a fixed sleep stands in for a readiness signal from a spawned process",
            "a capture taken after a fixed sleep, racing the asynchronous work whose result it captures",
        ),
        (
            "a test needs two asks to overlap and races a fixture's decode time for it",
            "a test needs two asks to overlap and races a fixture's decode time for it",
        ),
    ] {
        assert!(same_class(a, b, &n), "{a} / {b}");
    }
}
