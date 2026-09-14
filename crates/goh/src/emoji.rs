//! Disallowed-emoji scan — Rust port of `checks/check_no_emoji.py`.
//!
//! Policy: emoji are a failure state. Only the Kare icon set, functional Mac
//! key glyphs, domain operators, and legal typographic signs pass; every
//! other char in the emoji/symbol ranges fails — including the VS16 forms of
//! otherwise-allowed glyphs. `ALLOWED_ORDERED` doubles as the failure
//! message's permit list, so policy and message cannot drift apart.
//!
//! Line splitting follows `str::lines` (`\n`, `\r\n`). Python's `splitlines`
//! additionally breaks on `\v`, `\f`, `\u{85}`, `\u{2028}` and friends; a
//! file relying on those as line breaks would number lines differently —
//! accepted, since no tracked file does.

use std::collections::BTreeSet;

/// The complete allow-list, in buckets so the policy is auditable.
/// Mirrors `ALLOWED_ORDERED` (order matters: it is the permit list text).
pub const ALLOWED_ORDERED: &[char] = &[
    '→', '✓', '✗', '⚠', '↔', '↑', '↓', // Kare icons + arrows
    '←', '⌘', '⌥', '⌨', // cardinal arrow + Mac keys
    '⇧', '⌃', '⏎', '⎋', '↵', // Mac keys: shift / ctrl / return / escape / enter
    '⇒', '⇄', // operators: implication / exchange
    '©', '®', '™', // typographic signs, legal meaning
];

/// Codepoint ranges holding emoji / decorative pictographs (inclusive).
/// Mirrors `RANGES`.
pub const RANGES: &[(u32, u32)] = &[
    (0x1F000, 0x1FFFF),
    (0x2600, 0x26FF),
    (0x2700, 0x27BF),
    (0x2300, 0x23FF),
    (0x2B00, 0x2BFF),
    (0x25A0, 0x25FF),
    (0x2190, 0x21FF),
    (0xFE00, 0xFE0F),
    (0x20E3, 0x20E3),
    (0x2934, 0x2935),
    (0x3030, 0x3030),
    (0x3297, 0x3297),
    (0x3299, 0x3299),
    (0x2139, 0x2139),
    (0x24C2, 0x24C2),
];

/// Parse a `--allow` value the way the checker does: spaces stripped, the
/// remaining characters permitted. Mirrors `frozenset(args.allow.replace(" ", ""))`.
#[must_use]
pub fn parse_allow(allow: &str) -> BTreeSet<char> {
    allow.replace(' ', "").chars().collect()
}

/// True when `ch` is disallowed (not in the allow-list, `extra`, or ranges).
#[must_use]
pub fn is_disallowed(ch: char, extra: &BTreeSet<char>) -> bool {
    if ALLOWED_ORDERED.contains(&ch) || extra.contains(&ch) {
        return false;
    }
    let code = ch as u32;
    RANGES.iter().any(|(lo, hi)| *lo <= code && code <= *hi)
}

/// One disallowed sighting.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Hit {
    /// Repo-relative path.
    pub path: String,
    /// 1-based line number.
    pub lineno: usize,
    /// 1-based character column.
    pub col: usize,
    /// The offending character.
    pub ch: char,
}

/// Scan decoded text, returning every disallowed sighting.
#[must_use]
pub fn scan_text(path: &str, text: &str, extra: &BTreeSet<char>) -> Vec<Hit> {
    let mut hits = Vec::new();
    for (lineno, line) in text.lines().enumerate() {
        for (col, ch) in line.chars().enumerate() {
            if is_disallowed(ch, extra) {
                hits.push(Hit {
                    path: path.to_owned(),
                    lineno: lineno + 1,
                    col: col + 1,
                    ch,
                });
            }
        }
    }
    hits
}

/// Scan every in-scope file under `root`.
///
/// Files that fail strict UTF-8 decoding are binary and skipped, exactly like
/// the reference. Returns hits plus the decodable-file count.
///
/// # Errors
///
/// Returns a message when git lists files and fails.
pub fn scan_root(
    root: &std::path::Path,
    exclude: Option<&regex::Regex>,
    extra: &BTreeSet<char>,
    staged: bool,
) -> Result<(Vec<Hit>, usize), String> {
    let mut hits = Vec::new();
    let mut checked = 0;
    for path in crate::gitutil::listed_files(root, staged)? {
        if let Some(rx) = exclude {
            if rx.is_match(&path) {
                continue;
            }
        }
        let Some(blob) = crate::gitutil::content_bytes(root, &path, staged) else {
            continue;
        };
        let Ok(text) = std::str::from_utf8(&blob) else {
            continue;
        };
        checked += 1;
        hits.extend(scan_text(&path, text, extra));
    }
    Ok((hits, checked))
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]
mod tests {
    use super::*;

    fn extra() -> BTreeSet<char> {
        BTreeSet::new()
    }

    #[test]
    fn kare_set_and_typographic_signs_pass() {
        for ch in ALLOWED_ORDERED {
            assert!(!is_disallowed(*ch, &extra()), "{ch}");
        }
    }

    #[test]
    fn emoji_blocks_fail() {
        // Party popper, check-mark-button (U+2713 passes, U+2705 fails),
        // warn-sign VS16, keycap combiner, wavy dash, circled ideographs.
        // Escapes, never literals: a literal here would trip this very gate.
        for ch in [
            '\u{1F389}',
            '\u{2705}',
            '\u{FE0F}',
            '\u{20E3}',
            '\u{26D4}',
            '\u{2934}',
            '\u{3030}',
            '\u{3297}',
            '\u{24C2}',
        ] {
            assert!(is_disallowed(ch, &extra()), "{ch}");
        }
    }

    #[test]
    fn vs16_of_allowed_glyph_fails() {
        assert!(!is_disallowed('⚠', &extra()));
        assert!(is_disallowed('\u{FE0F}', &extra()));
    }

    #[test]
    fn allow_list_permits_extra_chars() {
        let extra = parse_allow("\u{1F389} \u{2705}");
        assert!(!is_disallowed('\u{1F389}', &extra));
        assert!(!is_disallowed('\u{2705}', &extra));
        assert!(is_disallowed('\u{26D4}', &extra));
    }

    #[test]
    fn columns_count_characters() {
        let hits = scan_text("f", "ab\u{1F389}cd", &extra());
        assert_eq!(hits.len(), 1);
        assert_eq!((hits[0].lineno, hits[0].col), (1, 3));
    }
}
