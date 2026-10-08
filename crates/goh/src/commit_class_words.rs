//! `goh commit-class`'s "same class?" measure: content words, lightly stemmed, the repo's domain
//! vocabulary aside (split out of `commit_class.rs` for the line cap).

use std::collections::{BTreeMap, BTreeSet};

/// A word in more than an eighth of a repo's classes is its domain vocabulary, not a class:
/// `ZoneWM`'s replay refused `078f137f` on `read` and `window` alone, and over its 75 classes
/// `read` is in 15% while the next word is in 8%, the labelled pairs' `wait` among them (BACKLOG
/// 2.2, 2026-10-08).
pub(super) const FREQUENT_SHARE: usize = 8;
/// Below this many classes every word looks frequent, so none is dropped.
pub(super) const FREQUENT_FLOOR: usize = 30;
/// Words that carry no class: the prototype dropped 17; the calibration on `ZoneWM`'s 54 classes
/// (2026-10-08) found `that`, `what`, `when`, `can` and their like were most of what unrelated
/// classes shared, so every function word goes.
const STOP: &[&str] = &[
    "a",
    "an",
    "the",
    "of",
    "in",
    "on",
    "is",
    "it",
    "its",
    "to",
    "and",
    "or",
    "for",
    "by",
    "not",
    "no",
    "be",
    "that",
    "what",
    "which",
    "when",
    "where",
    "while",
    "whose",
    "who",
    "can",
    "cannot",
    "one",
    "with",
    "from",
    "into",
    "inside",
    "about",
    "than",
    "then",
    "there",
    "this",
    "these",
    "those",
    "every",
    "each",
    "another",
    "other",
    "same",
    "still",
    "never",
    "does",
    "only",
    "much",
    "something",
    "rather",
    "instead",
    "because",
    "after",
    "before",
    "under",
    "over",
    "through",
    "per",
    "own",
    "but",
    "has",
    "have",
    "was",
    "were",
    "are",
    "been",
    "being",
    "their",
    "they",
    "them",
    "would",
    "could",
    "should",
    "must",
    "may",
    "all",
    "any",
    "some",
    "more",
    "most",
    "very",
    "so",
    "as",
    "at",
    "if",
    "up",
    "out",
    "once",
    "how",
    "why",
];

/// `recorded` and `record`, `ceilings` and `ceiling` are one word: the longest inflection off,
/// never below a four-letter root.
fn stem(word: &str) -> &str {
    ["ings", "ing", "ies", "ied", "ed", "es", "s"]
        .iter()
        .find_map(|suffix| word.strip_suffix(suffix).filter(|root| root.len() >= 4))
        .unwrap_or(word)
}

fn words(class: &str) -> BTreeSet<String> {
    class
        .to_lowercase()
        .split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|w| w.len() > 2 && !STOP.contains(w))
        .map(|w| stem(w).to_owned())
        .collect()
}

/// The repo's domain vocabulary: words in more than `1/FREQUENT_SHARE` of `classes`, read over the
/// whole history the check reads (a replay's included: the vocabulary is the repo's, not the
/// moment's), empty below `FREQUENT_FLOOR` classes.
pub(super) fn frequent(classes: &[String]) -> BTreeSet<String> {
    if classes.len() < FREQUENT_FLOOR {
        return BTreeSet::new();
    }
    let mut seen: BTreeMap<String, usize> = BTreeMap::new();
    for class in classes {
        for word in words(class) {
            *seen.entry(word).or_default() += 1;
        }
    }
    seen.into_iter()
        .filter(|(_, n)| n * FREQUENT_SHARE > classes.len())
        .map(|(w, _)| w)
        .collect()
}

/// Two content words shared (a one-word class needs that word), the repo's `vocabulary` aside. Calibrated on `ZoneWM`'s 54
/// classes and the pairs their author labelled (2026-10-08): the prototype's "half the smaller
/// class" matched 1 of 13 same-class pairs; this matches 6, with 2 of 1,418 other pairs and
/// neither labelled near-miss. The other 7 are paraphrase, which no word overlap sees.
pub(super) fn same_class(a: &str, b: &str, vocabulary: &BTreeSet<String>) -> bool {
    let (wa, wb) = (&words(a) - vocabulary, &words(b) - vocabulary);
    let smaller = wa.len().min(wb.len());
    smaller > 0 && wa.intersection(&wb).count() >= smaller.min(2)
}
