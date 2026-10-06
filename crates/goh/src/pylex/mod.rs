//! A small Python LEXER, and the statement facts the ported checkers read
//! from it -- where the Python tier used `ast.parse` + `tokenize` (Phase N1).
//!
//! It is not a parser. It knows strings (every prefix; f- and t-string
//! replacement fields nested per PEP 701), comments, brackets, operators,
//! logical lines and the tokenizer's dedent rule, and it splits logical lines
//! into STATEMENTS (`;`, and the colon that ends a compound header). From
//! that it answers what the checkers asked `ast` for: which statements are
//! only a `str` literal, which of those are docstrings, which `def`s exist,
//! and which module-level assignments hold a list or dict literal of strings.
//! A file the tokenizer refuses (unterminated string, unclosed bracket, bad
//! dedent) is `None`. A file that tokenizes but does not PARSE is the one
//! case it cannot see; each caller states what the reference does there.

/// `(line, column)`, 1-based line, column in characters -- `tokenize`'s.
pub type Pos = (usize, usize);

mod facts;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Kind {
    /// A string literal; `fmt` is an f- or t-string, `bytes` a bytes literal.
    Str {
        fmt: bool,
        bytes: bool,
    },
    Name,
    Op,
    Open,
    Close,
    /// The end of a logical line.
    Newline,
}

#[derive(Clone, Debug)]
pub struct Tok {
    pub kind: Kind,
    pub text: String,
    pub start: Pos,
    pub end: Pos,
    /// Bracket depth before an opener, after a closer, else current.
    pub depth: usize,
    /// Indentation level of the logical line the token is on.
    pub level: usize,
}

impl Tok {
    pub(crate) fn plain_str(&self) -> bool {
        self.kind
            == (Kind::Str {
                fmt: false,
                bytes: false,
            })
    }
}

/// One statement: a token range, its indentation level, and whether it is
/// the body that follows a compound header on the same line.
#[derive(Clone, Debug)]
pub struct Stmt {
    pub toks: std::ops::Range<usize>,
    pub level: usize,
    pub after_header: bool,
    /// The first statement of a module, `def` or `class` body.
    pub doc_slot: bool,
}

/// A lexed file.
pub struct Lexed {
    pub toks: Vec<Tok>,
    pub comments: Vec<Pos>,
    /// Every non-f string literal, nested ones inside f-string fields
    /// included: `tokenize`'s STRING tokens, as `(start, end)`.
    pub strings: Vec<(Pos, Pos)>,
    pub stmts: Vec<Stmt>,
}

const COMPOUND: [&str; 14] = [
    "if", "elif", "else", "for", "while", "try", "except", "finally", "with", "def", "class",
    "async", "match", "case",
];
const OPS: [&str; 24] = [
    "**=", "//=", ">>=", "<<=", "...", "!=", "%=", "&=", "**", "*=", "+=", "-=", "->", "//", "/=",
    ":=", "<<", "<=", "==", ">=", ">>", "@=", "^=", "|=",
];

/// `(is f/t-string, is bytes, is raw)` for a valid string prefix.
fn prefix(word: &str) -> Option<(bool, bool, bool)> {
    let w = word.to_ascii_lowercase();
    let raw = w.contains('r');
    match w.as_str() {
        "" | "r" | "u" => Some((false, false, raw)),
        "b" | "br" | "rb" => Some((false, true, raw)),
        "f" | "fr" | "rf" | "t" | "tr" | "rt" => Some((true, false, raw)),
        _ => None,
    }
}

struct Lexer<'a> {
    c: &'a [char],
    i: usize,
    line: usize,
    col: usize,
    comments: Vec<Pos>,
    strings: Vec<(Pos, Pos)>,
}

impl Lexer<'_> {
    fn peek(&self, k: usize) -> Option<char> {
        self.c.get(self.i + k).copied()
    }

    const fn pos(&self) -> Pos {
        (self.line, self.col)
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

    fn word(&mut self) -> String {
        let mut w = String::new();
        while let Some(c) = self.peek(0).filter(|c| c.is_alphanumeric() || *c == '_') {
            w.push(c);
            self.bump();
        }
        w
    }

    fn comment(&mut self) {
        self.comments.push(self.pos());
        while self.peek(0).is_some_and(|c| c != '\n') {
            self.bump();
        }
    }

    /// A string at its opening quote, its prefix already read from `start`.
    fn quoted(&mut self, word: &str, start: Pos) -> Option<(bool, bool)> {
        let (fmt, bytes, raw) = prefix(word)?;
        let q = self.peek(0)?;
        let triple = self.peek(1) == Some(q) && self.peek(2) == Some(q);
        let quote: String = std::iter::repeat_n(q, if triple { 3 } else { 1 }).collect();
        for _ in 0..quote.len() {
            self.bump();
        }
        loop {
            if self.starts(&quote) {
                for _ in 0..quote.len() {
                    self.bump();
                }
                break;
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
                self.bump();
                if self.peek(0) == Some('{') {
                    self.bump();
                } else {
                    self.field()?;
                }
                continue;
            }
            self.bump();
        }
        if !fmt {
            self.strings.push((start, self.pos()));
        }
        Some((fmt, bytes))
    }

    /// A replacement field after its `{`, through its `}`.
    fn field(&mut self) -> Option<()> {
        let mut depth = 0usize;
        loop {
            let ch = self.peek(0)?;
            match ch {
                '}' if depth == 0 => {
                    self.bump();
                    return Some(());
                }
                ':' if depth == 0 => {
                    self.bump();
                    loop {
                        let ch = self.peek(0)?;
                        self.bump();
                        if ch == '}' {
                            return Some(());
                        }
                        if ch == '{' {
                            self.field()?;
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
                '#' => self.comment(),
                '"' | '\'' => {
                    let start = self.pos();
                    self.quoted("", start)?;
                }
                c if c.is_alphabetic() || c == '_' => {
                    let start = self.pos();
                    let word = self.word();
                    if matches!(self.peek(0), Some('"' | '\'')) && prefix(&word).is_some() {
                        self.quoted(&word, start)?;
                    }
                }
                _ => {
                    self.bump();
                }
            }
        }
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

struct Scan {
    toks: Vec<Tok>,
    depth: usize,
    indents: Vec<usize>,
}

impl Scan {
    fn push(&mut self, kind: Kind, text: String, start: Pos, end: Pos) {
        let level = self.indents.len() - 1;
        self.toks.push(Tok {
            kind,
            text,
            start,
            end,
            depth: self.depth,
            level,
        });
    }

    /// The dedent rule; `None` is the tokenizer's `IndentationError`.
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

    /// One token at the lexer's position; `Some(true)` when a logical line ended.
    fn token(&mut self, lx: &mut Lexer<'_>) -> Option<bool> {
        let ch = lx.peek(0)?;
        let start = lx.pos();
        match ch {
            '\n' => {
                lx.bump();
                if self.depth == 0 {
                    self.push(Kind::Newline, String::new(), start, start);
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
            '#' => lx.comment(),
            '"' | '\'' => {
                let (fmt, bytes) = lx.quoted("", start)?;
                self.push(Kind::Str { fmt, bytes }, String::new(), start, lx.pos());
            }
            '(' | '[' | '{' => {
                lx.bump();
                self.push(Kind::Open, ch.to_string(), start, lx.pos());
                self.depth += 1;
            }
            ')' | ']' | '}' => {
                lx.bump();
                self.depth = self.depth.checked_sub(1)?;
                self.push(Kind::Close, ch.to_string(), start, lx.pos());
            }
            c if c.is_alphabetic() || c == '_' => {
                let word = lx.word();
                if matches!(lx.peek(0), Some('"' | '\'')) && prefix(&word).is_some() {
                    let (fmt, bytes) = lx.quoted(&word, start)?;
                    self.push(Kind::Str { fmt, bytes }, String::new(), start, lx.pos());
                } else {
                    self.push(Kind::Name, word, start, lx.pos());
                }
            }
            c if c.is_whitespace() => {
                lx.bump();
            }
            _ => {
                let op = OPS
                    .iter()
                    .find(|op| lx.starts(op))
                    .map_or_else(|| ch.to_string(), |op| (*op).to_owned());
                for _ in 0..op.chars().count() {
                    lx.bump();
                }
                self.push(Kind::Op, op, start, lx.pos());
            }
        }
        Some(false)
    }
}

fn statements(toks: &[Tok]) -> Vec<Stmt> {
    let mut out = Vec::new();
    let mut begin = 0usize;
    let mut after_header = false;
    let mut doc_slot = true; // the module's first statement
    let mut i = 0usize;
    let close = |out: &mut Vec<Stmt>, from: usize, to: usize, after: bool, doc: &mut bool| {
        if from < to {
            out.push(Stmt {
                toks: from..to,
                level: toks[from].level,
                after_header: after,
                doc_slot: *doc,
            });
            *doc = false;
        }
    };
    while i < toks.len() {
        let t = &toks[i];
        let first = toks.get(begin).filter(|_| begin < i);
        if t.depth == 0 && (t.kind == Kind::Newline || (t.kind == Kind::Op && t.text == ";")) {
            close(&mut out, begin, i, after_header, &mut doc_slot);
            if t.kind == Kind::Newline {
                after_header = false;
            }
            begin = i + 1;
        } else if t.depth == 0
            && t.kind == Kind::Op
            && t.text == ":"
            && first.is_some_and(|f| f.kind == Kind::Name && COMPOUND.contains(&f.text.as_str()))
            && !toks[begin..i]
                .iter()
                .any(|s| s.depth == 0 && s.kind == Kind::Op && s.text == ":")
        {
            let opener = toks[begin..i]
                .iter()
                .find(|s| s.kind == Kind::Name && s.text != "async");
            let defines = opener.is_some_and(|o| o.text == "def" || o.text == "class");
            close(&mut out, begin, i + 1, after_header, &mut doc_slot);
            doc_slot = defines;
            after_header = true;
            begin = i + 1;
        }
        i += 1;
    }
    close(&mut out, begin, toks.len(), after_header, &mut doc_slot);
    out
}

/// Lex `text`; `None` where `tokenize` raises.
#[must_use]
pub fn lex(text: &str) -> Option<Lexed> {
    let chars: Vec<char> = text.chars().collect();
    let mut lx = Lexer {
        c: &chars,
        i: 0,
        line: 1,
        col: 0,
        comments: Vec::new(),
        strings: Vec::new(),
    };
    let mut scan = Scan {
        toks: Vec::new(),
        depth: 0,
        indents: vec![0],
    };
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
    if scan.depth != 0 {
        return None;
    }
    let stmts = statements(&scan.toks);
    Some(Lexed {
        toks: scan.toks,
        comments: lx.comments,
        strings: lx.strings,
        stmts,
    })
}
