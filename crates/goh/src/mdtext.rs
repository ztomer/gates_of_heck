//! Markdown text -> the links it contains and the anchors it generates.
//! Port of `checks/_md_text.py`, shared by the ported prose checkers.
//!
//! An anchor is not the heading (GitHub lowercases, keeps alphanumerics,
//! spaces, `-` and `_`, and hyphenates spaces); code is not content for LINK
//! extraction (fences, indented blocks, inline spans) but is for anchors;
//! duplicate headings are numbered `-1`, `-2`.

use std::collections::BTreeSet;

use regex::Regex;
use unicode_normalization::UnicodeNormalization;

/// `str.splitlines()`: every Python line boundary, no trailing empty line.
#[must_use]
pub fn splitlines(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0usize;
    let mut it = text.char_indices().peekable();
    while let Some((i, c)) = it.next() {
        let boundary = matches!(
            c,
            '\n' | '\r'
                | '\u{b}'
                | '\u{c}'
                | '\u{1c}'
                | '\u{1d}'
                | '\u{1e}'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        );
        if !boundary {
            continue;
        }
        out.push(&text[start..i]);
        let mut next = i + c.len_utf8();
        if c == '\r' && it.peek().is_some_and(|(_, n)| *n == '\n') {
            it.next();
            next += 1;
        }
        start = next;
    }
    if start < text.len() {
        out.push(&text[start..]);
    }
    out
}

/// The fence and indented-block patterns, and the link/anchor grammar.
pub struct Fences {
    fence: Regex,
    fence_any: Regex,
    indented: Regex,
    link: Regex,
    reference: Regex,
    atx: Regex,
    setext: Regex,
    explicit_id: Regex,
    html_anchor: Regex,
    scheme: Regex,
    html_tag: Regex,
    emphasis: Regex,
}

impl Fences {
    /// # Errors
    /// A built-in pattern that does not compile.
    pub fn new() -> Result<Self, String> {
        let rx = |p: &str| Regex::new(p).map_err(|e| format!("markdown pattern {p:?}: {e}"));
        Ok(Self {
            fence: rx(r"^\s{0,3}(`{3,}|~{3,})")?,
            fence_any: rx(r"^\s*(`{3,}|~{3,})")?,
            indented: rx(r"^(?: {4}|\t)")?,
            link: rx(r#"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+["'(][^)]*)?\s*\)"#)?,
            reference: rx(r"^\s{0,3}\[[^\]]+\]:\s*<?([^>\s]+)>?")?,
            atx: rx(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")?,
            setext: rx(r"^\s{0,3}(=+|-+)\s*$")?,
            explicit_id: rx(r"\{#([^}\s]+)\}")?,
            html_anchor: rx(r#"(?i)<a\s+(?:id|name)\s*=\s*["']([^"']+)["']"#)?,
            scheme: rx(r"^[A-Za-z][A-Za-z0-9+.-]*:")?,
            html_tag: rx(r"<[^>]+>")?,
            emphasis: rx(r"[`*_~]")?,
        })
    }

    /// Blank FENCED (and, with `indented`, INDENTED) code blocks, keeping
    /// line structure (`_md_text.strip_fences`).
    #[must_use]
    pub fn strip(&self, text: &str, indented: bool) -> String {
        let fence_re = if indented {
            &self.fence
        } else {
            &self.fence_any
        };
        let mut out: Vec<&str> = Vec::new();
        let mut fence: Option<String> = None;
        let mut in_indented = false;
        for raw in splitlines(text) {
            let hit = fence_re
                .captures(raw)
                .and_then(|c| c.get(1))
                .map(|m| m.as_str().to_owned());
            if let Some(open) = &fence {
                out.push("");
                if hit.as_ref().is_some_and(|h| {
                    h.chars().next() == open.chars().next() && h.len() >= open.len()
                }) {
                    fence = None;
                }
                continue;
            }
            if hit.is_some() {
                fence = hit;
                out.push("");
                continue;
            }
            if !indented {
                out.push(raw);
                continue;
            }
            if self.indented.is_match(raw) {
                in_indented = true;
                out.push("");
                continue;
            }
            if in_indented {
                if raw.trim().is_empty() {
                    in_indented = false;
                }
                out.push("");
                continue;
            }
            out.push(raw);
        }
        out.join("\n")
    }
}

/// `CODE_SPAN.sub(spaces, line)`: `` (`+)(?:(?!\1).)*?\1 ``. The opening run
/// is greedy and gives back backticks when no closer of its length follows;
/// the closer is the FIRST run of exactly that many backticks' worth.
fn blank_code_spans(line: &str) -> String {
    let c: Vec<char> = line.chars().collect();
    let mut out = String::with_capacity(line.len());
    let mut i = 0usize;
    'outer: while i < c.len() {
        if c[i] == '`' {
            let run = c[i..].iter().take_while(|ch| **ch == '`').count();
            for n in (1..=run).rev() {
                let mut p = i + n;
                while p + n <= c.len() {
                    if c[p..p + n].iter().all(|ch| *ch == '`') {
                        out.extend(std::iter::repeat_n(' ', p + n - i));
                        i = p + n;
                        continue 'outer;
                    }
                    p += 1;
                }
            }
        }
        out.push(c[i]);
        i += 1;
    }
    out
}

/// Python's `str.isalnum` for one character.
fn py_alnum(c: char) -> bool {
    c.is_alphanumeric()
}

impl Fences {
    /// Fenced and indented blocks AND inline code spans blanked (`strip_code`).
    #[must_use]
    pub fn strip_code(&self, text: &str) -> String {
        splitlines(&self.strip(text, true))
            .into_iter()
            .map(blank_code_spans)
            .collect::<Vec<_>>()
            .join("\n")
    }

    /// GitHub's heading anchor (`slugify`). Accented letters survive.
    #[must_use]
    pub fn slugify(&self, heading: &str) -> String {
        let text = self.html_tag.replace_all(heading, "");
        let text = self.emphasis.replace_all(&text, "").replace('\t', " ");
        let text: String = text.nfc().collect();
        let kept: String = text
            .to_lowercase()
            .chars()
            .filter(|c| py_alnum(*c) || " -_".contains(*c))
            .collect();
        kept.trim().replace(' ', "-")
    }

    /// Every anchor a markdown file generates (`anchors`).
    #[must_use]
    pub fn anchors(&self, text: &str) -> BTreeSet<String> {
        let mut out = BTreeSet::new();
        let mut seen: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
        let stripped = self.strip(text, true);
        let lines = splitlines(&stripped);
        for (i, raw) in lines.iter().enumerate() {
            let setext =
                i + 1 < lines.len() && !raw.trim().is_empty() && self.setext.is_match(lines[i + 1]);
            let body = self.atx.captures(raw).map_or_else(
                || setext.then(|| raw.trim().to_owned()),
                |c| c.get(2).map(|m| m.as_str().to_owned()),
            );
            let Some(mut body) = body else {
                for hit in self.html_anchor.captures_iter(raw) {
                    if let Some(m) = hit.get(1) {
                        out.insert(m.as_str().to_owned());
                    }
                }
                continue;
            };
            if let Some(c) = self.explicit_id.captures(&body) {
                if let (Some(all), Some(id)) = (c.get(0), c.get(1)) {
                    out.insert(id.as_str().to_owned());
                    body.truncate(all.start());
                }
            }
            let slug = self.slugify(&body);
            if slug.is_empty() {
                continue;
            }
            let count = seen.get(&slug).copied().unwrap_or(0);
            seen.insert(slug.clone(), count + 1);
            out.insert(if count == 0 {
                slug
            } else {
                format!("{slug}-{count}")
            });
        }
        out
    }

    /// `[(line, target)]` for every link in one markdown file (`links_in`).
    #[must_use]
    pub fn links_in(&self, text: &str) -> Vec<(usize, String)> {
        let cleaned = self.strip_code(text);
        let mut out = Vec::new();
        for (n, raw) in splitlines(&cleaned).into_iter().enumerate() {
            for hit in self.link.captures_iter(raw) {
                if let Some(m) = hit.get(1) {
                    out.push((n + 1, m.as_str().to_owned()));
                }
            }
            if let Some(m) = self.reference.captures(raw).and_then(|c| c.get(1)) {
                out.push((n + 1, m.as_str().to_owned()));
            }
        }
        out
    }

    /// Is this link target out of scope (a scheme, protocol-relative, site-absolute)?
    #[must_use]
    pub fn skipped(&self, target: &str) -> bool {
        self.scheme.is_match(target) || target.starts_with('/')
    }
}
