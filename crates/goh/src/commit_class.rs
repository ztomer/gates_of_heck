//! `goh commit-class`: a fix names its class and siblings; a third instance ends the class.
//!
//! Roadmap Phase 8, ported from `ZoneWM`'s `tools/check_commit_class.py`, whose selftest cases
//! `tests/test_commit_class.py` carries.
//!
//! A written "fix the class" rule was applied one site at a time; the commit message is where the
//! question cannot be skipped:
//!
//! * `Class:` -- the invariant that broke, as a reusable phrase -- and `Siblings:` -- the other
//!   sites, or `none (<the search that found none>)`;
//! * a Class sharing half its words with two or more earlier classes is a THIRD instance, and must
//!   also carry `Systemic:` (the helper, gate or type that ends it) or `Filed:` (the item that
//!   will), with substance: `none yet`, `n/a`, `tbd`, `-` are what gaming it looked like.
//!
//! One change from the prototype, at its author's suggestion: a NEW commit's trailers must be git's
//! own trailer block, the message's last paragraph, so `%(trailers)` and every other tool read what
//! the gate read. HISTORY is read leniently (a `Class:` line anywhere), so classes committed before
//! the port still count.

use std::collections::{BTreeMap, BTreeSet};
use std::process::Command;

const HISTORY: &str = "-400"; // earlier commits a class is compared against
const REPEATS: usize = 2;
const KEYS: [&str; 4] = ["Class", "Siblings", "Systemic", "Filed"];
const STOP: [&str; 17] = [
    "a", "an", "the", "of", "in", "on", "is", "it", "its", "to", "and", "or", "for", "by", "not",
    "no", "be",
];

/// `fix:`, `perf(scope):`, `fix!:` -- never `fixup! ...` or `fixes ...`.
fn gated(subject: &str) -> bool {
    let Some(mut rest) = subject
        .strip_prefix("fix")
        .or_else(|| subject.strip_prefix("perf"))
    else {
        return false;
    };
    if let Some(scoped) = rest.strip_prefix('(') {
        let Some(end) = scoped.find(')') else {
            return false;
        };
        rest = &scoped[end + 1..];
    }
    rest.strip_prefix('!').unwrap_or(rest).starts_with(':')
}

/// The message as git commits it: an editor buffer's comments and everything below its scissors
/// line dropped.
fn cleaned(message: &str) -> Vec<&str> {
    message
        .lines()
        .take_while(|l| !(l.starts_with("# ") && l.contains(">8")))
        .filter(|l| !l.starts_with('#'))
        .map(str::trim_end)
        .collect()
}

/// git's trailer block: the last paragraph (never the subject's), when EVERY line in it is
/// `Token: value` or a continuation. Stricter than git, so whatever passes here git reads too.
fn final_trailers(lines: &[&str]) -> BTreeMap<String, String> {
    let mut end = lines.len();
    while end > 0 && lines[end - 1].is_empty() {
        end -= 1;
    }
    let mut start = end;
    while start > 0 && !lines[start - 1].is_empty() {
        start -= 1;
    }
    let mut out = BTreeMap::new();
    if start == 0 {
        return out; // the subject paragraph is never a trailer block
    }
    let mut last: Option<String> = None;
    for line in &lines[start..end] {
        if line.starts_with([' ', '\t']) {
            let Some(v) = last.as_ref().and_then(|k| out.get_mut(k)) else {
                return BTreeMap::new();
            };
            v.push(' ');
            v.push_str(line.trim());
            continue;
        }
        let Some((token, value)) = line.split_once(':') else {
            return BTreeMap::new();
        };
        if token.is_empty()
            || !token
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-')
        {
            return BTreeMap::new();
        }
        out.insert(token.to_owned(), value.trim().to_owned());
        last = Some(token.to_owned());
    }
    out
}

/// The prototype's reading: a `Key:` line anywhere, the last one winning.
fn loose_trailers(lines: &[&str]) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    for line in lines {
        for key in KEYS {
            if let Some(v) = line.strip_prefix(key).and_then(|r| r.strip_prefix(':')) {
                out.insert(key.to_owned(), v.trim().to_owned());
            }
        }
    }
    out
}

fn words(class: &str) -> BTreeSet<String> {
    class
        .to_lowercase()
        .split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|w| w.len() > 2 && !STOP.contains(w))
        .map(str::to_owned)
        .collect()
}

/// Half the smaller class's words shared (a one-word class needs that word).
fn same_class(a: &str, b: &str) -> bool {
    let (wa, wb) = (words(a), words(b));
    let smaller = wa.len().min(wb.len());
    smaller > 0 && 2 * wa.intersection(&wb).count() >= smaller
}

fn ends_the_class(t: &BTreeMap<String, String>) -> bool {
    ["Systemic", "Filed"].iter().any(|k| {
        t.get(*k).is_some_and(|v| {
            let v = v.to_lowercase();
            !v.is_empty() && !["none", "n/a", "tbd", "-"].iter().any(|p| v.starts_with(p))
        })
    })
}

/// `none (<search>)`, the search at least eight characters.
fn none_with_search(siblings: &str) -> bool {
    let lower = siblings.to_lowercase();
    lower
        .strip_prefix("none")
        .map(str::trim_start)
        .and_then(|r| r.strip_prefix('('))
        .and_then(|r| r.trim_end().strip_suffix(')'))
        .is_some_and(|search| search.chars().count() >= 8)
}

/// Why this message cannot be committed; empty when it can.
#[must_use]
pub fn refusals(message: &str, earlier: &[String]) -> Vec<String> {
    let lines = cleaned(message);
    let Some(subject) = lines.iter().find(|l| !l.is_empty()) else {
        return Vec::new();
    };
    if !gated(subject) {
        return Vec::new();
    }
    let t = final_trailers(&lines);
    let loose = loose_trailers(&lines);
    let mut out = Vec::new();
    for key in KEYS {
        if !t.contains_key(key) && loose.contains_key(key) {
            out.push(format!(
                "`{key}:` sits above the message's last paragraph, where git does not read \
                 trailers: move it into the trailer block, beside Co-Authored-By"
            ));
        }
    }
    let class = t.get("Class").filter(|c| !c.is_empty());
    if class.is_none() && !loose.contains_key("Class") {
        out.push(
            "a fix or perf commit names its Class: the invariant that broke, as a reusable phrase"
                .to_owned(),
        );
    }
    match t.get("Siblings").filter(|s| !s.is_empty()) {
        None if !loose.contains_key("Siblings") => out.push(
            "a fix or perf commit names its Siblings: the other sites, or `none (<the search>)`"
                .to_owned(),
        ),
        Some(s) if s.to_lowercase().starts_with("none") && !none_with_search(s) => out.push(
            "`Siblings: none` says how it knows: `none (<the grep or scan that found none>)`"
                .to_owned(),
        ),
        _ => {}
    }
    if let Some(class) = class {
        let seen: Vec<&String> = earlier.iter().filter(|c| same_class(class, c)).collect();
        if seen.len() >= REPEATS && !ends_the_class(&t) {
            let named: Vec<&str> = seen.iter().take(3).map(|s| s.as_str()).collect();
            out.push(format!(
                "Class '{class}' is the instance after {} earlier ones ({}): end the class with \
                 `Systemic: <helper, gate or type>`, or name the item that will with \
                 `Filed: <item id>`",
                seen.len(),
                named.join("; ")
            ));
        }
    }
    out
}

/// `[(sha, message)]` from `git log <args>`, in git's order.
fn log(args: &[&str]) -> Vec<(String, String)> {
    let Ok(out) = Command::new("git")
        .arg("log")
        .arg("--format=%H%x00%B%x01")
        .args(args)
        .output()
    else {
        return Vec::new();
    };
    String::from_utf8_lossy(&out.stdout)
        .split('\u{1}')
        .filter_map(|chunk| {
            let (sha, body) = chunk.split_once('\0')?;
            Some((sha.trim().to_owned(), body.trim().to_owned()))
        })
        .collect()
}

fn class_of(message: &str) -> Option<String> {
    let lines = cleaned(message);
    final_trailers(&lines)
        .remove("Class")
        .or_else(|| loose_trailers(&lines).remove("Class"))
        .filter(|c| !c.is_empty())
}

/// The classes of the commits before `rev` (inclusive), oldest first.
fn classes_from(rev: &str) -> Vec<String> {
    let mut found: Vec<String> = log(&[HISTORY, rev])
        .iter()
        .filter_map(|(_, m)| class_of(m))
        .collect();
    found.reverse();
    found
}

/// `goh commit-class [FILE | --range REV... [--report]]`.
#[must_use]
pub fn run(file: Option<&str>, range: &[String], report: bool) -> i32 {
    match file {
        Some(f) => check_file(f),
        None if !range.is_empty() => check_range(range, report),
        None => {
            eprintln!("goh commit-class: FILE or --range REV...");
            2
        }
    }
}

/// `goh commit-class FILE`: the message git is about to commit, against HEAD's history.
#[must_use]
pub fn check_file(path: &str) -> i32 {
    let Ok(message) = std::fs::read_to_string(path) else {
        eprintln!("✗ [commit_class] cannot read the message file {path}");
        return 2;
    };
    let why = refusals(&message, &classes_from("HEAD"));
    for line in &why {
        eprintln!("✗ [commit_class] commit refused: {line}");
    }
    i32::from(!why.is_empty())
}

/// `goh commit-class --range REV...`: every commit `git log REV...` names, oldest first, each
/// against the history before it and the range's own earlier commits.
#[must_use]
pub fn check_range(revs: &[String], report: bool) -> i32 {
    let args: Vec<&str> = std::iter::once("--reverse")
        .chain(revs.iter().map(String::as_str))
        .collect();
    let commits = log(&args);
    let mut earlier = commits
        .first()
        .map(|(sha, _)| classes_from(&format!("{sha}^")))
        .unwrap_or_default();
    let mut bad = 0;
    for (sha, body) in &commits {
        let why = refusals(body, &earlier);
        if !why.is_empty() {
            bad += 1;
            let mark = if report { "⚠" } else { "✗" };
            let subject: String = body.lines().next().unwrap_or("").chars().take(80).collect();
            eprintln!(
                "{mark} [commit_class] {} {subject}",
                &sha[..sha.len().min(8)]
            );
            for line in &why {
                eprintln!("{mark}     {line}");
            }
        }
        if let Some(class) = class_of(body) {
            earlier.push(class);
        }
    }
    if bad > 0 && !report {
        eprintln!("✗ [commit_class] {bad} commit(s) do not end their class or name it: amend them");
        return 1;
    }
    println!(
        "✓ [commit_class] {} commit(s), {bad} the rule refuses",
        commits.len()
    );
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_subjects_it_gates() {
        for s in ["fix: a", "perf(x): a", "fix!: a", "fix(core)!: a"] {
            assert!(gated(s), "{s}");
        }
        for s in [
            "fixup! fix: a",
            "fixes: a",
            "feat: a",
            "fix(x a",
            "docs: fix",
        ] {
            assert!(!gated(s), "{s}");
        }
    }

    #[test]
    fn the_trailer_block_is_the_last_paragraph_only() {
        let lines = cleaned("fix: a\n\nClass: c\n\nSiblings: s\nCo-Authored-By: x\n");
        let t = final_trailers(&lines);
        assert!(t.contains_key("Siblings") && !t.contains_key("Class"));
        assert_eq!(final_trailers(&cleaned("Class: subject only\n")).len(), 0);
        assert_eq!(
            final_trailers(&cleaned("fix: a\n\nprose line\nClass: c\n")).len(),
            0
        );
        let wrapped = final_trailers(&cleaned("fix: a\n\nClass: one\n  two\n"));
        assert_eq!(wrapped.get("Class").map(String::as_str), Some("one two"));
    }

    #[test]
    fn similarity_is_half_the_smaller_class() {
        assert!(same_class(
            "lock path declared twice",
            "a lock path declared in several files"
        ));
        assert!(!same_class(
            "window built per show",
            "a lock path declared in several files"
        ));
        assert!(!same_class("the of", "the of"));
    }
}
