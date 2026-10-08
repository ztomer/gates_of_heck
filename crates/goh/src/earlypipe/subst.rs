//! Is a pipeline's STATUS read? The race corrupts only the status -- the consumer's output is
//! complete before the producer dies -- so a pipeline nothing reads the status of is not a finding.
//!
//! Inside a command substitution the status reaches the script only through an ASSIGNMENT
//! (`v=$(producer | head -1)`: under `set -e` a 141 there kills the script, and `if v=$(...)`
//! branches on it). As an ARGUMENT (`echo "$(producer | head -1)"`, `[ -n "$(...)" ]`) or the value
//! of `local`/`export`/`declare`/`readonly`/`typeset` (whose own status replaces it) it is
//! discarded. A pipeline outside any substitution is a command, and its status is read by
//! whatever runs it (`if`, `&&`, `||`, `!`, `set -e`).

/// The words before an assignment that still leave it at the start of a command.
const COMMAND_START: [&str; 9] = [
    "then", "do", "else", "if", "elif", "while", "until", "!", "time",
];

#[derive(Clone, Copy, PartialEq, Eq)]
enum Open {
    Sub,
    Tick,
    Paren,
}

/// For every `|` in the masked text, where the innermost command substitution around it opens
/// (`None` outside any). Indexed by position; only `|` positions are meaningful.
#[must_use]
pub fn enclosing(m: &[char]) -> Vec<Option<usize>> {
    let mut at = vec![None; m.len()];
    let mut stack: Vec<(usize, Open)> = Vec::new();
    let mut k = 0usize;
    while k < m.len() {
        match m[k] {
            '$' if m.get(k + 1) == Some(&'(') && m.get(k + 2) != Some(&'(') => {
                stack.push((k, Open::Sub));
                k += 1;
            }
            '(' => stack.push((k, Open::Paren)),
            ')' => {
                stack.pop();
            }
            '`' if stack.last().is_some_and(|t| t.1 == Open::Tick) => {
                stack.pop();
            }
            '`' => stack.push((k, Open::Tick)),
            '|' => at[k] = stack.iter().rev().find(|t| t.1 != Open::Paren).map(|t| t.0),
            _ => {}
        }
        k += 1;
    }
    at
}

const fn blank(c: char) -> bool {
    c == ' ' || c == '\t'
}

/// The word ending just before `end`: `(start, end)`, scanning back over non-blanks.
fn word_before(m: &[char], mut end: usize) -> Option<(usize, usize)> {
    while end > 0 && blank(m[end - 1]) {
        end -= 1;
    }
    let mut start = end;
    while start > 0 && !blank(m[start - 1]) && !"\n;&|(){}".contains(m[start - 1]) {
        start -= 1;
    }
    (start < end).then_some((start, end))
}

fn is_assignment(word: &str) -> bool {
    word.split_once('=').is_some_and(|(name, _)| {
        let name = name.strip_suffix('+').unwrap_or(name);
        let name = name.split_once('[').map_or(name, |(n, _)| n);
        name.chars()
            .next()
            .is_some_and(|c| c.is_alphabetic() || c == '_')
            && name.chars().all(|c| c.is_alphanumeric() || c == '_')
    })
}

/// Does the script read the status of the substitution opening at `open`?
#[must_use]
pub fn status_read(m: &[char], raw: &[char], open: usize) -> bool {
    // The word the substitution sits in, up to its `$(` or backtick.
    let mut start = open;
    while start > 0 && !blank(m[start - 1]) && !"\n;&|(){}".contains(m[start - 1]) {
        start -= 1;
    }
    let word: String = raw[start..open].iter().collect();
    if !is_assignment(&word) {
        return false;
    }
    // Walk back over any further assignments to what starts the command. Any other word means
    // the assignment is an argument -- of `local`/`export`/`declare`, whose own status replaces
    // it, or of a command -- and its status is discarded.
    let mut cursor = start;
    loop {
        let Some((s, e)) = word_before(m, cursor) else {
            return true;
        };
        let prev: String = raw[s..e].iter().collect();
        if COMMAND_START.contains(&prev.as_str()) {
            return true;
        }
        if !is_assignment(&prev) {
            return false;
        }
        cursor = s;
    }
}
