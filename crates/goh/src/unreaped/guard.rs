//! WHO PROTECTS THIS SPAWN. Port of the retired `checks/_spawn_guard_attr.py` and
//! `checks/_spawn_guard_attr_rust.py`.
//!
//! A guard protects the VALUE it wraps, never the function it was written in:
//! a guard on one binding laundered a second, raw one (`routines`), and a
//! `Self(` construction passed whenever any `impl Drop` existed in the file,
//! even one whose body did nothing (`monitor`). Every branch below names the
//! binding, or resolves `Self` to the type whose `Drop` was read.

use std::collections::BTreeSet;

use super::lex::{Lex, EXTERNAL_GUARDS, EXTERNAL_REAPERS};

/// The verdict a protection rule hands back: provably guarded, or a tag.
pub enum Attribution {
    Guarded,
    Tag(&'static str, String),
}

/// The line where the statement containing the spawn begins: its `let`,
/// walking back to the nearest `;` or `}`.
#[must_use]
pub fn stmt_head(lex: &Lex, lines: &[&str], at: usize, sig: usize) -> usize {
    let mut j = at;
    loop {
        if lex.let_bind.is_match(lines[j]) {
            return j;
        }
        if j < at && lex.semi_brace.is_match(lines[j]) {
            break;
        }
        if j == sig || j == 0 {
            break;
        }
        j -= 1;
    }
    at
}

/// The identifier the spawn's result is bound to, from the statement's `let`.
#[must_use]
pub fn binding(lex: &Lex, lines: &[&str], at: usize, sig: usize) -> Option<String> {
    lex.let_bind
        .captures(lines[stmt_head(lex, lines, at, sig)])
        .and_then(|c| c.get(1).map(|m| m.as_str().to_owned()))
}

/// The type an enclosing `impl` block's `Self` names, or None.
#[must_use]
pub fn self_type(lex: &Lex, lines: &[&str], sig: usize) -> Option<String> {
    for j in (0..sig).rev() {
        if let Some(c) = lex.impl_type.captures(lines[j]) {
            return c.get(1).map(|m| m.as_str().to_owned());
        }
        if lex.func.is_match(lines[j]) || lex.mod_or_close.is_match(lines[j]) {
            return None;
        }
    }
    None
}

/// Types this text DEFINES, by any declaration the compiler would accept.
#[must_use]
pub fn known_types(lex: &Lex, masked: &str) -> BTreeSet<String> {
    lex.known_types
        .captures_iter(masked)
        .filter_map(|c| c.get(1).map(|m| m.as_str().to_owned()))
        .collect()
}

fn brace_delta(line: &str) -> isize {
    let opens = line.matches('{').count();
    let closes = line.matches('}').count();
    isize::try_from(opens).unwrap_or(isize::MAX) - isize::try_from(closes).unwrap_or(isize::MAX)
}

/// Blank everything OUTSIDE every `#[cfg(test)]` region, preserving lines. A
/// file with no marker is returned unchanged (a `tests/` file is all test).
#[must_use]
pub fn rust_region(masked: &str) -> String {
    let lines: Vec<&str> = masked.split('\n').collect();
    let marks: Vec<usize> = lines
        .iter()
        .enumerate()
        .filter(|(_, l)| l.replace(' ', "").contains("cfg(test)"))
        .map(|(n, _)| n)
        .collect();
    if marks.is_empty() {
        return masked.to_owned();
    }
    let mut inside = vec![false; lines.len()];
    for at in marks {
        let start = (at..lines.len())
            .find(|&j| lines[j].contains('{'))
            .unwrap_or(lines.len() - 1);
        let mut depth = 0isize;
        for j in start..lines.len() {
            inside[j] = true;
            depth += brace_delta(lines[j]);
            if depth <= 0 {
                break;
            }
        }
    }
    lines
        .iter()
        .zip(&inside)
        .map(|(line, &keep)| {
            if keep {
                (*line).to_owned()
            } else {
                " ".repeat(line.chars().count())
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// Does a construction of one of `owners` in `text` carry THIS binding into
/// it? Multi-line (`Server { child, .. }` spans four), bounded to one
/// construction by `[^;{}]`.
#[must_use]
pub fn construction_carries<'a>(
    lex: &Lex,
    text: &str,
    owners: impl IntoIterator<Item = &'a str>,
    binding: Option<&str>,
) -> bool {
    let Some(binding) = binding else {
        return false;
    };
    if text.is_empty() {
        return false;
    }
    owners.into_iter().any(|name| {
        let pat = format!(
            r"\b{}\s*(?:::|\s*[\{{\(])(?:[^;{{}}]|\n){{0,240}}?\b{}\b",
            regex::escape(name),
            regex::escape(binding)
        );
        lex.found(&pat, text)
    })
}

/// The first type the spawn's own constructor chain names that this file
/// does not define, or None. `Command` is the builder, never a wrapper.
fn foreign_guard(
    lex: &Lex,
    lines: &[&str],
    at: usize,
    guards: &BTreeSet<String>,
    known: &BTreeSet<String>,
) -> Option<String> {
    let first = (0..at).rev().find(|&j| lex.let_bind.is_match(lines[j]))?;
    let head = lines[first..=at].join("\n");
    let name = lex
        .foreign_let
        .captures(&head)
        .and_then(|c| c.get(1).map(|m| m.as_str().to_owned()))?;
    if name == "Command"
        || guards.contains(&name)
        || known.contains(&name)
        || EXTERNAL_GUARDS.contains(&name.as_str())
    {
        return None;
    }
    Some(name)
}

/// Does a watchdog closure in this function signal THIS child's pid?
fn watchdog_signals(lex: &Lex, masked: &str, binding: &str, bodies: &[String]) -> bool {
    let b = regex::escape(binding);
    let derive =
        format!(r"\b(\w+)\s*(?::[^=;\n]+)?=\s*[^\n;]*\b{b}\s*\.\s*(?:id|get\s*\(\s*0)\s*\(");
    let derived: Vec<String> = lex.cached(&derive).map_or_else(Vec::new, |r| {
        r.captures_iter(masked)
            .filter_map(|c| c.get(1).map(|m| m.as_str().to_owned()))
            .collect()
    });
    let mentions =
        |text: &str, name: &str| lex.found(&format!(r"\b{}\b", regex::escape(name)), text);
    bodies.iter().any(|text| {
        lex.killer.is_match(text)
            && (mentions(text, binding) || derived.iter().any(|n| mentions(text, n)))
    })
}

/// Where one spawn sits, for the attribution rules.
pub struct Site<'a> {
    pub statement: &'a str,
    pub body: &'a str,
    pub lines: &'a [&'a str],
    pub head: usize,
    pub end: usize,
    pub close: usize,
    pub sig: usize,
    pub binding: Option<&'a str>,
}

/// The crate- and file-scope type sets the attribution reads.
pub struct Types<'a> {
    pub guards: &'a BTreeSet<String>,
    pub known: &'a BTreeSet<String>,
    pub drops: &'a BTreeSet<String>,
}

/// Why THIS spawn cannot leak, or None.
///
/// Strongest evidence first: a guard
/// type in the spawn's own statement, the handle moved into a guard, a
/// factory whose `Self` resolves to a guard, a reaper or watchdog acting on
/// this binding.
#[must_use]
pub fn attribute(
    lex: &Lex,
    site: &Site<'_>,
    types: &Types<'_>,
    self_type: Option<&str>,
    watchdogs: &[String],
) -> Option<Attribution> {
    let Site {
        statement,
        body,
        lines,
        head,
        end,
        close,
        sig,
        binding,
    } = *site;
    if EXTERNAL_GUARDS.iter().any(|g| statement.contains(g)) {
        return Some(Attribution::Guarded);
    }
    if types
        .guards
        .iter()
        .any(|g| lex.found(&format!(r"\b{}\b", regex::escape(g)), statement))
    {
        return Some(Attribution::Guarded);
    }
    let after = lines[(end + 1).min(lines.len())..=close.min(lines.len() - 1)].join("\n");
    if end < close
        && construction_carries(
            lex,
            &after,
            types.guards.iter().map(String::as_str),
            binding,
        )
    {
        return Some(Attribution::Guarded);
    }
    if lex.self_ctor.is_match(statement) || construction_carries(lex, body, ["Self"], binding) {
        match self_type {
            Some(t) if types.guards.contains(t) => return Some(Attribution::Guarded),
            None => return Some(Attribution::Tag(
                "unjudgeable",
                "the Child is handed to `Self`, but the enclosing block has no resolvable `impl`, \
                     so this pass cannot read the `Drop` that would reap it"
                    .to_owned(),
            )),
            Some(t) if types.drops.contains(t) => {
                return Some(Attribution::Tag(
                    "unreaped-spawn",
                    format!(
                        "wrapped in `{t}`, whose `Drop` neither kills nor waits: the type claims \
                         to clean up on unwind and does not, so a panic leaves a LIVE ORPHAN"
                    ),
                ))
            }
            Some(t) if lex.returns(lines[sig]) => {
                return Some(Attribution::Tag(
                    "handoff",
                    format!(
                    "the Child is handed to `{t}`, which has no `Drop`; the caller owes the reap"
                ),
                ))
            }
            Some(_) => {}
        }
    }
    if let Some(b) = binding {
        let eb = regex::escape(b);
        if EXTERNAL_REAPERS.iter().any(|name| {
            lex.found(
                &format!(r"\b{}\s*\([^;\n]*\b{eb}\b", regex::escape(name)),
                body,
            )
        }) {
            return Some(Attribution::Guarded);
        }
        if !watchdogs.is_empty()
            && watchdog_signals(
                lex,
                &lines[sig..=close.min(lines.len() - 1)].join("\n"),
                b,
                watchdogs,
            )
        {
            return Some(Attribution::Tag(
                "watchdog",
                "a background thread kills this child after a deadline: the leak is bounded, \
                 not prevented — a ReapOnDrop guard is the shape that prevents it"
                    .to_owned(),
            ));
        }
    }
    foreign_guard(lex, lines, head, types.guards, types.known).map(|foreign| {
        Attribution::Tag(
            "unjudgeable",
            format!(
                "the Child is wrapped in `{foreign}(…)`, a type this file does not define — its \
                 `Drop` is somewhere else, so whether it reaps cannot be judged from here"
            ),
        )
    })
}
