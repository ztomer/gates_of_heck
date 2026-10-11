//! What an allowlist entry is matched on: the CALL, not the physical line (BACKLOG 4.8).
//!
//! Two allowlists keyed an exemption on one line's whitespace-stripped text, so a formatter
//! reflowing a call across lines read as a new finding AND a stale entry: necrohand re-keyed one
//! `qa_minigames.py` kill three times in an afternoon, and `substdin`'s key was the line the call
//! STARTS on, which for a reflowed call is `subprocess.run(` alone -- the same for every call. The
//! key is now the call: `substdin` takes the call expression from its parser, and `killname`, a
//! line scanner, takes the logical statement the kill sits in (`statements`). Both compare with
//! whitespace and trailing commas removed (`norm`), so a reflow is invisible and a deleted or
//! changed call is not.
//!
//! The estate's entries hold the line the gates used to print, which for a reflowed call is a
//! FRAGMENT of it. A fragment still matches the call that contains it, so no repo goes red the day
//! this lands, but `fit` says it was a fragment: a fragment is a guess about which call it meant,
//! and a guess says so rather than passing silently.

use std::collections::BTreeMap;

/// How an entry matched a kill.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Fit {
    /// The entry is the line or the whole call.
    Exact,
    /// The entry is part of the call: right today, and to be re-keyed to the call.
    Fragment,
}

/// The comparable form: no whitespace, and no comma before a closing bracket (ruff's magic
/// trailing comma is the one token a reflow adds).
#[must_use]
pub fn norm(text: &str) -> String {
    let squeezed: String = text.split_whitespace().collect();
    let mut out = String::with_capacity(squeezed.len());
    for c in squeezed.chars() {
        if matches!(c, ')' | ']' | '}') && out.ends_with(',') {
            out.pop();
        }
        out.push(c);
    }
    out
}

/// `{line: the statement spanning it}` for Python; `None` when the file does not lex.
///
/// Each statement is its source text from its first token to its last with comments cut (columns
/// are characters, as the lexer counts them), so every line of one call maps to the same text.
#[must_use]
pub fn statements(text: &str) -> Option<BTreeMap<usize, String>> {
    let lexed = crate::pylex::lex(text)?;
    let lines: Vec<Vec<char>> = text.split('\n').map(|l| l.chars().collect()).collect();
    let comment: BTreeMap<usize, usize> = lexed.comments.iter().copied().collect();
    let mut out = BTreeMap::new();
    for stmt in &lexed.stmts {
        let mut toks = lexed.toks[stmt.toks.clone()]
            .iter()
            .filter(|t| t.kind != crate::pylex::Kind::Newline);
        let Some(first) = toks.next() else {
            continue;
        };
        let (from, to) = (first.start, toks.next_back().map_or(first.end, |t| t.end));
        let mut call = String::new();
        for row in from.0..=to.0 {
            let Some(line) = lines.get(row - 1) else {
                break;
            };
            let start = if row == from.0 { from.1 } else { 0 };
            let end = if row == to.0 { to.1 } else { line.len() };
            let end = end.min(comment.get(&row).copied().unwrap_or(usize::MAX));
            call.extend(line.iter().take(end).skip(start));
            call.push(' ');
        }
        for row in from.0..=to.0 {
            out.entry(row).or_insert_with(|| call.clone());
        }
    }
    Some(out)
}

/// Whether `entry` (an allowlist line) excuses a kill on `line` inside `call`.
#[must_use]
pub fn fit(entry: &str, line: &str, call: Option<&str>) -> Option<Fit> {
    let e = norm(entry);
    if e.is_empty() {
        return None;
    }
    // The whole call where there is one, else the line: a line of a longer call is a part of it.
    let whole = call.map_or_else(|| norm(line), norm);
    if e == whole {
        return Some(Fit::Exact);
    }
    (e == norm(line) || whole.contains(&e)).then_some(Fit::Fragment)
}

#[cfg(test)]
mod tests {
    use super::*;

    const CALL: &str = "subprocess.run([\"pk\", \"-f\", \"X\"], check=False)";

    #[test]
    fn a_trailing_comma_and_whitespace_do_not_change_the_key() {
        assert_eq!(
            norm("f(\n  [\n    \"a\",\n  ],\n  b,\n)"),
            norm("f([\"a\"], b)")
        );
    }

    #[test]
    fn a_comma_between_arguments_stays() {
        assert_eq!(norm("f(a, b)"), "f(a,b)");
    }

    #[test]
    fn every_line_of_a_reflowed_call_maps_to_the_whole_call() {
        let text = "import subprocess\nsubprocess.run(\n    [\"pk\",\n     \"-f\"],\n)\nx = 1\n";
        let map = statements(text).unwrap_or_else(|| panic!("lexes"));
        assert_eq!(map[&2], map[&3]);
        assert_eq!(map[&3], map[&5]);
        assert_eq!(norm(&map[&4]), "subprocess.run([\"pk\",\"-f\"])");
        assert_ne!(map[&2], map[&6]);
    }

    #[test]
    fn the_whole_call_is_exact_and_a_line_or_part_of_it_is_a_fragment() {
        assert_eq!(fit(CALL, "\"pk\",", Some(CALL)), Some(Fit::Exact));
        assert_eq!(fit("\"pk\",", "\"pk\",", Some(CALL)), Some(Fit::Fragment));
        assert_eq!(fit("pk -x A", "pk -x A", None), Some(Fit::Exact));
        assert_eq!(
            fit("[\"pk\",\"-f\",\"X\"]", "\"pk\",", Some(CALL)),
            Some(Fit::Fragment)
        );
        assert_eq!(fit("[\"pk\",\"-f\",\"Y\"]", "\"pk\",", Some(CALL)), None);
        assert_eq!(fit("", "\"pk\",", Some(CALL)), None);
    }
}
