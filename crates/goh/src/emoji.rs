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
    /// Written as an escape sequence rather than the character itself.
    pub escaped: bool,
}

/// Scan decoded text, returning every disallowed sighting.
///
/// Written out, or written as an escape (the Rust brace form, the Python
/// eight-digit form, the JS four-digit form) that renders as one. Same rule
/// as the Python checker, same order per line: literal sightings first, then
/// escapes.
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
                    escaped: false,
                });
            }
        }
        for (col, ch) in escaped_violations(line, extra) {
            hits.push(Hit {
                path: path.to_owned(),
                lineno: lineno + 1,
                col,
                ch,
                escaped: true,
            });
        }
    }
    hits
}

/// `(1-based char column, decoded char)` for every escape on `line` naming a
/// disallowed codepoint.
///
/// The brace form takes 1..=6 hex digits, the uppercase-U form eight, the
/// lowercase-u form four; anything that is not a scalar value is skipped, as
/// is any escape whose character is permitted.
#[must_use]
pub fn escaped_violations(line: &str, extra: &BTreeSet<char>) -> Vec<(usize, char)> {
    let bytes = line.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i + 1 < bytes.len() {
        if bytes[i] != b'\\' {
            i += 1;
            continue;
        }
        let (digits, consumed) = match bytes[i + 1] {
            b'u' if bytes.get(i + 2) == Some(&b'{') => {
                let start = i + 3;
                let end = start
                    + bytes[start..]
                        .iter()
                        .take_while(|b| b.is_ascii_hexdigit())
                        .count();
                if bytes.get(end) == Some(&b'}') && (1..=6).contains(&(end - start)) {
                    (&line[start..end], end + 1 - i)
                } else {
                    i += 1;
                    continue;
                }
            }
            b'U' | b'u' => {
                let n = if bytes[i + 1] == b'U' { 8 } else { 4 };
                let Some(end) = hex_run(bytes, i + 2, n) else {
                    i += 1;
                    continue;
                };
                (&line[i + 2..end], end - i)
            }
            _ => {
                i += 1;
                continue;
            }
        };
        if let Some(ch) = u32::from_str_radix(digits, 16)
            .ok()
            .and_then(char::from_u32)
        {
            if is_disallowed(ch, extra) {
                // 1-based CHARACTER column of the backslash, as the Python
                // checker reports (`m.start() + 1` over a str).
                out.push((line[..i].chars().count() + 1, ch));
            }
        }
        i += consumed;
    }
    out
}

/// `Some(end)` when exactly `n` hex digits start at `start`.
fn hex_run(bytes: &[u8], start: usize, n: usize) -> Option<usize> {
    let end = start + n;
    (end <= bytes.len() && bytes[start..end].iter().all(u8::is_ascii_hexdigit)).then_some(end)
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

/// The permit-list text: the allow-list joined by spaces, plus raw `--allow`.
/// Shared so the message and the policy cannot drift apart.
#[must_use]
pub fn permit_text(allow: &str) -> String {
    let mut permit = ALLOWED_ORDERED
        .iter()
        .map(char::to_string)
        .collect::<Vec<_>>()
        .join(" ");
    if !allow.is_empty() {
        permit.push_str(" + ");
        permit.push_str(allow);
    }
    permit
}

/// Full violation block. Shared by the `emoji` subcommand and the structural
/// pipeline so both print one text.
#[must_use]
pub fn format_report(hits: &[Hit], staged: bool, allow: &str) -> String {
    let scope = if staged { "staged" } else { "tracked" };
    let mut shown = String::new();
    for hit in hits.iter().take(200) {
        let suffix = if hit.escaped {
            " (written as an escape)"
        } else {
            ""
        };
        let line = format!(
            "  {}:{}:{}: U+{:04X} '{}'{suffix}\n",
            hit.path, hit.lineno, hit.col, hit.ch as u32, hit.ch
        );
        shown.push_str(&line);
    }
    let overflow = if hits.len() > 200 {
        format!("  … and {} more\n", hits.len() - 200)
    } else {
        String::new()
    };
    format!(
        "✗ DISALLOWED EMOJI in {} location(s) ({scope}) — only the Kare icon set and functional typographic glyphs are permitted ({}); © ® ™ are typographic signs with legal meaning, but their emoji-presentation VS16 forms fail:\n{shown}{overflow}",
        hits.len(),
        permit_text(allow)
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Forbidden glyphs are built from their NUMBERS, never written as
    /// literals or escapes: either would trip this very gate on this file.
    fn c(code: u32) -> char {
        char::from_u32(code).expect("scalar value")
    }

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
            c(0x1F389),
            c(0x2705),
            c(0xFE0F),
            c(0x20E3),
            c(0x26D4),
            c(0x2934),
            c(0x3030),
            c(0x3297),
            c(0x24C2),
        ] {
            assert!(is_disallowed(ch, &extra()), "{ch}");
        }
    }

    #[test]
    fn vs16_of_allowed_glyph_fails() {
        assert!(!is_disallowed('⚠', &extra()));
        assert!(is_disallowed(c(0xFE0F), &extra()));
    }

    #[test]
    fn allow_list_permits_extra_chars() {
        let extra = parse_allow(&format!("{} {}", c(0x1F389), c(0x2705)));
        assert!(!is_disallowed(c(0x1F389), &extra));
        assert!(!is_disallowed(c(0x2705), &extra));
        assert!(is_disallowed(c(0x26D4), &extra));
    }

    #[test]
    fn columns_count_characters() {
        let hits = scan_text("f", &format!("ab{}cd", c(0x1F389)), &extra());
        assert_eq!(hits.len(), 1);
        assert_eq!((hits[0].lineno, hits[0].col), (1, 3));
    }
}
