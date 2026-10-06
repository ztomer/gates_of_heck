//! The RUST rules: where a spawn is, which function holds it, and where its
//! reap is. Port of the retired `checks/_spawn_rust.py` and `checks/_spawn_reap.py`.
//!
//! Everything here takes ALREADY-MASKED text. The crate-scope sets are
//! derived once per scan by the caller ([`CrateFacts`]) -- deriving them per
//! file was the quadratic that cost `media_server` 23 s (2026-10-05).

use std::collections::BTreeSet;

use super::guard::{
    attribute, binding, known_types, self_type, stmt_head, Attribution, Site, Types,
};
use super::lex::Lex;

/// `(line number, tag, why)`.
pub type Verdict = (usize, &'static str, String);

fn delta(line: &str) -> isize {
    let count = |c: char| isize::try_from(line.matches(c).count()).unwrap_or(isize::MAX);
    count('{') - count('}')
}

const fn block_end(text: &str, open: usize) -> usize {
    let bytes = text.as_bytes();
    let (mut depth, mut i) = (0isize, open);
    while i < bytes.len() {
        match bytes[i] {
            b'{' => depth += 1,
            b'}' => {
                depth -= 1;
                if depth == 0 {
                    break;
                }
            }
            _ => {}
        }
        i += 1;
    }
    i
}

/// Local types whose `Drop` impl STOPS the child: `kill()` or `wait()`.
#[must_use]
pub fn guard_types(lex: &Lex, masked: &str) -> BTreeSet<String> {
    let mut found = BTreeSet::new();
    for c in lex.drop_impl.captures_iter(masked) {
        let (Some(all), Some(name)) = (c.get(0), c.get(1)) else {
            continue;
        };
        let end = block_end(masked, all.end() - 1);
        if lex
            .guard_body
            .is_match(&masked[all.end()..end.min(masked.len())])
        {
            found.insert(name.as_str().to_owned());
        }
    }
    found
}

/// Every type with an `impl Drop`, whether or not its `Drop` reaps.
#[must_use]
pub fn drop_types(lex: &Lex, masked: &str) -> BTreeSet<String> {
    lex.drop_impl
        .captures_iter(masked)
        .filter_map(|c| c.get(1).map(|m| m.as_str().to_owned()))
        .collect()
}

/// The three crate-scope sets, derived ONCE per scan.
#[derive(Default)]
pub struct CrateFacts {
    pub guards: BTreeSet<String>,
    pub drops: BTreeSet<String>,
    pub known: BTreeSet<String>,
    pub present: bool,
}

impl CrateFacts {
    #[must_use]
    pub fn derive(lex: &Lex, crate_text: &str) -> Self {
        if crate_text.is_empty() {
            return Self::default();
        }
        Self {
            guards: guard_types(lex, crate_text),
            drops: drop_types(lex, crate_text),
            known: known_types(lex, crate_text),
            present: true,
        }
    }
}

fn statement_end(lines: &[&str], start: usize, close: usize, depth0: isize) -> (usize, bool) {
    let mut depth = 0isize;
    let last = (start + 40).min(close + 1);
    let ret = regex_free_return_or_close;
    for (idx, &line) in lines.iter().enumerate().take(last).skip(start) {
        for ch in line.chars() {
            if "([{".contains(ch) {
                depth += 1;
            } else if ")]}".contains(ch) {
                depth -= 1;
            }
        }
        if depth <= depth0 {
            let tail = line.trim_end();
            if tail.ends_with(';') || tail.ends_with(',') || tail.trim().is_empty() {
                return (idx, tail.ends_with(';'));
            }
            if idx > start && ret(line) {
                return (idx, false);
            }
        }
    }
    ((start + 39).min(close), false)
}

/// `re.match(r"^\s*(return\b|})", line)`.
fn regex_free_return_or_close(line: &str) -> bool {
    let t = line.trim_start();
    t.starts_with('}')
        || (t.starts_with("return")
            && !t["return".len()..]
                .chars()
                .next()
                .is_some_and(super::lex::word))
}

fn depth_at(lines: &[&str], sig: usize, upto: usize) -> isize {
    lines[sig..upto].iter().map(|l| delta(l)).sum()
}

/// (signature line, closing-brace line) of the function CONTAINING `at`.
fn fn_bounds(lex: &Lex, lines: &[&str], at: usize) -> (usize, usize) {
    let mut depth = 0isize;
    let mut fallback: Option<usize> = None;
    for i in (0..=at).rev() {
        depth -= delta(lines[i]);
        if depth > 0 {
            continue;
        }
        let Some(sig_from) = (0..=i).rev().find(|&s| lex.func.is_match(lines[s])) else {
            break;
        };
        fallback = fallback.or(Some(sig_from));
        let mut d = 0isize;
        for (j, line) in lines.iter().enumerate().skip(sig_from) {
            d += delta(line);
            if d <= 0 {
                if sig_from <= at && at <= j {
                    return (sig_from, j);
                }
                break;
            }
        }
    }
    (fallback.unwrap_or(0), lines.len() - 1)
}

fn fn_returns(lex: &Lex, lines: &[&str], sig: usize) -> bool {
    let mut depth = 0isize;
    for line in &lines[sig..] {
        if lex.func.is_match(line) && lex.returns(line) {
            return true;
        }
        depth += delta(line);
        if depth <= 0 {
            return false;
        }
    }
    false
}

/// The source of each `thread::spawn(...)` closure inside one function.
fn watchdog_bodies(lex: &Lex, scope: &str) -> Vec<String> {
    let bytes = scope.as_bytes();
    let mut out = Vec::new();
    for m in lex.watchdog.find_iter(scope) {
        let Some(open) = scope[m.end()..].find('(').map(|o| o + m.end()) else {
            continue;
        };
        let (mut depth, mut start, mut i) = (0isize, None, open);
        while i < bytes.len() {
            match bytes[i] {
                b'(' | b'[' | b'{' => {
                    depth += 1;
                    if depth == 1 {
                        start = Some(i + 1);
                    }
                }
                b')' | b']' | b'}' => {
                    depth -= 1;
                    if depth == 0 {
                        break;
                    }
                }
                _ => {}
            }
            i += 1;
        }
        if let Some(s) = start {
            out.push(scope[s..i.min(scope.len())].to_owned());
        }
    }
    out
}

/// `[(name, signature line, closing line)]` for every `fn` in the text.
fn all_fns<'a>(lex: &Lex, lines: &[&'a str]) -> Vec<(&'a str, usize, usize)> {
    let mut out = Vec::new();
    let mut depth = 0isize;
    for (i, line) in lines.iter().enumerate() {
        if depth <= 0 {
            if let Some(name) = lex.func.captures(line).and_then(|c| c.get(1)) {
                let mut sig = i;
                while sig < lines.len() && !lines[sig].contains('{') {
                    if sig != i && lex.func.is_match(lines[sig]) {
                        break;
                    }
                    sig += 1;
                }
                // The reference `continue`s here, skipping this line's depth update: kept.
                if sig >= lines.len() {
                    continue;
                }
                let mut d = 0isize;
                for (j, body) in lines.iter().enumerate().skip(sig) {
                    d += delta(body);
                    if d <= 0 {
                        out.push((name.as_str(), sig, j));
                        break;
                    }
                }
            }
        }
        depth += delta(line);
    }
    out
}

/// Names of same-file functions whose body can panic.
fn panicking_callees(lex: &Lex, lines: &[&str]) -> BTreeSet<String> {
    all_fns(lex, lines)
        .into_iter()
        .filter(|&(_, sig, close)| (sig..=close.min(lines.len() - 1)).any(|j| lex.panics(lines[j])))
        .map(|(name, _, _)| name.to_owned())
        .collect()
}

fn calls_callee(lex: &Lex, line: &str, callees: &BTreeSet<String>) -> bool {
    callees.iter().any(|name| {
        lex.cached(&format!(r"^{}\s*\(", regex::escape(name)))
            .is_some_and(|r| {
                Lex::search_after(&r, line, |c| super::lex::word(c) || c == ':' || c == '.')
            })
    })
}

/// The `reap-after-panic` finding, or None.
fn ordering_finding(
    lex: &Lex,
    lines: &[&str],
    end: usize,
    reap: usize,
    callees: &BTreeSet<String>,
) -> Option<String> {
    let early =
        (end + 1..reap).find(|&j| lex.panics(lines[j]) || calls_callee(lex, lines[j], callees))?;
    Some(format!(
        "the reap is at line {}, AFTER a construct that can panic at line {} — a \
         failing test skips the reap and leaks a server that never exits",
        reap + 1,
        early + 1
    ))
}

/// The line that STOPS the child (`kill`), or the `wait` when nothing kills.
fn reap_line(lex: &Lex, lines: &[&str], start: usize, close: usize) -> Option<usize> {
    let (mut stop, mut collect) = (None, None);
    for (j, line) in lines.iter().enumerate().take(close + 1).skip(start) {
        if stop.is_none() && lex.stop.is_match(line) {
            stop = Some(j);
        }
        if collect.is_none() && lex.collect.is_match(line) {
            collect = Some(j);
        }
        if stop.is_some() && collect.is_some() {
            break;
        }
    }
    stop.or(collect)
}

/// Is the `.spawn()` on line `i` a PROCESS spawn? `Command::new` within the
/// statement above it, or a receiver DECLARED as a `Command`.
fn is_command_spawn(lex: &Lex, lines: &[&str], i: usize) -> bool {
    let floor = i.saturating_sub(8);
    for j in (floor..=i).rev() {
        if lex.cmd_new.is_match(lines[j]) {
            return true;
        }
        if j < i && lex.semi_brace_open.is_match(lines[j]) {
            break;
        }
    }
    let Some(name) = lex.receiver.captures(lines[i]).and_then(|c| c.get(1)) else {
        return false;
    };
    let name = name.as_str();
    let n = regex::escape(name);
    let decl = format!(
        r"(?:\blet\s+(?:mut\s+)?{n}\s*[:=]|\b{n}\s*:\s*&?\s*mut\s+)(?:\s*)(?:std::process::|process::)?Command\b"
    );
    let Some(decl) = lex.cached(&decl) else {
        return false;
    };
    lines.iter().any(|l| l.contains(name) && decl.is_match(l))
        || decl.is_match(&lines[i.saturating_sub(40)..(i + 40).min(lines.len())].join("\n"))
}

/// `[(lineno, tag, why)]` over one masked, region-limited Rust file.
#[must_use]
pub fn rust_findings(lex: &Lex, masked: &str, facts: &CrateFacts) -> Vec<Verdict> {
    let local_guards = guard_types(lex, masked);
    let local_drops = drop_types(lex, masked);
    let mut guards = local_guards.clone();
    let mut drops = local_drops.clone();
    let mut known = known_types(lex, masked);
    if facts.present {
        guards.extend(facts.guards.difference(&local_drops).cloned());
        drops.extend(facts.drops.difference(&local_guards).cloned());
    }
    known.extend(facts.known.iter().cloned());
    let lines: Vec<&str> = masked.split('\n').collect();
    let callees = panicking_callees(lex, &lines);
    let types = Types {
        guards: &guards,
        known: &known,
        drops: &drops,
    };
    let mut out = Vec::new();
    for (i, line) in lines.iter().enumerate() {
        if !lex.spawn.is_match(line) || !is_command_spawn(lex, &lines, i) {
            continue;
        }
        let (sig, close) = fn_bounds(lex, &lines, i);
        let (end, semicolon) = statement_end(&lines, i, close, depth_at(&lines, sig, i));
        let head = stmt_head(lex, &lines, i, sig);
        let statement = lines[head..=end].join("\n");
        let body = lines[sig..=close].join("\n");
        let bound = binding(lex, &lines, i, sig);
        let watchdogs = watchdog_bodies(lex, &body);
        let site = Site {
            statement: &statement,
            body: &body,
            lines: &lines,
            head,
            end,
            close,
            sig,
            binding: bound.as_deref(),
        };
        let st = self_type(lex, &lines, sig);
        match attribute(lex, &site, &types, st.as_deref(), &watchdogs) {
            Some(Attribution::Guarded) => continue,
            Some(Attribution::Tag(tag, why)) => {
                out.push((i + 1, tag, why));
                continue;
            }
            None => {}
        }
        if lex.return_head.is_match(&statement) || (!semicolon && fn_returns(lex, &lines, sig)) {
            out.push((
                i + 1,
                "handoff",
                "spawn handed to the caller; this gate does not follow it".to_owned(),
            ));
            continue;
        }
        let Some(reap) = reap_line(lex, &lines, end + 1, close) else {
            out.push((
                i + 1,
                "unreaped-spawn",
                "a Child that is never kill()ed or wait()ed: measured LIVE ORPHAN".to_owned(),
            ));
            continue;
        };
        if let Some(early) = ordering_finding(lex, &lines, end, reap, &callees) {
            out.push((i + 1, "reap-after-panic", early));
        }
    }
    out
}
