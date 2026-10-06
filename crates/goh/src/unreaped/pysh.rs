//! The PYTHON and SHELL rules. Port of `checks/_spawn_py_sh.py`.
//!
//! Python: a `Popen` outside `with`, not handed off, not under a reaping
//! `try`/`finally`, whose reap is missing or sits below a raising line or an
//! early `return`. Shell: a backgrounded command in a block with no `kill`,
//! `wait` or `trap` (a script-global `trap` covers every launch).

use super::lex::{word, Lex};
use super::rust_rules::Verdict;

fn indent(line: &str) -> usize {
    line.chars().count() - line.trim_start().chars().count()
}

/// The end of the block `start` sits in: the next non-blank line at column 0.
fn py_block(lines: &[&str], start: usize) -> usize {
    (start + 1..lines.len())
        .find(|&j| !lines[j].trim().is_empty() && !lines[j].starts_with(char::is_whitespace))
        .unwrap_or(lines.len())
}

/// The Popen leaves this function: `return ...Popen(` or stored on an object.
fn py_handed_off(lex: &Lex, head: &str) -> bool {
    lex.py_return.is_match(head)
        || lex.py_self_attr.is_match(head)
        || lex.py_dotted_popen.is_match(head)
}

fn py_finally_body_reaps(lex: &Lex, lines: &[&str], start: usize, ind: usize, end: usize) -> bool {
    for body in &lines[start.min(end)..end] {
        if body.trim().is_empty() {
            continue;
        }
        if indent(body) <= ind {
            break;
        }
        if lex.py_reap.is_match(body) {
            return true;
        }
    }
    false
}

/// Is the Popen under a `try: ... finally: <reap>`, opened after it or around it?
fn py_finally_reaps(lex: &Lex, lines: &[&str], i: usize, end: usize) -> bool {
    let ind = indent(lines[i]);
    let mut opener: Option<(usize, &str)> = None;
    for (j, &line) in lines.iter().enumerate().take(end.min(i + 80)).skip(i + 1) {
        if line.trim().is_empty() {
            continue;
        }
        let here = indent(line);
        if here < ind {
            break;
        }
        if here == ind && line.trim_end().ends_with(':') {
            opener = Some((j, line.trim()));
            break;
        }
    }
    if let Some((at, text)) = opener {
        if text.starts_with("try:") {
            let fin = (at + 1..end)
                .find(|&k| lines[k].trim().starts_with("finally:") && indent(lines[k]) == ind);
            if fin.is_some_and(|f| py_finally_body_reaps(lex, lines, f + 1, ind, end)) {
                return true;
            }
        }
    }
    for j in i + 1..end.min(i + 200) {
        let line = lines[j];
        if line.trim().is_empty() {
            continue;
        }
        let here = indent(line);
        if here < ind {
            return line.trim().starts_with("finally:")
                && py_finally_body_reaps(lex, lines, j + 1, here, end);
        }
    }
    false
}

fn py_binding_at(lex: &Lex, lines: &[&str], j: usize) -> Option<String> {
    (j.saturating_sub(60)..j).find_map(|k| {
        lex.py_bind
            .captures(lines[k])
            .and_then(|c| c.get(1).map(|m| m.as_str().to_owned()))
    })
}

/// Did the code just above this `return` establish the child is already gone?
fn py_observed_exit(lex: &Lex, lines: &[&str], j: usize, binding: Option<&str>) -> bool {
    let Some(b) = binding else {
        return false;
    };
    let look = lines[j.saturating_sub(3)..j].join("\n");
    lex.found(
        &format!(
            r"\b{}\s*\.\s*(?:poll|returncode)\b|\bwaitpid\b",
            regex::escape(b)
        ),
        &look,
    )
}

/// Is the `return` at `j` an exit that hands the handle onward?
fn py_exits_but_hands_off(lex: &Lex, lines: &[&str], j: usize) -> bool {
    if !lex.py_return.is_match(lines[j]) {
        return false;
    }
    if py_observed_exit(lex, lines, j, py_binding_at(lex, lines, j).as_deref()) {
        return true;
    }
    if lex.py_bind.is_match(lines[j]) {
        return false;
    }
    (j.saturating_sub(60)..j)
        .filter_map(|k| lex.py_bind.captures(lines[k]).and_then(|c| c.get(1)))
        .any(|n| lex.found(&format!(r"\b{}\b", regex::escape(n.as_str())), lines[j]))
}

#[must_use]
pub fn python_findings(lex: &Lex, masked: &str) -> Vec<Verdict> {
    let lines: Vec<&str> = masked.split('\n').collect();
    let mut out = Vec::new();
    for (i, line) in lines.iter().enumerate() {
        if !lex.py_popen(line) || lex.py_with.is_match(line) {
            continue;
        }
        let end = py_block(&lines, i);
        if py_handed_off(lex, line) {
            out.push((
                i + 1,
                "handoff",
                "the Popen leaves this function; the caller owes the reap".to_owned(),
            ));
            continue;
        }
        if py_finally_reaps(lex, &lines, i, end) {
            continue;
        }
        let Some(stop) = (i + 1..end).find(|&j| lex.py_reap.is_match(lines[j])) else {
            out.push((
                i + 1,
                "unreaped-spawn",
                "Popen with no `with`, wait(), communicate() or kill(): measured LIVE ORPHAN"
                    .to_owned(),
            ));
            continue;
        };
        let early = (i + 1..stop)
            .find(|&j| lex.py_panic.is_match(lines[j]) && !py_exits_but_hands_off(lex, &lines, j));
        if let Some(e) = early {
            out.push((
                i + 1,
                "reap-after-panic",
                format!(
                    "the reap is at line {}, AFTER a raising line at {}",
                    stop + 1,
                    e + 1
                ),
            ));
        }
    }
    out
}

/// `BG`: a `&` that ends a command -- not `&&`, `&>`, `>&`, `|&`, `)&`.
fn background(code: &str) -> bool {
    let chars: Vec<char> = code.chars().collect();
    chars.iter().enumerate().any(|(i, &c)| {
        c == '&'
            && !i
                .checked_sub(1)
                .and_then(|p| chars.get(p))
                .is_some_and(|&p| word(p) || ")&|>".contains(p))
            && !chars
                .get(i + 1)
                .is_some_and(|&n| word(n) || "&>".contains(n))
    })
}

/// Shell functions at brace depth zero: `[(start, end)]`.
fn sh_functions(lex: &Lex, lines: &[&str]) -> Vec<(usize, usize)> {
    let delta = |l: &str| {
        isize::try_from(l.matches('{').count()).unwrap_or(isize::MAX)
            - isize::try_from(l.matches('}').count()).unwrap_or(isize::MAX)
    };
    let mut starts = Vec::new();
    let mut depth = 0isize;
    for (j, line) in lines.iter().enumerate() {
        if depth == 0 && lex.sh_fn.is_match(line) {
            starts.push(j);
        }
        depth += delta(line);
    }
    starts
        .into_iter()
        .map(|s| {
            let mut d = 0isize;
            for (k, line) in lines.iter().enumerate().skip(s) {
                d += delta(line);
                if d <= 0 {
                    return (s, k);
                }
            }
            (s, lines.len() - 1)
        })
        .collect()
}

#[must_use]
pub fn shell_findings(lex: &Lex, masked: &str) -> Vec<Verdict> {
    let lines: Vec<&str> = masked.split('\n').collect();
    let fns = sh_functions(lex, &lines);
    let inside = |j: usize| fns.iter().any(|&(lo, hi)| lo <= j && j <= hi);
    let script_trap = (0..lines.len()).any(|j| !inside(j) && lex.sh_traps(lines[j]));
    let mut out = Vec::new();
    for (i, line) in lines.iter().enumerate() {
        let code = line.trim_end();
        if code.is_empty() || code.ends_with("&&") || code.contains("&>") || !background(code) {
            continue;
        }
        let (lo, hi) = fns
            .iter()
            .copied()
            .find(|&(lo, hi)| lo <= i && i <= hi)
            .unwrap_or((0, lines.len() - 1));
        if script_trap || lex.sh_reaps(&lines[lo..=hi].join("\n")) {
            continue;
        }
        out.push((
            i + 1,
            "unreaped-spawn",
            "a backgrounded command with no kill, no wait and no trap: it outlives the test"
                .to_owned(),
        ));
    }
    out
}
