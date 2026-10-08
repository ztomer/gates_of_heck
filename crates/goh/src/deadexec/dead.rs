//! Which statements an `exec` makes unreachable.
//!
//! An `exec` that names a command replaces the shell; the statement after it, in the same block,
//! never runs. An `exec` with only redirections (`exec >log 2>&1`, `exec 3<&-`, `exec {fd}>f`)
//! replaces nothing and is not one. Nor is an `exec` whose outcome is still branched on or run
//! elsewhere: one joined by `||` / `&&` / `|` (`exec x || fallback`), one sent to the background,
//! and one whose statement a `)` ends (`( exec x )`: only the subshell is replaced).
//!
//! What ends the block, so that nothing after the `exec` is dead: `;;` / `;&` / `;;&`, `esac`,
//! `fi`, `else`, `elif`, `done`, `}`, a `)`, `then` / `do`, and EOF. Blank lines and comments are
//! not statements. One statement is allowed through: a bare `exit` or `exit N` -- the house
//! parse-guard ends `exec ... "$@"` / `exit` / `} # parse-guard`, and an `exit` there is a stated
//! intent, not dead logic. Anything after THAT is still dead.

use super::parse::{statements, End, Stmt};
use super::Finding;

/// The first word of a statement that closes the block the `exec` sits in.
const BLOCK_END: [&str; 8] = ["fi", "else", "elif", "done", "esac", "}", "then", "do"];

/// Redirection operators, longest first (a word starting with one, after an optional fd number
/// or `{name}`, is a redirection).
const REDIRECT: [&str; 12] = [
    "&>>", "&>", ">>", "<<<", "<<-", "<<", "<>", ">|", "<&", ">&", "<", ">",
];

/// Is `word` a redirection? `Some(true)` when its target is the NEXT word (`>` `file`).
fn redirection(word: &str) -> Option<bool> {
    let rest = if let Some(named) = word.strip_prefix('{') {
        let close = named.find('}')?;
        let name = &named[..close];
        if name.is_empty() || !name.chars().all(|c| c.is_alphanumeric() || c == '_') {
            return None;
        }
        &named[close + 1..]
    } else {
        word.trim_start_matches(|c: char| c.is_ascii_digit())
    };
    let op = REDIRECT.iter().find(|op| rest.starts_with(**op))?;
    Some(rest.len() == op.len())
}

/// The index of the `exec` word, when `s` runs `exec` as its command (after `then`, `do`,
/// `else`, `{`).
fn exec_word(s: &Stmt) -> Option<usize> {
    let lead = ["then", "do", "else", "{"];
    let at = s.words.iter().position(|w| !lead.contains(&w.as_str()))?;
    (s.words[at] == "exec").then_some(at)
}

/// Does `exec ARGS` name a command (so it replaces the shell), not only redirections?
fn names_a_command(args: &[String]) -> bool {
    let mut i = 0usize;
    let mut options = true;
    while i < args.len() {
        let w = args[i].as_str();
        if let Some(takes_next) = redirection(w) {
            i += 1 + usize::from(takes_next);
        } else if options && w == "--" {
            options = false;
            i += 1;
        } else if options && w.len() > 1 && w.starts_with('-') {
            // `-a NAME` takes an argument; `-c`, `-l` do not.
            i += 1 + usize::from(w.contains('a'));
        } else {
            return true;
        }
    }
    false
}

/// Does `s` run an `exec` that replaces the shell and is the last thing its block can reach?
fn replaces_shell(s: &Stmt) -> bool {
    !s.joined
        && matches!(s.end, End::Line | End::Semi | End::Eof)
        && exec_word(s).is_some_and(|at| names_a_command(&s.words[at + 1..]))
}

/// The first statement after `stmts[0]` that no path reaches, if any.
fn first_dead(after: &[Stmt]) -> Option<&Stmt> {
    for t in after {
        let Some(first) = t.words.first() else {
            match t.end {
                End::Line | End::Semi => continue, // a blank line, a comment, a stray `;`
                End::Open => return Some(t),       // `( ... )`, `(( ... ))`: code
                _ => return None,
            }
        };
        if BLOCK_END.contains(&first.as_str()) {
            return None;
        }
        let bare_exit = first == "exit" && t.words.len() <= 2 && !t.joined;
        if !bare_exit {
            return Some(t);
        }
        if !matches!(t.end, End::Line | End::Semi) {
            return None;
        }
    }
    None
}

/// `chars[from..to]` as written, whitespace collapsed (a bare `(` for a subshell's opener).
fn quoted(chars: &[char], from: usize, to: usize) -> String {
    let raw: String = chars[from..to].iter().collect();
    let raw = if raw.trim().is_empty() {
        "(".to_owned()
    } else {
        raw
    };
    raw.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Every statement made unreachable by an `exec` before it: the FIRST in each block.
#[must_use]
pub fn scan_text(src: &str) -> Vec<Finding> {
    let chars: Vec<char> = src.chars().collect();
    let line_of = |at: usize| 1 + chars[..at].iter().filter(|c| **c == '\n').count();
    let stmts = statements(src);
    let mut found = Vec::new();
    for (k, s) in stmts.iter().enumerate() {
        if !replaces_shell(s) {
            continue;
        }
        let Some(t) = first_dead(&stmts[k + 1..]) else {
            continue;
        };
        let exec_at = exec_word(s).map_or(s.start, |w| s.at[w]);
        found.push(Finding {
            line: line_of(t.start),
            text: quoted(&chars, t.start, t.stop),
            exec_line: line_of(exec_at),
            exec: quoted(&chars, exec_at, s.stop),
        });
    }
    found
}
