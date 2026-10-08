//! Find every `producer | early-exit-consumer` in a shell source.
//!
//! Not gated on the file's own pipefail line: a library runs under its CALLER's pipefail
//! (`media_server`'s install-lib.sh, sourced by install.sh, carried a racy pipe past a scan that
//! only read files saying "pipefail"), and so does any script started with `SHELLOPTS` exported.

use super::consumer::classify;
use super::lex::mask;
use super::subst;
use super::Finding;

/// A character that ends a simple command: the next one starts after it.
fn ends_command(m: &[char], i: usize) -> bool {
    match m[i] {
        '|' | ';' | ')' | '`' | '\n' => !(m[i] == '\n' && i > 0 && m[i - 1] == '\\'),
        // `&` ends a command unless it is part of a redirection (`>&2`, `2>&1`, `&>`).
        '&' => !(i > 0 && matches!(m[i - 1], '<' | '>')) && m.get(i + 1) != Some(&'>'),
        _ => false,
    }
}

/// The words of the simple command starting at `i`: `(start, end)` spans, and where it ends.
fn words_from(m: &[char], mut i: usize) -> (Vec<(usize, usize)>, usize) {
    let mut words = Vec::new();
    loop {
        while i < m.len()
            && (m[i] == ' '
                || m[i] == '\t'
                || (m[i] == '\\' && m.get(i + 1) == Some(&'\n'))
                || (m[i] == '\n' && i > 0 && m[i - 1] == '\\'))
        {
            i += 1;
        }
        if i >= m.len() || ends_command(m, i) || m[i] == '(' {
            return (words, i);
        }
        let start = i;
        while i < m.len() && !matches!(m[i], ' ' | '\t' | '(') && !ends_command(m, i) {
            i += 1;
        }
        words.push((start, i));
    }
}

/// A word's shell value, quotes removed (enough for flags and literal programs).
fn unquote(raw: &[char]) -> String {
    let mut out = String::new();
    let mut quote: Option<char> = None;
    let mut i = 0usize;
    while i < raw.len() {
        let c = raw[i];
        match (quote, c) {
            (None, '\'' | '"') => quote = Some(c),
            (Some(q), _) if c == q => quote = None,
            (Some('\''), _) => out.push(c),
            (_, '\\') if i + 1 < raw.len() => {
                i += 1;
                out.push(raw[i]);
            }
            _ => out.push(c),
        }
        i += 1;
    }
    out
}

/// After the consumer's command ends at `i`: does the rest of the pipeline end in `|| true` or
/// `|| :`? Then its status is discarded, and there is no verdict for the race to corrupt.
fn status_discarded(m: &[char], raw: &[char], mut i: usize) -> bool {
    loop {
        if i >= m.len() || m[i] != '|' {
            return false;
        }
        if m.get(i + 1) == Some(&'|') {
            let (words, end) = words_from(m, past_blank_lines(m, i + 2));
            let only = words.len() == 1 && {
                let w = unquote(&raw[words[0].0..words[0].1]);
                w == "true" || w == ":"
            };
            return only && (end >= m.len() || m[end] != '|');
        }
        // A further `| cmd` of the same pipeline: step over it.
        let (_, end) = words_from(
            m,
            past_blank_lines(m, i + 1 + usize::from(m.get(i + 1) == Some(&'&'))),
        );
        i = end;
    }
}

/// Past the blanks and newlines after a `|` or `||`: the shell reads the next command from the
/// next line, so `producer |` at a line end continues the pipeline.
fn past_blank_lines(m: &[char], mut i: usize) -> usize {
    while i < m.len() && m[i].is_whitespace() {
        i += 1;
    }
    i
}

/// Is the `|` at `i` a pipe (not `||`, not `>|`)? Returns where the next command starts.
fn pipe_at(m: &[char], i: usize) -> Option<usize> {
    if m[i] != '|' || m.get(i + 1) == Some(&'|') || (i > 0 && matches!(m[i - 1], '|' | '>')) {
        return None;
    }
    Some(past_blank_lines(
        m,
        i + 1 + usize::from(m.get(i + 1) == Some(&'&')),
    ))
}

/// `foo|head)` in a `case` arm: the `)` closes nothing opened on this line.
fn case_pattern(m: &[char], pipe: usize, word_end: usize) -> bool {
    if m.get(word_end) != Some(&')') {
        return false;
    }
    let line_start = m[..pipe]
        .iter()
        .rposition(|c| *c == '\n')
        .map_or(0, |p| p + 1);
    let opened = m[line_start..pipe].iter().filter(|c| **c == '(').count();
    let closed = m[line_start..pipe].iter().filter(|c| **c == ')').count();
    opened <= closed
}

/// Every early-exit consumer reading a pipe whose status the script reads.
#[must_use]
pub fn scan_text(src: &str) -> Vec<Finding> {
    let m = mask(src);
    let raw: Vec<char> = src.chars().collect();
    let around = subst::enclosing(&m);
    let mut found = Vec::new();
    for (i, open) in around.iter().enumerate() {
        let Some(next) = pipe_at(&m, i) else { continue };
        let (words, end) = words_from(&m, next);
        let mut cmd_at = 0usize;
        while let Some(&(s, e)) = words.get(cmd_at) {
            let w = unquote(&raw[s..e]);
            let assignment = w.split_once('=').is_some_and(|(k, _)| {
                !k.is_empty() && k.chars().all(|c| c.is_alphanumeric() || c == '_')
            });
            if !(assignment || w == "env" || w == "command") {
                break;
            }
            cmd_at += 1;
        }
        let Some(&(cs, ce)) = words.get(cmd_at) else {
            continue;
        };
        let cmd = unquote(&raw[cs..ce]);
        let base = cmd.rsplit('/').next().unwrap_or(&cmd);
        let args: Vec<String> = words[cmd_at + 1..]
            .iter()
            .map(|&(s, e)| unquote(&raw[s..e]))
            .collect();
        let Some(kind) = classify(base, &args) else {
            continue;
        };
        let last = words.last().map_or(ce, |w| w.1);
        let unread = open.is_some_and(|open| !subst::status_read(&m, &raw, open));
        if unread || case_pattern(&m, i, last) || status_discarded(&m, &raw, end) {
            continue;
        }
        let line = 1 + m[..cs].iter().filter(|c| **c == '\n').count();
        let text: String = raw[cs..last].iter().collect();
        found.push(Finding {
            line,
            kind,
            text: text.split_whitespace().collect::<Vec<_>>().join(" "),
        });
    }
    found
}
