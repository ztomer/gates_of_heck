//! Shell masking for the early-exit-pipe scan: what is CODE stays, everything else is inert.
//!
//! Not `unreaped::mask::mask_shell`, and the difference is the defect this check exists for: that
//! mask blanks a double-quoted span whole, and `v="$(producer | head -1)"` -- the commonest shape
//! of the class -- is code inside double quotes. Here a `$( )` or a backtick inside double quotes
//! is code again, to its matching close, and strings may span lines.
//!
//! The output has exactly one `char` per input `char`, newlines where the input has them, so a
//! finding's offset is the author's offset. Quote characters are KEPT and their content becomes
//! `_`, so a quoted argument is still one word (an awk program, a sed script) and the raw text
//! can be read back at the same span. Comments and heredoc bodies become spaces. A backslash
//! escape becomes `__`, except a backslash-newline, which is kept: it continues the line.

/// Filler for a quoted span's content: a word character, never a shell operator.
const FILL: char = '_';

#[derive(Clone, Copy)]
enum Frame {
    /// Code; `close` is what ends it (`None` at top level), `depth` its open parentheses.
    Code { close: Option<char>, depth: usize },
    /// `"..."`.
    Double,
    /// `'...'`, or `$'...'` when `ansi` (backslash escapes count).
    Single { ansi: bool },
}

/// A pending heredoc's tag. The terminator is matched trimmed, which covers `<<-`'s tabs.
struct Pending(String);

fn is_word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// The heredoc opened at `i` (`<<` already seen there, not `<<<`): its tag, read from the raw text.
fn heredoc_tag(text: &[char], i: usize) -> Option<String> {
    let mut j = i + 2;
    if text.get(j) == Some(&'-') {
        j += 1;
    }
    while matches!(text.get(j), Some(' ' | '\t')) {
        j += 1;
    }
    let quote = match text.get(j) {
        Some(&q @ ('\'' | '"')) => {
            j += 1;
            Some(q)
        }
        Some('\\') => {
            j += 1;
            None
        }
        _ => None,
    };
    let start = j;
    if !text.get(j).is_some_and(|c| c.is_alphabetic() || *c == '_') {
        return None;
    }
    while text.get(j).copied().is_some_and(is_word) {
        j += 1;
    }
    if let Some(q) = quote {
        if text.get(j) != Some(&q) {
            return None;
        }
    }
    Some(text[start..j].iter().collect())
}

/// Blank heredoc bodies starting at line offset `i` (just past a newline); returns the offset
/// after the last terminator consumed.
fn skip_bodies(
    text: &[char],
    mut i: usize,
    pending: &mut Vec<Pending>,
    out: &mut Vec<char>,
) -> usize {
    for Pending(tag) in pending.drain(..) {
        while i < text.len() {
            let end = text[i..]
                .iter()
                .position(|c| *c == '\n')
                .map_or(text.len(), |p| i + p);
            let line: String = text[i..end].iter().collect();
            out.extend(std::iter::repeat_n(' ', end - i));
            if end < text.len() {
                out.push('\n');
            }
            i = end + 1;
            if line.trim() == tag {
                break;
            }
        }
    }
    i.min(text.len())
}

/// One pass over the script, frame by frame. Each method handles the character at `i` in its
/// frame and returns where the next one starts.
struct Masker {
    text: Vec<char>,
    out: Vec<char>,
    stack: Vec<Frame>,
    pending: Vec<Pending>,
}

const TOP: Frame = Frame::Code {
    close: None,
    depth: 0,
};

impl Masker {
    /// A backslash escape inside a string: both characters inert, a newline kept.
    fn escaped(&mut self, i: usize) -> usize {
        let next = if self.text[i + 1] == '\n' { '\n' } else { FILL };
        self.out.extend([FILL, next]);
        i + 2
    }

    fn content(&mut self, ch: char) {
        self.out.push(if ch == '\n' { '\n' } else { FILL });
    }

    fn single(&mut self, i: usize, ansi: bool) -> usize {
        let ch = self.text[i];
        if ansi && ch == '\\' && i + 1 < self.text.len() {
            return self.escaped(i);
        }
        if ch == '\'' {
            self.stack.pop();
            self.out.push('\'');
        } else {
            self.content(ch);
        }
        i + 1
    }

    fn double(&mut self, i: usize) -> usize {
        let ch = self.text[i];
        if ch == '\\' && i + 1 < self.text.len() {
            return self.escaped(i);
        }
        if ch == '"' {
            self.stack.pop();
            self.out.push('"');
        } else if ch == '$' && self.text.get(i + 1) == Some(&'(') {
            self.stack.push(Frame::Code {
                close: Some(')'),
                depth: 0,
            });
            self.out.extend(['$', '(']);
            return i + 2;
        } else if ch == '`' {
            self.stack.push(Frame::Code {
                close: Some('`'),
                depth: 0,
            });
            self.out.push('`');
        } else {
            self.content(ch);
        }
        i + 1
    }

    fn depth_by(&mut self, up: bool) {
        if let Some(Frame::Code { depth, .. }) = self.stack.last_mut() {
            *depth = if up {
                *depth + 1
            } else {
                depth.saturating_sub(1)
            };
        }
    }

    fn code(&mut self, i: usize, close: Option<char>, depth: usize) -> usize {
        let (text, n) = (&self.text, self.text.len());
        let ch = text[i];
        if ch == '\\' && i + 1 < n {
            let pair = if text[i + 1] == '\n' {
                ['\\', '\n']
            } else {
                [FILL, FILL]
            };
            self.out.extend(pair);
            return i + 2;
        }
        let at_word_start = self.out.last().is_none_or(|c| " \t\n;|&()".contains(*c));
        if ch == '#' && at_word_start {
            let end = text[i..]
                .iter()
                .position(|c| *c == '\n')
                .map_or(n, |p| i + p);
            self.out.extend(std::iter::repeat_n(' ', end - i));
            return end;
        }
        let heredoc = ch == '<'
            && text.get(i + 1) == Some(&'<')
            && text.get(i + 2) != Some(&'<')
            && (i == 0 || text[i - 1] != '<');
        if heredoc {
            if let Some(tag) = heredoc_tag(text, i) {
                self.pending.push(Pending(tag));
            }
            self.out.extend(['<', '<']);
            return i + 2;
        }
        match ch {
            '\'' => {
                let ansi = self.out.last() == Some(&'$');
                self.stack.push(Frame::Single { ansi });
            }
            '"' => self.stack.push(Frame::Double),
            '(' => self.depth_by(true),
            ')' if close == Some(')') && depth == 0 => {
                self.stack.pop();
            }
            ')' => self.depth_by(false),
            '`' if close == Some('`') => {
                self.stack.pop();
            }
            '\n' if !self.pending.is_empty() => {
                self.out.push('\n');
                return skip_bodies(&self.text, i + 1, &mut self.pending, &mut self.out);
            }
            _ => {}
        }
        self.out.push(ch);
        i + 1
    }
}

/// The masked text: code kept, strings filled, comments and heredoc bodies blanked.
#[must_use]
pub fn mask(src: &str) -> Vec<char> {
    let text: Vec<char> = src.chars().collect();
    let mut m = Masker {
        out: Vec::with_capacity(text.len()),
        text,
        stack: vec![TOP],
        pending: Vec::new(),
    };
    let mut i = 0usize;
    while i < m.text.len() {
        i = match m.stack.last().copied().unwrap_or(TOP) {
            Frame::Single { ansi } => m.single(i, ansi),
            Frame::Double => m.double(i),
            Frame::Code { close, depth } => m.code(i, close, depth),
        };
    }
    m.out
}

#[cfg(test)]
mod tests {
    use super::mask;

    fn m(s: &str) -> String {
        mask(s).into_iter().collect()
    }

    #[test]
    fn one_char_out_per_char_in() {
        for s in [
            "a 'b\nc' \"d $(e | f) g\" # h\ncat <<EOF\nx\nEOF\ny \\\n z é",
            "x=$'a\\'b' | c",
        ] {
            assert_eq!(mask(s).len(), s.chars().count(), "{s:?}");
            assert_eq!(m(s).matches('\n').count(), s.matches('\n').count(), "{s:?}");
        }
    }

    #[test]
    fn code_in_a_double_quoted_substitution_survives() {
        assert_eq!(m("v=\"a $(b | c) d\""), "v=\"__$(b | c)__\"");
    }

    #[test]
    fn strings_comments_heredocs_are_inert() {
        assert_eq!(m("echo 'a|b' # c|d"), "echo '___'      ");
        assert_eq!(m("cat <<'E'\na|b\nE\nx"), "cat <<'_'\n   \n \nx");
        assert_eq!(m("a \\| b"), "a __ b");
        assert_eq!(m("a${x#y}|b"), "a${x#y}|b");
    }
}
