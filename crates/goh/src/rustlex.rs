//! Rust source read as tokens, a line at a time: what is a comment, what is a
//! string or character literal, and what is code.
//!
//! The checks that read Rust as text kept two partial answers. Comments were
//! stripped by a depth counter that knew nothing of strings, so a `//` inside
//! `"https://..."` ended the line there; strings were blanked by a one-line
//! regex that knew nothing of raw strings, character literals or a string
//! carried onto the next line. So `goh no-allow` flagged `#[allow(...)]`
//! written inside a test fixture's string (a private consumer's `crates/xtask/src/walk_tests.rs:28`,
//! 2026-10-12). One lexer answers all of it, with its state carried across
//! lines: nestable block comments, `//` tails, `"..."` with escapes (and
//! `b"`/`c"`), raw strings `r#"..."#` at any hash count, and character
//! literals told apart from lifetimes.
//!
//! Each line gives three views: `display`, the line with its comments removed
//! and its literals intact (what a finding quotes); `code`, the same with
//! every literal's contents removed (what a pattern is matched against); and
//! `masked`, `display` with every literal character but a brace turned to `"`,
//! column for column (a format string's `{x}` stays visible). The masked view
//! is the lexer's, so a string carried onto the next line by a trailing `\` is
//! masked there too -- a per-line regex read its continuation as code
//! (GOH.EMPTYASSERTSTRING).

/// Where a line starts: in code, or inside a construct an earlier line opened.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum LexState {
    /// Ordinary code.
    #[default]
    Code,
    /// Inside a block comment, at this nesting depth (at least one).
    Block(usize),
    /// Inside a `"..."` string.
    Str,
    /// Inside a raw string closed by `"` and this many `#`.
    Raw(usize),
}

/// One line, read.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Lexed {
    /// Comments removed, literals intact.
    pub display: String,
    /// Comments removed and every literal emptied (`""`, `''`).
    pub code: String,
    /// `display` with each literal character, delimiters included, as `"`, but a brace as itself.
    pub masked: String,
}

impl Lexed {
    fn both(&mut self, c: char) {
        self.display.push(c);
        self.code.push(c);
        self.masked.push(c);
    }

    /// Literal characters: shown in `display`, masked in `masked`.
    fn lit<'a>(&mut self, cs: impl IntoIterator<Item = &'a char>) {
        for c in cs {
            self.display.push(*c);
            self.masked
                .push(if matches!(c, '{' | '}') { *c } else { '"' });
        }
    }
}

const fn is_ident(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// Whether `chars[at]` starts a token: nothing before it, or a character that
/// cannot end an identifier.
const fn token_start(chars: &[char], at: usize) -> bool {
    at == 0 || !is_ident(chars[at - 1])
}

/// The hash count of a raw string opening at `chars[at] == 'r'`, if one does.
/// `r` must start its token, or follow a `b`/`c` prefix that does; `r#ident`
/// (a raw identifier) is not a string.
fn raw_open(chars: &[char], at: usize) -> Option<usize> {
    let prefixed = at > 0 && matches!(chars[at - 1], 'b' | 'c') && token_start(chars, at - 1);
    if !(token_start(chars, at) || prefixed) {
        return None;
    }
    let hashes = chars[at + 1..].iter().take_while(|c| **c == '#').count();
    (chars.get(at + 1 + hashes) == Some(&'"')).then_some(hashes)
}

/// The index just past a character literal opening at `chars[at] == '\''`, or
/// `None` when the quote is a lifetime or a label (`'a`, `'outer:`).
fn char_end(chars: &[char], at: usize) -> Option<usize> {
    match chars.get(at + 1) {
        // `'\n'`, `'\''`, `'\\'`, `'\u{2192}'`: the escaped character is skipped, so an escaped
        // quote is not the close.
        Some('\\') => {
            let after = (at + 3).min(chars.len());
            let close = chars[after..].iter().position(|c| *c == '\'')?;
            Some(after + close + 1)
        }
        Some(_) if chars.get(at + 2) == Some(&'\'') => Some(at + 3),
        _ => None,
    }
}

/// Read one line from `state`, returning its two views and the state the next
/// line starts in. Character-indexed, so a marker beside a multi-byte
/// character never splits it.
#[must_use]
pub fn lex_line(line: &str, state: LexState) -> (Lexed, LexState) {
    let chars: Vec<char> = line.chars().collect();
    let mut out = Lexed::default();
    let mut state = state;
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        let next = chars.get(i + 1).copied();
        match state {
            LexState::Block(depth) => {
                if c == '/' && next == Some('*') {
                    state = LexState::Block(depth + 1);
                    i += 2;
                } else if c == '*' && next == Some('/') {
                    state = if depth == 1 {
                        LexState::Code
                    } else {
                        LexState::Block(depth - 1)
                    };
                    i += 2;
                } else {
                    i += 1;
                }
            }
            LexState::Str => {
                if c == '\\' {
                    out.lit(&chars[i..(i + 2).min(chars.len())]);
                    i += 2;
                } else {
                    if c == '"' {
                        out.code.push('"');
                        state = LexState::Code;
                    }
                    out.lit([&c]);
                    i += 1;
                }
            }
            LexState::Raw(hashes) => {
                let closes = c == '"'
                    && chars.len() > i + hashes
                    && chars[i + 1..=i + hashes].iter().all(|h| *h == '#');
                if closes {
                    out.lit(&chars[i..=i + hashes]);
                    out.code.push('"');
                    state = LexState::Code;
                    i += hashes + 1;
                } else {
                    out.lit([&c]);
                    i += 1;
                }
            }
            LexState::Code => {
                if c == '/' && next == Some('/') {
                    break;
                }
                if c == '/' && next == Some('*') {
                    state = LexState::Block(1);
                    i += 2;
                } else if c == '"' {
                    out.display.push('"');
                    out.code.push('"');
                    out.masked.push('"');
                    state = LexState::Str;
                    i += 1;
                } else if let Some(hashes) = (c == 'r').then(|| raw_open(&chars, i)).flatten() {
                    out.lit(&chars[i..i + hashes + 2]);
                    out.code.push('"');
                    state = LexState::Raw(hashes);
                    i += hashes + 2;
                } else if let Some(end) = (c == '\'').then(|| char_end(&chars, i)).flatten() {
                    out.lit(&chars[i..end]);
                    out.code.push_str("''");
                    i = end;
                } else {
                    out.both(c);
                    i += 1;
                }
            }
        }
    }
    (out, state)
}

#[cfg(test)]
#[path = "rustlex_tests.rs"]
mod tests;
