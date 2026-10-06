//! Markdown text helpers shared by the ported prose checkers. Port of the
//! parts of `checks/_md_text.py` they use.

use regex::Regex;

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

/// The fence and indented-block patterns.
pub struct Fences {
    fence: Regex,
    fence_any: Regex,
    indented: Regex,
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
