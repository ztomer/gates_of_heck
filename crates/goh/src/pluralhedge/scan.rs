//! The finder: a `(s)`, `(es)` or `(ies)` glued to a word INSIDE a string literal.
//!
//! Line-based, as `goh status-markers` is: a whole-line comment is prose about a string, not a
//! string, and a trailing comment ends the code that is read. Which quotes open a string depends
//! on the language: `"` everywhere, `'` too in Python and shell (in Rust and Swift a `'` is a char
//! literal or a lifetime). A hedge must follow a letter or a `}` (`{unit}(s)` is a hedge built
//! from a placeholder). Not a hedge, by what it is: `http(s)` (a scheme), a call in a code
//! fragment held in a string (`console.log(s)`, `module_from_spec(s)`, `def close(s):` -- a word
//! after `.`, `def `, `fn ` or `func `, or one with `_` in it). What the lexing misses -- a
//! hedge inside a multi-line string's middle lines (a heredoc, a docstring body) -- is invisible
//! here, and said so in `mod.rs`.

/// The language a file is lexed as: which quote characters open a string, and its comment opener.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lang {
    /// Python and shell: `"` and `'` strings, `#` comments.
    Hash,
    /// Rust and Swift: `"` strings, `//` comments.
    Slash,
}

fn is_comment_line(line: &str, lang: Lang) -> bool {
    let t = line.trim_start();
    match lang {
        Lang::Hash => t.starts_with('#') && !t.starts_with("#!"),
        Lang::Slash => {
            t.starts_with("//") || t.starts_with("/*") || t.starts_with("* ") || t == "*"
        }
    }
}

/// The byte spans of `line`'s string literals' contents, up to a trailing comment.
#[must_use]
pub fn literal_spans(line: &str, lang: Lang) -> Vec<(usize, usize)> {
    if is_comment_line(line, lang) {
        return Vec::new();
    }
    let mut spans = Vec::new();
    let mut open: Option<(char, usize)> = None;
    let mut escaped = false;
    let mut prev = ' ';
    for (i, c) in line.char_indices() {
        if let Some((q, start)) = open {
            if escaped {
                escaped = false;
            } else if c == '\\' {
                escaped = true;
            } else if c == q {
                spans.push((start, i));
                open = None;
            }
        } else if c == '"' || (c == '\'' && lang == Lang::Hash) {
            open = Some((c, i + 1));
        } else if lang == Lang::Hash && c == '#' && (prev.is_whitespace() || i == 0) {
            break;
        } else if lang == Lang::Slash && c == '/' && line[i + 1..].starts_with('/') {
            break;
        }
        prev = c;
    }
    if let Some((_, start)) = open {
        spans.push((start, line.len())); // a string that runs on past the line's end
    }
    spans
}

/// The hedges in one piece of string text: each `word(s)` as written.
#[must_use]
pub fn hedges(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    for suffix in ["(s)", "(es)", "(ies)"] {
        for (at, _) in text.match_indices(suffix) {
            let before = &text[..at];
            let Some(last) = before.chars().next_back() else {
                continue;
            };
            if !(last.is_alphabetic() || last == '}') {
                continue;
            }
            let start = before
                .char_indices()
                .rev()
                .find(|(_, c)| !(c.is_alphanumeric() || *c == '_' || *c == '{' || *c == '}'))
                .map_or(0, |(i, c)| i + c.len_utf8());
            let word = &before[start..];
            let lead = &before[..start];
            if word.eq_ignore_ascii_case("http")
                || (word.contains('_') && !word.contains('{'))
                || lead.ends_with('.')
                || ["def ", "fn ", "func "].iter().any(|k| lead.ends_with(k))
            {
                continue;
            }
            out.push(format!("{word}{suffix}"));
        }
    }
    out
}

/// One finding: its line and the hedge.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub line: usize,
    pub hedge: String,
}

/// Every line of `source` whose string literals hedge a plural, the first hedge named.
#[must_use]
pub fn findings(source: &str, lang: Lang) -> Vec<Finding> {
    let mut out = Vec::new();
    for (i, line) in source.lines().enumerate() {
        let first = literal_spans(line, lang)
            .into_iter()
            .find_map(|(a, b)| hedges(&line[a..b]).into_iter().next());
        if let Some(hedge) = first {
            out.push(Finding { line: i + 1, hedge });
        }
    }
    out
}
