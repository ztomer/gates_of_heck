//! A shell script read as commands of words, through its quoting (`shell.rs`'s lexer).
//!
//! Single and double quotes, a backslash escape and a backslash-newline continuation; `${...}` kept
//! whole for substitution; `$(`, `;`, `&`, `|`, `(`, `)`, a backtick and a newline end a command; a
//! `#` starting a word is a comment; a redirection's target is no argument; a heredoc's body is
//! skipped. Enough to tell a command from text, not a shell.

/// One shell word: its text with the quoting removed (`$` kept for substitution), the line it
/// starts on, and whether it began inside quotes (a quoted `NAME=` is not an assignment).
#[derive(Debug, Clone)]
pub struct Word {
    pub text: String,
    pub line: usize,
    pub quoted: bool,
}

struct Lexer {
    c: Vec<char>,
    i: usize,
    line: usize,
    out: Vec<Vec<Word>>,
    word: Option<Word>,
    heredocs: Vec<(String, bool)>,
}

impl Lexer {
    fn at(&self, k: usize) -> Option<char> {
        self.c.get(self.i + k).copied()
    }

    /// The word being read, started at this line if none is.
    fn word(&mut self, quoted: bool) -> &mut Word {
        let line = self.line;
        self.word.get_or_insert_with(|| Word {
            text: String::new(),
            line,
            quoted,
        })
    }

    fn flush(&mut self) {
        if let Some(w) = self.word.take() {
            if let Some(cmd) = self.out.last_mut() {
                cmd.push(w);
            }
        }
    }

    /// End the command being read.
    fn end(&mut self) {
        self.flush();
        self.out.push(Vec::new());
    }

    fn skip_blanks(&mut self) {
        while matches!(self.at(0), Some(' ' | '\t')) {
            self.i += 1;
        }
    }

    /// A `'...'` or `"..."` run at `self.i`, appended to the word.
    fn quoted(&mut self, q: char) {
        self.word(true);
        self.i += 1;
        let mut text = String::new();
        while let Some(ch) = self.at(0).filter(|ch| *ch != q) {
            if ch == '\n' {
                self.line += 1;
            }
            let next = self.at(1).filter(|_| q == '"' && ch == '\\');
            if let Some(next) = next {
                if !matches!(next, '$' | '`' | '"' | '\\' | '\n') {
                    text.push('\\');
                }
                if next == '\n' {
                    self.line += 1;
                } else {
                    text.push(next);
                }
                self.i += 2;
            } else {
                text.push(ch);
                self.i += 1;
            }
        }
        self.word(true).text.push_str(&text);
        self.i += 1;
    }

    /// `<<WORD`, `<<-WORD`, `<<'WORD'`: its body is skipped at the end of the line.
    fn heredoc(&mut self) {
        self.flush();
        self.i += 2;
        let dash = self.at(0) == Some('-');
        self.i += usize::from(dash);
        self.skip_blanks();
        let mut delim = String::new();
        while let Some(x) = self.at(0) {
            if x.is_whitespace() || ";&|<>()".contains(x) {
                break;
            }
            if !matches!(x, '\'' | '"' | '\\') {
                delim.push(x);
            }
            self.i += 1;
        }
        self.heredocs.push((delim, dash));
    }

    /// A redirection: its target is no argument, and nor is a bare fd number before it.
    fn redirect(&mut self) {
        if self.word.as_ref().is_some_and(|w| {
            !w.quoted && !w.text.is_empty() && w.text.bytes().all(|b| b.is_ascii_digit())
        }) {
            self.word = None;
        }
        self.flush();
        self.i += 1;
        while matches!(self.at(0), Some('>' | '&' | '|')) {
            self.i += 1;
        }
        self.skip_blanks();
        while self
            .at(0)
            .is_some_and(|x| !x.is_whitespace() && !";&|()".contains(x))
        {
            self.i += 1;
        }
    }

    /// A newline: the command ends, and any heredoc opened on the line has its body skipped.
    fn newline(&mut self) {
        self.end();
        self.line += 1;
        self.i += 1;
        for (delim, dash) in std::mem::take(&mut self.heredocs) {
            while self.i < self.c.len() {
                let end = self.c[self.i..]
                    .iter()
                    .position(|x| *x == '\n')
                    .map_or(self.c.len(), |p| self.i + p);
                let body: String = self.c[self.i..end].iter().collect();
                let body = if dash {
                    body.trim_start_matches('\t')
                } else {
                    &body
                };
                let done = body == delim;
                self.i = end + 1;
                self.line += 1;
                if done {
                    break;
                }
            }
        }
    }

    fn step(&mut self) {
        let ch = self.c[self.i];
        match ch {
            '\\' if self.at(1) == Some('\n') => {
                self.line += 1;
                self.i += 2;
            }
            '\\' => {
                let next = self.at(1);
                self.word(false).text.extend(next);
                self.i += 2;
            }
            '\'' | '"' => self.quoted(ch),
            '$' if self.at(1) == Some('{') => {
                let mut text = String::new();
                while let Some(x) = self.at(0).filter(|x| *x != '}' && *x != '\n') {
                    text.push(x);
                    self.i += 1;
                }
                text.extend(self.at(0).filter(|x| *x == '}'));
                self.word(false).text.push_str(&text);
                self.i += 1;
            }
            '$' if self.at(1) == Some('(') => {
                self.end();
                self.i += 2;
            }
            '#' if self.word.is_none() => {
                while self.at(0).is_some_and(|x| x != '\n') {
                    self.i += 1;
                }
            }
            '<' if self.at(1) == Some('<') && self.at(2) != Some('<') => self.heredoc(),
            '<' | '>' => self.redirect(),
            '\n' => self.newline(),
            ';' | '&' | '|' | '(' | ')' | '`' => {
                self.end();
                self.i += 1;
            }
            ' ' | '\t' | '\r' => {
                self.flush();
                self.i += 1;
            }
            _ => {
                self.word(false).text.push(ch);
                self.i += 1;
            }
        }
    }
}

/// The commands of `text`: runs of words between `;`, `&`, `|`, `(`, `)`, a backtick and a newline.
#[must_use]
pub fn commands(text: &str) -> Vec<Vec<Word>> {
    let mut lx = Lexer {
        c: text.chars().collect(),
        i: 0,
        line: 1,
        out: vec![Vec::new()],
        word: None,
        heredocs: Vec::new(),
    };
    while lx.i < lx.c.len() {
        lx.step();
    }
    lx.flush();
    lx.out.retain(|cmd| !cmd.is_empty());
    lx.out
}
