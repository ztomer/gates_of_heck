//! Python code lines with comments and bare-string statements removed --
//! what `ast.parse` + `tokenize` give `check_no_kill_by_name.py`.
//!
//! A small Python lexer, not a parser: it finds `#` comments (cut at their
//! column, as the reference cuts at the COMMENT token's column) and every
//! expression statement that is only a `str` literal (a docstring, or any
//! bare string -- `ast.walk` sees them all), whose whole line range is
//! dropped. A file the reference's tokenizer refuses -- an unterminated
//! string, an unclosed bracket, an inconsistent dedent -- returns `None`
//! here too, and the caller falls back to the plain line rules. What it
//! cannot see is a file that tokenizes but does not PARSE; that is the one
//! place the two tiers can differ, and the estate sweep measures it.

use std::collections::BTreeMap;

#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    Str { plain_text: bool },
    Open,
    Close,
    Semi,
    Colon,
    Name,
    Other,
}

struct Tok {
    kind: Kind,
    line: usize,
    end_line: usize,
    depth: usize,
    word: String,
}

struct Lexer<'a> {
    c: &'a [char],
    i: usize,
    line: usize,
    col: usize,
}

const COMPOUND: [&str; 14] = [
    "if", "elif", "else", "for", "while", "try", "except", "finally", "with", "def", "class",
    "async", "match", "case",
];

fn is_prefix(word: &str) -> Option<(bool, bool)> {
    // (is f/t-string, is bytes)
    let w = word.to_ascii_lowercase();
    match w.as_str() {
        "" | "r" | "u" => Some((false, false)),
        "b" | "br" | "rb" => Some((false, true)),
        "f" | "fr" | "rf" | "t" | "tr" | "rt" => Some((true, false)),
        _ => None,
    }
}

impl Lexer<'_> {
    fn peek(&self, k: usize) -> Option<char> {
        self.c.get(self.i + k).copied()
    }

    fn bump(&mut self) -> Option<char> {
        let ch = self.c.get(self.i).copied()?;
        self.i += 1;
        if ch == '\n' {
            self.line += 1;
            self.col = 0;
        } else {
            self.col += 1;
        }
        Some(ch)
    }

    fn starts(&self, s: &str) -> bool {
        s.chars()
            .enumerate()
            .all(|(k, ch)| self.peek(k) == Some(ch))
    }

    /// The body of a string after its opening quote. `None`: unterminated.
    fn string_body(
        &mut self,
        quote: &str,
        fmt: bool,
        raw: bool,
        comments: &mut Vec<(usize, usize)>,
    ) -> Option<()> {
        let triple = quote.len() == 3;
        loop {
            if self.starts(quote) {
                for _ in 0..quote.len() {
                    self.bump();
                }
                return Some(());
            }
            let ch = self.peek(0)?;
            if ch == '\\' {
                self.bump();
                // In an f-string a backslash does not protect a brace (`rf"\{{"` is a
                // backslash and an escaped brace); `\N{NAME}` is one escape, braces and all.
                if fmt && !raw && self.peek(0) == Some('N') && self.peek(1) == Some('{') {
                    while self.peek(0).is_some_and(|c| c != '}' && c != '\n') {
                        self.bump();
                    }
                }
                if !(fmt && matches!(self.peek(0), Some('{' | '}'))) {
                    self.bump()?;
                }
                continue;
            }
            if ch == '\n' && !triple {
                return None;
            }
            if fmt && ch == '{' {
                if self.peek(1) == Some('{') {
                    self.bump();
                    self.bump();
                    continue;
                }
                self.bump();
                self.field(comments)?;
                continue;
            }
            self.bump();
        }
    }

    /// A replacement field after its `{`, up to and including its `}`.
    fn field(&mut self, comments: &mut Vec<(usize, usize)>) -> Option<()> {
        let mut depth = 0usize;
        loop {
            let ch = self.peek(0)?;
            match ch {
                '}' if depth == 0 => {
                    self.bump();
                    return Some(());
                }
                ':' if depth == 0 => {
                    // The format spec: literal text holding nested fields.
                    self.bump();
                    loop {
                        let ch = self.peek(0)?;
                        if ch == '}' {
                            self.bump();
                            return Some(());
                        }
                        self.bump();
                        if ch == '{' {
                            self.field(comments)?;
                        }
                    }
                }
                '(' | '[' | '{' => {
                    depth += 1;
                    self.bump();
                }
                ')' | ']' | '}' => {
                    depth = depth.checked_sub(1)?;
                    self.bump();
                }
                '#' => {
                    comments.push((self.line, self.col));
                    while self.peek(0).is_some_and(|c| c != '\n') {
                        self.bump();
                    }
                }
                '"' | '\'' => {
                    self.quoted("", comments)?;
                }
                c if c.is_alphabetic() || c == '_' => {
                    let word = self.word();
                    if matches!(self.peek(0), Some('"' | '\'')) && is_prefix(&word).is_some() {
                        self.quoted(&word, comments)?;
                    }
                }
                _ => {
                    self.bump();
                }
            }
        }
    }

    fn word(&mut self) -> String {
        let mut w = String::new();
        while let Some(c) = self.peek(0).filter(|c| c.is_alphanumeric() || *c == '_') {
            w.push(c);
            self.bump();
        }
        w
    }

    /// A string whose prefix was already read; at its opening quote.
    fn quoted(&mut self, prefix: &str, comments: &mut Vec<(usize, usize)>) -> Option<(bool, bool)> {
        let (fmt, bytes) = is_prefix(prefix)?;
        let q = self.peek(0)?;
        let quote: String = if self.peek(1) == Some(q) && self.peek(2) == Some(q) {
            std::iter::repeat_n(q, 3).collect()
        } else {
            q.to_string()
        };
        for _ in 0..quote.len() {
            self.bump();
        }
        self.string_body(
            &quote,
            fmt,
            prefix.to_ascii_lowercase().contains('r'),
            comments,
        )?;
        Some((fmt, bytes))
    }
}

fn indent_width(line: &[char]) -> Option<usize> {
    let mut col = 0usize;
    for &ch in line {
        match ch {
            ' ' => col += 1,
            '\t' => col = (col / 8 + 1) * 8,
            '\u{c}' => col = 0,
            _ => return Some(col),
        }
    }
    None
}

/// A comment's `(line, column)`, the column in characters.
type Comment = (usize, usize);

/// The tokenizer's running state over one file.
struct Scan {
    toks: Vec<Tok>,
    comments: Vec<Comment>,
    depth: usize,
    indents: Vec<usize>,
}

impl Scan {
    fn push(&mut self, kind: Kind, line: usize, end_line: usize, word: String) {
        self.toks.push(Tok {
            kind,
            line,
            end_line,
            depth: self.depth,
            word,
        });
    }

    /// The tokenizer's dedent rule: a logical line's indentation must match an
    /// enclosing level. `None` is its `IndentationError`.
    fn indentation(&mut self, rest: &[char]) -> Option<()> {
        let Some(width) = indent_width(rest) else {
            return Some(());
        };
        let first = rest
            .iter()
            .find(|c| !matches!(c, ' ' | '\t' | '\u{c}' | '\r'));
        if first.is_none_or(|c| *c == '#') {
            return Some(());
        }
        let top = self.indents.last().copied().unwrap_or(0);
        if width > top {
            self.indents.push(width);
            return Some(());
        }
        while self.indents.last().is_some_and(|t| *t > width) {
            self.indents.pop();
        }
        (self.indents.last().copied() == Some(width)).then_some(())
    }

    /// One token at the lexer's position. Returns whether a logical line ended.
    fn token(&mut self, lx: &mut Lexer<'_>) -> Option<bool> {
        let ch = lx.peek(0)?;
        let line = lx.line;
        match ch {
            '\n' => {
                lx.bump();
                if self.depth == 0 {
                    self.push(Kind::Semi, line, line, String::new());
                    return Some(true);
                }
            }
            '\\' if lx.peek(1) == Some('\n')
                || (lx.peek(1) == Some('\r') && lx.peek(2) == Some('\n')) =>
            {
                while lx.peek(0) != Some('\n') {
                    lx.bump();
                }
                lx.bump();
            }
            '#' => {
                self.comments.push((lx.line, lx.col));
                while lx.peek(0).is_some_and(|c| c != '\n') {
                    lx.bump();
                }
            }
            '"' | '\'' => {
                let (fmt, bytes) = lx.quoted("", &mut self.comments)?;
                self.push(
                    Kind::Str {
                        plain_text: !fmt && !bytes,
                    },
                    line,
                    lx.line,
                    String::new(),
                );
            }
            '(' | '[' | '{' => {
                lx.bump();
                self.push(Kind::Open, line, line, String::new());
                self.depth += 1;
            }
            ')' | ']' | '}' => {
                lx.bump();
                self.depth = self.depth.checked_sub(1)?;
                self.push(Kind::Close, line, line, String::new());
            }
            ';' => {
                lx.bump();
                self.push(Kind::Semi, line, line, String::new());
            }
            ':' => {
                lx.bump();
                let kind = if lx.peek(0) == Some('=') {
                    Kind::Other
                } else {
                    Kind::Colon
                };
                self.push(kind, line, line, String::new());
            }
            c if c.is_alphabetic() || c == '_' => {
                let word = lx.word();
                if matches!(lx.peek(0), Some('"' | '\'')) && is_prefix(&word).is_some() {
                    let (fmt, bytes) = lx.quoted(&word, &mut self.comments)?;
                    self.push(
                        Kind::Str {
                            plain_text: !fmt && !bytes,
                        },
                        line,
                        lx.line,
                        String::new(),
                    );
                } else {
                    self.push(Kind::Name, line, line, word);
                }
            }
            c if c.is_whitespace() => {
                lx.bump();
            }
            _ => {
                lx.bump();
                self.push(Kind::Other, line, line, String::new());
            }
        }
        Some(false)
    }
}

/// `(tokens, comment positions)`, or `None` where `tokenize` raises.
fn lex(text: &str) -> Option<Scan> {
    let chars: Vec<char> = text.chars().collect();
    let mut lx = Lexer {
        c: &chars,
        i: 0,
        line: 1,
        col: 0,
    };
    let mut scan = Scan {
        toks: Vec::new(),
        comments: Vec::new(),
        depth: 0,
        indents: vec![0],
    };
    // A newline inside brackets continues the logical line: only one at depth 0 starts a line
    // whose indentation the tokenizer judges.
    let mut at_line_start = true;
    while lx.i < chars.len() {
        if at_line_start {
            let rest: Vec<char> = chars[lx.i..]
                .iter()
                .take_while(|c| **c != '\n')
                .copied()
                .collect();
            scan.indentation(&rest)?;
        }
        at_line_start = scan.token(&mut lx)?;
    }
    (scan.depth == 0).then_some(scan)
}

/// The line ranges of every statement that is only a `str` literal.
fn prose_ranges(toks: &[Tok]) -> Vec<(usize, usize)> {
    let mut out = Vec::new();
    let mut stmt: Vec<&Tok> = Vec::new();
    let mut flush = |stmt: &mut Vec<&Tok>| {
        let strings = stmt
            .iter()
            .filter(|t| matches!(t.kind, Kind::Str { .. }))
            .count();
        let pure = stmt.iter().all(|t| {
            matches!(
                t.kind,
                Kind::Open | Kind::Close | Kind::Str { plain_text: true }
            )
        });
        if strings > 0 && pure {
            if let (Some(a), Some(b)) = (stmt.first(), stmt.last()) {
                out.push((a.line, b.end_line));
            }
        }
        stmt.clear();
    };
    for t in toks {
        if t.depth == 0 && t.kind == Kind::Semi {
            flush(&mut stmt);
            continue;
        }
        if t.depth == 0
            && t.kind == Kind::Colon
            && stmt
                .first()
                .is_some_and(|f| f.kind == Kind::Name && COMPOUND.contains(&f.word.as_str()))
            && !stmt.iter().any(|s| s.depth == 0 && s.kind == Kind::Colon)
        {
            stmt.clear();
            continue;
        }
        stmt.push(t);
    }
    flush(&mut stmt);
    out
}

/// `{lineno: code}`, comments cut and bare-string statements removed; `None`
/// where the reference's tokenizer would refuse the file.
#[must_use]
pub fn python_code_lines(text: &str) -> Option<BTreeMap<usize, String>> {
    let Scan { toks, comments, .. } = lex(text)?;
    let mut lines: Vec<String> = text.split('\n').map(str::to_owned).collect();
    for (row, col) in comments {
        if let Some(line) = lines.get_mut(row - 1) {
            *line = line.chars().take(col).collect();
        }
    }
    let prose = prose_ranges(&toks);
    Some(
        lines
            .into_iter()
            .enumerate()
            .map(|(i, l)| (i + 1, l))
            .filter(|(n, _)| !prose.iter().any(|&(a, b)| a <= *n && *n <= b))
            .collect(),
    )
}
