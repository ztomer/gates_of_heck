//! No-suppression scan — Rust port of `checks/check_no_allow.py`.
//!
//! Clippy runs at `-D warnings` everywhere; warnings are fixed, never
//! silenced. Both `#[allow(…)]` / `#![allow(…)]` and `#[expect(…)]` /
//! `#![expect(…)]` fail — outer or inner — including the same pair wrapped
//! in `#[cfg_attr(…)]`. Machine-generated files carrying `@generated`
//! within their first 40 lines are exempt; nothing else is.
//!
//! Comment handling mirrors `_code_portions` exactly: `//` tails and
//! nestable `/* */` spans stripped by a left-to-right depth counter with
//! state carried across lines, so a doc-comment MENTIONING `#[allow]` is
//! prose, not a suppression. String literals are NOT comment-aware (same
//! as the reference): a `//` inside a string still starts a comment.
//!
//! Line splitting follows `str::lines`; the reference uses `splitlines`
//! (additionally breaks on vertical tab, form feed, bare carriage
//! return). No tracked file relies on those as line breaks — accepted,
//! same as the emoji and home-paths ports.

/// Literal suppression attributes, outer or inner. Mirrors
/// `_ALLOW_PATTERN` — the literal tokens, never a looser regex.
const ALLOW_PATTERN: &str = r"#!?\[(?:allow|expect)\(";

/// The opening of a condition-wrapped suppression. Mirrors
/// `_CFG_ATTR_OPEN`.
const CFG_ATTR_OPEN: &str = r"#!?\[cfg_attr\(";

/// Wrapped `allow(`/`expect(` token, without its guard (the `regex` crate
/// cannot express the reference's `(?<![\w:])` look-behind, so the
/// preceding character is checked by hand — same approach as the
/// home-paths port, zero-width guard, identical columns).
const WRAPPED_SUPPRESSION: &str = r"(?:allow|expect)\(";

/// Double-quoted string literals, blanked before the wrapped search so a
/// mention inside a string is prose. Mirrors `_STRING_LITERAL` (raw
/// strings are not special-cased — same as the reference).
const STRING_LITERAL: &str = r#""(?:[^"\\]|\\.)*""#;

/// Generation marker: exempts the file when found within its first 40
/// lines, mirroring `_GENERATED_MARKER`. A mention inside a code span
/// (between backticks) is documentation, not the marker: counting it
/// exempted this very file from its own check (2026-09-23).
const GENERATED_MARKER: &str = "@generated";

/// The code-span delimiter a documented mention of the marker sits in.
const CODE_SPAN: char = '`';

/// Head-line window for the generation marker: the reference reads the
/// first 40 LINES (the docstring wins over character windows).
const GENERATED_HEAD_LINES: usize = 40;

/// Report cap: the reference shows the first 40 hits with no overflow
/// line — mirrored exactly, named so the cap cannot drift silently.
const MAX_REPORT_HITS: usize = 40;

/// Compiled patterns for one scan.
pub struct Scanner {
    /// Literal suppression attributes.
    allow: regex::Regex,
    /// `cfg_attr(` attribute openings.
    cfg_attr_open: regex::Regex,
    /// Wrapped suppression tokens.
    wrapped: regex::Regex,
    /// String literals to blank.
    string_literal: regex::Regex,
}

impl Scanner {
    /// Compile every built-in pattern.
    ///
    /// # Errors
    ///
    /// Returns a message only if a built-in pattern fails to compile,
    /// which is a programming error, never input-dependent.
    pub fn compile() -> Result<Self, String> {
        Ok(Self {
            allow: regex::Regex::new(ALLOW_PATTERN)
                .map_err(|e| format!("built-in allow pattern failed: {e}"))?,
            cfg_attr_open: regex::Regex::new(CFG_ATTR_OPEN)
                .map_err(|e| format!("built-in cfg_attr pattern failed: {e}"))?,
            wrapped: regex::Regex::new(WRAPPED_SUPPRESSION)
                .map_err(|e| format!("built-in wrapped pattern failed: {e}"))?,
            string_literal: regex::Regex::new(STRING_LITERAL)
                .map_err(|e| format!("built-in string pattern failed: {e}"))?,
        })
    }

    /// Strip comments from one line, threading block-comment depth across
    /// lines. Returns `(code_only, depth_after)`. Character-indexed, like
    /// the reference (byte indexing would split multi-byte chars when a
    /// comment marker lands beside one).
    fn strip_line(line: &str, depth: usize) -> (String, usize) {
        let chars: Vec<char> = line.chars().collect();
        let mut kept = String::new();
        let mut depth = depth;
        let mut i = 0;
        while i < chars.len() {
            if depth == 0 && chars[i] == '/' && chars.get(i + 1) == Some(&'/') {
                break;
            }
            if chars[i] == '/' && chars.get(i + 1) == Some(&'*') {
                depth += 1;
                i += 2;
                continue;
            }
            if depth > 0 && chars[i] == '*' && chars.get(i + 1) == Some(&'/') {
                depth -= 1;
                i += 2;
                continue;
            }
            if depth == 0 {
                kept.push(chars[i]);
            }
            i += 1;
        }
        (kept, depth)
    }

    /// True when a wrapped token at byte `start` in `code` is a real
    /// suppression, not a longer identifier (`allow_x`) or a path
    /// (`cfg::allow(`/ `something::expect(`).
    fn wrapped_at(code: &str, start: usize) -> bool {
        let Some(prev) = code[..start].chars().next_back() else {
            return true;
        };
        !(prev.is_ascii_alphanumeric() || prev == '_' || prev == ':')
    }

    /// Whether `text` holds a wrapped `allow(`/`expect(` (not a path segment).
    fn has_wrapped(&self, text: &str) -> bool {
        self.wrapped
            .find_iter(text)
            .any(|m| Self::wrapped_at(text, m.start()))
    }

    /// Scan comment-stripped code lines, returning
    /// `(lineno, stripped_line)` hits. The bracket depth of an open
    /// `#[cfg_attr(` attribute carries across lines; the attribute ends
    /// when its brackets balance (the opening `#[` counts, so depth
    /// returns to 0 on its `)]`).
    fn scan_code(&self, text: &str) -> Vec<(usize, String)> {
        let mut hits = Vec::new();
        let mut depth = 0;
        let mut in_cfg_attr: i32 = 0;
        for (idx, line) in text.split('\n').enumerate() {
            let lineno = idx + 1;
            let (code, next_depth) = Self::strip_line(line, depth);
            depth = next_depth;
            if self.allow.is_match(&code) {
                hits.push((lineno, code.trim().to_owned()));
                continue;
            }
            // The opener is looked for OUTSIDE string literals: one spelled
            // inside a string (a fixture writing a file) opened the attribute
            // mid-literal, its `]` stayed inside the quotes, and every later
            // `allow(`/`expect(` line — `.expect("x")` included — was flagged
            // (2026-09-23). A quoted one is still judged on its own line, as a
            // quoted `#[allow(` is above; it just never carries state.
            let literal_free_line = self.string_literal.replace_all(&code, "\"\"").into_owned();
            let opened = (in_cfg_attr == 0)
                .then(|| self.cfg_attr_open.find(&literal_free_line))
                .flatten();
            if opened.is_none() && in_cfg_attr == 0 {
                if let Some(quoted) = self.cfg_attr_open.find(&code) {
                    if self.has_wrapped(&code[quoted.start()..]) {
                        hits.push((lineno, code.trim().to_owned()));
                    }
                }
                continue;
            }
            let literal_free = opened.map_or(literal_free_line.as_str(), |m| {
                &literal_free_line[m.start()..]
            });
            if self.has_wrapped(literal_free) {
                hits.push((lineno, code.trim().to_owned()));
            }
            let opens = literal_free.chars().filter(|c| *c == '[').count();
            let closes = literal_free.chars().filter(|c| *c == ']').count();
            // Bracket counts per line are tiny; saturating at MAX keeps an
            // open attribute open, which is the safe direction (a vanished
            // `[` would close the attribute early and miss suppressions).
            in_cfg_attr = (in_cfg_attr + i32::try_from(opens).unwrap_or(i32::MAX)
                - i32::try_from(closes).unwrap_or(i32::MAX))
            .max(0);
        }
        hits
    }
}

/// One suppression sighting.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Hit {
    /// Repo-relative path.
    pub path: String,
    /// 1-based line number.
    pub lineno: usize,
    /// The comment-stripped, trimmed offending line.
    pub code: String,
}

/// True for compiled Rust sources.
///
/// A `src/`, `benches/`, `tests/` or `examples/` segment ANYWHERE in the
/// path (nested crates included), and `build.rs`. Tests are in scope — a
/// suppression there defeats the gate just the same.
#[must_use]
pub fn is_compiled_src(rel: &str) -> bool {
    if rel == "build.rs" || rel.ends_with("/build.rs") {
        return true;
    }
    ["src", "benches", "tests", "examples"]
        .iter()
        .any(|d| rel.starts_with(&format!("{d}/")) || rel.contains(&format!("/{d}/")))
}

/// True when the file carries the generation marker within its first 40
/// lines — the only exemption. Mirrors `_is_generated` (the docstring
/// wins over character windows: lines, not bytes).
fn is_generated(text: &str) -> bool {
    let head = text
        .lines()
        .take(GENERATED_HEAD_LINES)
        .collect::<Vec<_>>()
        .join("\n");
    head.match_indices(GENERATED_MARKER).any(|(at, marker)| {
        let before = head[..at].chars().next_back();
        let after = head[at + marker.len()..].chars().next();
        before != Some(CODE_SPAN) && after != Some(CODE_SPAN)
    })
}

/// Scan one decoded blob, returning every suppression sighting.
#[must_use]
pub fn scan_text(scanner: &Scanner, path: &str, text: &str) -> Vec<Hit> {
    if is_generated(text) {
        return Vec::new();
    }
    scanner
        .scan_code(text)
        .into_iter()
        .map(|(lineno, code)| Hit {
            path: path.to_owned(),
            lineno,
            code,
        })
        .collect()
}

/// Scan every compiled Rust source under `root`. Returns hits plus the
/// scanned-file list (the empty-scope refusal needs to distinguish "no
/// Rust matched" from "no Rust present").
///
/// # Errors
///
/// Returns a message when git lists files and fails, or a built-in
/// pattern fails to compile.
pub fn scan_root(
    root: &std::path::Path,
    files: &[String],
    exclude: Option<&regex::Regex>,
    staged: bool,
) -> Result<(Vec<Hit>, Vec<String>), String> {
    let scanner = Scanner::compile()?;
    let mut hits = Vec::new();
    let mut matched = Vec::new();
    for rel in files {
        // Case-sensitive, like the reference's `endswith(".rs")`:
        // `FOO.RS` is not a Rust source to either implementation.
        let is_rs = std::path::Path::new(rel)
            .extension()
            .is_some_and(|ext| ext == "rs");
        if !is_rs || !is_compiled_src(rel) {
            continue;
        }
        if let Some(rx) = exclude {
            if rx.is_match(rel) {
                continue;
            }
        }
        // Matched means scope-matched: generated and unreadable files
        // count toward the scanned total (and against the blind-layout
        // refusal) exactly like the reference, which filters content but
        // never the file list.
        matched.push(rel.clone());
        let Some(blob) = crate::gitutil::content_bytes(root, rel, staged) else {
            continue;
        };
        // NOTE: generated detection decodes lossy, like the reference's
        // `errors="replace"` — a file unreadable as UTF-8 is still
        // scannable, never silently exempt.
        let text = String::from_utf8_lossy(&blob);
        if is_generated(&text) {
            continue;
        }
        hits.extend(scan_text(&scanner, rel, &text));
    }
    Ok((hits, matched))
}

/// True when the repo contains Rust at all: a tracked `Cargo.toml` is the
/// manifest that says so. A repo with Rust but zero matched sources is
/// blind (the layout moved), not clean.
#[must_use]
pub fn has_rust(files: &[String]) -> bool {
    files
        .iter()
        .any(|f| f == "Cargo.toml" || f.ends_with("/Cargo.toml"))
}

/// Classify a finished scan: violations, the blind-layout refusal, or a
/// clean pass over `scanned.len()` files.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ScopeVerdict {
    /// Suppressions to report.
    Violations,
    /// Rust present, nothing matched — the layout moved out from under
    /// the scope. A refusal, never compliance.
    BlindLayout,
    /// Clean pass.
    Clean,
}

/// Classify a finished scan the way the reference does.
#[must_use]
pub const fn classify(hit_count: usize, scanned_count: usize, rust_present: bool) -> ScopeVerdict {
    if hit_count > 0 {
        ScopeVerdict::Violations
    } else if scanned_count == 0 && rust_present {
        ScopeVerdict::BlindLayout
    } else {
        ScopeVerdict::Clean
    }
}

/// Full violation block, mirroring the reference message verbatim.
#[must_use]
pub fn format_report(hits: &[Hit], staged: bool) -> String {
    use std::fmt::Write as _;
    let scope = if staged { "staged" } else { "tracked" };
    let mut text = format!(
        "✗ [no_allow] {} #[allow]/#[expect] in {scope} Rust source:\n",
        hits.len()
    );
    for hit in hits.iter().take(MAX_REPORT_HITS) {
        let _ = writeln!(text, "    {}:{}: {}", hit.path, hit.lineno, hit.code);
    }
    text.push_str(
        "fix the finding properly; do not add #[allow] or #[expect]. Generated files must carry @generated.\n",
    );
    text
}

/// The blind-layout refusal, mirroring the reference verbatim.
#[must_use]
pub fn format_blind() -> String {
    "✗ [no_allow] this repo has Rust (a Cargo.toml is tracked) and the scan matched NO compiled source. That is not a clean run -- the crate layout moved out from under src/, benches/ and build.rs.\n"
        .to_owned()
}

/// The clean-pass line, mirroring the reference.
#[must_use]
pub fn format_ok(file_count: usize) -> String {
    format!("✓ [no_allow] OK — {file_count} files clean\n")
}

#[cfg(test)]
#[path = "noallow_tests.rs"]
mod tests;
