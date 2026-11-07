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
/// classes shared, so every function word goes, and the verb that says only that something ran.
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
    // "Run" is how a class says something happened, never which: it and `work` were all that
    // per-pixel colour work and a rule racing a notice shared (ZoneWM, 2026-10-12).
    "run",
    "runs",
    "ran",
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

/// Of the smaller class's content words, a match needs at least one in `SHARE`. Two words are
/// evidence of a class in a six-word phrase and chance in a fifteen-word one: `ZoneWM`'s `54b348de`
/// (ZW.REV2, 2026-11-07) shared `hold`+`narrower` with a nine-word class and `form`+`value` with an
/// eighteen-word one, and was refused as their third instance. A rarity weight cannot see that --
/// `narrower` and `form` are each in ONE class of the registry, so they outweigh the words a true
/// restatement shares (measured over `ZoneWM`'s 207 classes) -- but length can. Of its 326 matching
/// pairs, 33 fall under a quarter, every one two common words in a smaller class of 9 to 11; the
/// thinnest overlap among the pairs kept is 2 of 7.
pub(super) const SHARE: usize = 4;

/// Two content words shared (a one-word class needs that word), the repo's `vocabulary` aside, and
/// at least a `SHARE`th of the smaller class's words. The two-word floor was calibrated on `ZoneWM`'s 54
/// classes and the pairs their author labelled (2026-10-08): the prototype's "half the smaller
/// class" matched 1 of 13 same-class pairs; this matches 6, with 2 of 1,418 other pairs and
/// neither labelled near-miss. The other 7 are paraphrase, which no word overlap sees.
pub(super) fn same_class(a: &str, b: &str, vocabulary: &BTreeSet<String>) -> bool {
    let (wa, wb) = (&words(a) - vocabulary, &words(b) - vocabulary);
    let smaller = wa.len().min(wb.len());
    let shared = wa.intersection(&wb).count();
    smaller > 0 && shared >= smaller.min(2) && shared * SHARE >= smaller
}
