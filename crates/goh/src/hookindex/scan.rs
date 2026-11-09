//! Where a shell source EXPANDS a carried hook variable.
//!
//! `$VAR` or `${VAR...}` in code, inside double quotes, or in an unquoted heredoc body -- every
//! place the shell substitutes it. A mention is not a read: comments, single quotes, quoted heredoc bodies, an escaped `\$`, and a
//! bare name (`unset GOH_HOOK_INDEX_FILE`, an assignment) are skipped.
//!
//! Not `earlypipe::lex::mask`: that mask fills a double-quoted span with filler, and
//! `"${GOH_HOOK_INDEX_FILE:-...}"` -- the shape this check exists for -- is an expansion inside
//! double quotes.

/// The variables `goh_hook_unbind` carries out of a commit hook (`gates/_git_env.sh`).
pub const CARRIED: [&str; 2] = ["GOH_HOOK_INDEX_FILE", "GOH_HOOK_GIT_DIR"];

/// One expansion of a carried variable.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Read {
    /// 1-based line of the `$`.
    pub line: usize,
    /// The variable expanded.
    pub var: &'static str,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum State {
    Code,
    Double,
    Single,
    Comment,
}

/// A heredoc opened on the current line, read once that line ends.
struct Heredoc {
    tag: String,
    /// `<<'EOF'`, `<<"EOF"` or `<<\EOF`: the body is literal.
    quoted: bool,
}

const fn is_name_start(c: char) -> bool {
    c.is_ascii_alphabetic() || c == '_'
}

const fn is_name(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// A `#` here starts a comment: at the start, or after a blank or an operator.
fn starts_word(prev: Option<char>) -> bool {
    prev.is_none_or(|p| p.is_whitespace() || ";|&(){}".contains(p))
}

/// The carried variable a `$` at `i` expands, if any.
fn expanded(text: &[char], i: usize) -> Option<&'static str> {
    let mut j = i + 1;
    if text.get(j) == Some(&'{') {
        j += 1;
        if matches!(text.get(j), Some('#' | '!')) {
            j += 1;
        }
    }
    if !text.get(j).copied().is_some_and(is_name_start) {
        return None;
    }
    let start = j;
    while text.get(j).copied().is_some_and(is_name) {
        j += 1;
    }
    let name: String = text[start..j].iter().collect();
    CARRIED.iter().copied().find(|v| *v == name)
}

/// The heredoc whose `<<` is at `i` (not `<<<`): its tag, and where the operator ends.
fn heredoc_at(text: &[char], i: usize) -> Option<(Heredoc, usize)> {
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
            Some('\\')
        }
        _ => None,
    };
    let start = j;
    while text.get(j).copied().is_some_and(is_name) {
        j += 1;
    }
    if j == start {
        return None;
    }
    let tag: String = text[start..j].iter().collect();
    if matches!(quote, Some('\'' | '"')) && text.get(j) == quote.as_ref() {
        j += 1;
    }
    Some((
        Heredoc {
            tag,
            quoted: quote.is_some(),
        },
        j,
    ))
}

/// `src` with what the shell does not run as code blanked to spaces.
///
/// Newlines are kept, so a char's index and line survive: comments always; with `literals`, also single-quoted spans, quoted
/// heredoc bodies and escaped characters (`\$X` is not an expansion). Without `literals` a
/// command inside quotes stays visible: `bash -c '... git diff --cached'` still runs it.
#[must_use]
pub fn mask(src: &str, literals: bool) -> Vec<char> {
    let text: Vec<char> = src.chars().collect();
    let mut out = text.clone();
    let mut state = State::Code;
    let mut pending: Vec<Heredoc> = Vec::new();
    let mut i = 0usize;
    while i < text.len() {
        let c = text[i];
        if c == '\n' {
            if state == State::Comment {
                state = State::Code;
            }
            i += 1;
            if state == State::Code && !pending.is_empty() {
                i = bodies(&text, &mut out, i, &mut pending, literals);
            }
            continue;
        }
        match state {
            State::Comment => out[i] = ' ',
            State::Single => {
                if literals {
                    out[i] = ' ';
                }
                if c == '\'' {
                    state = State::Code;
                }
            }
            State::Code | State::Double => {
                if c == '\\' {
                    if literals {
                        out[i] = ' ';
                    }
                    i += 1; // the next char is literal; a newline after it still counts above
                    if text.get(i) == Some(&'\n') {
                        continue;
                    }
                    if literals && i < text.len() {
                        out[i] = ' ';
                    }
                } else if c == '"' {
                    state = if state == State::Code {
                        State::Double
                    } else {
                        State::Code
                    };
                } else if state == State::Code {
                    if c == '\'' {
                        state = State::Single;
                        if literals {
                            out[i] = ' ';
                        }
                    } else if c == '#'
                        && starts_word(i.checked_sub(1).map(|p| text[p]))
                        && !(i >= 2 && text[i - 1] == '{' && text[i - 2] == '$')
                    {
                        // `${#X}` is a length, not a comment.
                        state = State::Comment;
                        out[i] = ' ';
                    } else if c == '<'
                        && text.get(i + 1) == Some(&'<')
                        && text.get(i + 2) != Some(&'<')
                    {
                        if let Some((doc, end)) = heredoc_at(&text, i) {
                            pending.push(doc);
                            i = end;
                            continue;
                        }
                    }
                }
            }
        }
        i += 1;
    }
    out
}

/// Every expansion of a carried variable in `src`, in order.
#[must_use]
pub fn reads(src: &str) -> Vec<Read> {
    let text = mask(src, true);
    let mut out = Vec::new();
    let mut line = 1usize;
    for (i, c) in text.iter().enumerate() {
        if *c == '\n' {
            line += 1;
        } else if *c == '$' {
            if let Some(var) = expanded(&text, i) {
                out.push(Read { line, var });
            }
        }
    }
    out
}

/// Mask the pending heredoc bodies starting at `i` (the line after their operators): with
/// `literals`, a quoted body is blanked whole and an unquoted one's escaped characters. Returns
/// where code resumes.
fn bodies(
    text: &[char],
    out: &mut [char],
    mut i: usize,
    pending: &mut Vec<Heredoc>,
    literals: bool,
) -> usize {
    for doc in pending.drain(..) {
        while i < text.len() {
            let end = text[i..]
                .iter()
                .position(|c| *c == '\n')
                .map_or(text.len(), |n| i + n);
            let body_line: String = text[i..end].iter().collect();
            let done = body_line.trim() == doc.tag;
            if !done && literals {
                let mut j = i;
                while j < end {
                    if doc.quoted {
                        out[j] = ' ';
                    } else if text[j] == '\\' {
                        out[j] = ' ';
                        if j + 1 < end {
                            out[j + 1] = ' ';
                        }
                        j += 1;
                    }
                    j += 1;
                }
            }
            i = (end + 1).min(text.len());
            if done {
                break;
            }
        }
    }
    i
}

/// The source defines `goh_bind_hook_index` -- the one place the carried index is read.
#[must_use]
pub fn defines_binder(src: &str) -> bool {
    src.lines().any(|l| {
        let l = l.trim_start();
        let (keyword, l) = l
            .strip_prefix("function ")
            .map_or((false, l), |r| (true, r.trim_start()));
        let Some(rest) = l.strip_prefix("goh_bind_hook_index") else {
            return false;
        };
        let rest = rest.trim_start();
        rest.starts_with("()") || (keyword && (rest.is_empty() || rest.starts_with('{')))
    })
}
