//! A shell source as a flat list of STATEMENTS, each with what ended it.
//!
//! Not a shell parser: enough structure to say "the statement after this one, in the same block".
//! It reads the masked text (`earlypipe::lex::mask_top`: comments and heredoc bodies blank,
//! strings filled), so a `;` or newline inside a quote, a comment or a heredoc ends nothing.
//!
//! A statement is an and-or list: `a | b && c` is ONE statement, `joined`. It ends at a newline,
//! `;`, `&`, a case-arm end (`;;` `;&` `;;&`), a `(` or a `)`, or EOF. A `|`, `||` or `&&` at a line end continues onto the next line, as does `\`-newline.
//! `$( )`, `<( )`, `a=( )`, extglob `@( )` and backticks stay inside the word that opens them.
//!
//! No nesting is tracked, and none is needed: the judgment (`dead.rs`) reads only what ENDED a
//! statement and the first word of the next one. A case pattern's `)` and a subshell's `)` both
//! end the block an `exec` sits in, so they need not be told apart -- a `case`/`(` context stack
//! stood here first and no row of the table could tell it was gone, so it was deleted.

use crate::earlypipe::lex::mask_top;
use crate::earlypipe::scan::unquote;

/// What ended a statement.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum End {
    /// A newline.
    Line,
    /// `;`.
    Semi,
    /// `&`: the statement ran in the background.
    Amp,
    /// `;;`, `;&` or `;;&`: the end of a case arm.
    CaseArm,
    /// A `(` opening a subshell (or `((`, a function's `()`, a case pattern's optional `(`).
    Open,
    /// A `)` closing one, or a case pattern.
    Close,
    /// End of file.
    Eof,
}

/// One statement: its words (quotes removed), where it starts, and what ended it.
#[derive(Debug, Clone)]
pub struct Stmt {
    /// Each word's shell value, quotes removed.
    pub words: Vec<String>,
    /// Each word's char offset.
    pub at: Vec<usize>,
    /// Char offset of the first word, or of the terminator when there are none.
    pub start: usize,
    /// Char offset just past the last word (`start` when there are none).
    pub stop: usize,
    /// `|`, `||`, `&&` or `|&` joined more than one command into it.
    pub joined: bool,
    /// What ended it.
    pub end: End,
}

struct Builder<'a> {
    m: &'a [char],
    top: &'a [bool],
    raw: &'a [char],
    words: Vec<String>,
    at: Vec<usize>,
    start: Option<usize>,
    stop: usize,
    joined: bool,
    out: Vec<Stmt>,
}

impl Builder<'_> {
    fn end(&mut self, end: End, at: usize) {
        let start = self.start.take().unwrap_or(at);
        self.out.push(Stmt {
            stop: if self.words.is_empty() {
                start
            } else {
                self.stop
            },
            words: std::mem::take(&mut self.words),
            at: std::mem::take(&mut self.at),
            start,
            joined: std::mem::take(&mut self.joined),
            end,
        });
    }

    /// Past blanks and newlines (after `|`, `||`, `&&`, the list continues on the next line).
    fn past_blanks(&self, mut i: usize) -> usize {
        while i < self.m.len() && self.top[i] && self.m[i].is_whitespace() || self.split_line(i) {
            i += 1;
        }
        i
    }

    /// A `\` that continues the line.
    fn split_line(&self, i: usize) -> bool {
        i < self.m.len() && self.m[i] == '\\' && self.m.get(i + 1) == Some(&'\n')
    }

    /// The offset just past the `)` balancing the `(` at `i`.
    const fn balanced(&self, mut i: usize) -> usize {
        let mut depth = 0usize;
        while i < self.m.len() {
            if self.top[i] {
                match self.m[i] {
                    '(' => depth += 1,
                    ')' if depth <= 1 => return i + 1,
                    ')' => depth -= 1,
                    _ => {}
                }
            }
            i += 1;
        }
        i
    }

    /// Does the top-level char at `i` (inside a word that began at `start`) end that word?
    fn ends_word(&self, i: usize, start: usize) -> bool {
        let prev = (i > start).then(|| self.m[i - 1]);
        match self.m[i] {
            ' ' | '\t' | '\r' | '\n' | ';' | ')' => true,
            '\\' => self.split_line(i),
            '|' => prev != Some('>'),
            '&' => !matches!(prev, Some('<' | '>')) && self.m.get(i + 1) != Some(&'>'),
            '(' => !matches!(
                prev,
                Some('$' | '<' | '>' | '=' | '@' | '?' | '*' | '+' | '!')
            ),
            _ => false,
        }
    }

    /// Read the word starting at `i`; returns the offset after it.
    fn word(&mut self, mut i: usize) -> usize {
        let start = i;
        while i < self.m.len() {
            if !self.top[i] {
                i += 1;
                continue;
            }
            if self.ends_word(i, start) {
                break;
            }
            i = match self.m[i] {
                '(' => self.balanced(i),
                '`' => self.m[i + 1..]
                    .iter()
                    .zip(&self.top[i + 1..])
                    .position(|(c, t)| *c == '`' && *t)
                    .map_or(self.m.len(), |p| i + p + 2),
                _ => i + 1,
            };
        }
        if i == start {
            // Termination, not a rule: `step` hands every operator to its own arm, so no char
            // reaches here unread -- and if one ever did, the scan must move, not spin.
            return i + 1;
        }
        let word = unquote(&self.raw[start..i]);
        self.start.get_or_insert(start);
        self.stop = i;
        self.at.push(start);
        self.words.push(word);
        i
    }

    fn open_close(&mut self, i: usize, end: End) -> usize {
        self.end(end, i);
        i + 1
    }

    fn semi(&mut self, i: usize) -> usize {
        let arm = [";;&", ";;", ";&"]
            .into_iter()
            .find(|op| self.m[i..].iter().take(op.len()).copied().eq(op.chars()));
        let Some(op) = arm else {
            self.end(End::Semi, i);
            return i + 1;
        };
        self.end(End::CaseArm, i);
        i + op.len()
    }

    fn join(&mut self, i: usize, width: usize) -> usize {
        self.joined = true;
        self.past_blanks(i + width)
    }

    /// One token at `i`. Every token starts at a TOP char: a quote's opener is read at the top
    /// frame, and everything inside it is read by `word`.
    fn step(&mut self, i: usize) -> usize {
        let next = self.m.get(i + 1).copied();
        match self.m[i] {
            '\\' if next == Some('\n') => i + 2,
            ' ' | '\t' | '\r' => i + 1,
            '\n' => {
                self.end(End::Line, i);
                i + 1
            }
            ';' => self.semi(i),
            '&' if next == Some('&') => self.join(i, 2),
            '&' if next != Some('>') => {
                self.end(End::Amp, i);
                i + 1
            }
            '|' if matches!(next, Some('|' | '&')) => self.join(i, 2),
            '|' => self.join(i, 1),
            '(' => self.open_close(i, End::Open),
            ')' => self.open_close(i, End::Close),
            _ => self.word(i),
        }
    }
}

/// Every statement of `src`, in order.
#[must_use]
pub fn statements(src: &str) -> Vec<Stmt> {
    let (m, top) = mask_top(src);
    let raw: Vec<char> = src.chars().collect();
    let mut b = Builder {
        m: &m,
        top: &top,
        raw: &raw,
        words: Vec::new(),
        at: Vec::new(),
        start: None,
        stop: 0,
        joined: false,
        out: Vec::new(),
    };
    let mut i = 0usize;
    while i < m.len() {
        i = b.step(i);
    }
    b.end(End::Eof, m.len());
    b.out
}
