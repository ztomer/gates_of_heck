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
//! * a Class sharing two content words with two or more earlier classes is a THIRD instance, and must
//!   also carry `Systemic:` (the helper, gate or type that ends it) or `Filed:` (the item that
//!   will), with substance: `none yet`, `n/a`, `tbd`, `-` are what gaming it looked like.
//!
//! One change from the prototype, at its author's suggestion: a NEW commit's trailers must be git's
//! own trailer block, the message's last paragraph, so `%(trailers)` and every other tool read what
//! the gate read. HISTORY is read leniently (a `Class:` line anywhere), so classes committed before
//! the port still count.

use std::collections::{BTreeMap, BTreeSet};
use std::process::Command;

#[path = "commit_class_clusters.rs"]
mod clusters;
#[path = "commit_class_words.rs"]
mod similarity;
#[cfg(test)]
use similarity::FREQUENT_FLOOR;
use similarity::{frequent, same_class};

const HISTORY: &str = "-400"; // earlier commits a class is compared against
const REPEATS: usize = 2;
const KEYS: [&str; 4] = ["Class", "Siblings", "Systemic", "Filed"];
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
///
/// `placement`: the trailers must be git's trailer block -- true for a new commit; false for a REPLAY of history, which is judged by
/// the rule its commits were written under (a `Key:` line anywhere), so the replay shows what the
/// class rule says rather than one placement line per pre-port commit.
#[must_use]
pub fn refusals(
    message: &str,
    earlier: &[String],
    vocabulary: &BTreeSet<String>,
    placement: bool,
) -> Vec<String> {
    let lines = cleaned(message);
    let Some(subject) = lines.iter().find(|l| !l.is_empty()) else {
        return Vec::new();
    };
    if !gated(subject) {
        return Vec::new();
    }
    let loose = loose_trailers(&lines);
    let t = if placement {
        final_trailers(&lines)
    } else {
        let mut both = loose.clone();
        both.extend(final_trailers(&lines));
        both
    };
    let mut out = Vec::new();
    for key in KEYS {
        if placement && !t.contains_key(key) && loose.contains_key(key) {
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
        let seen: Vec<&String> = earlier
            .iter()
            .filter(|c| same_class(class, c, vocabulary))
            .collect();
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

/// git's stdout.
///
/// # Errors
///
/// git's own message when it refuses: a listing git refused is never an empty one.
fn git(args: &[&str]) -> Result<Vec<u8>, String> {
    let out = Command::new("git")
        .args(args)
        .output()
        .map_err(|e| format!("git: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "git {} failed: {}",
            args.join(" "),
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(out.stdout)
}

/// `[(sha, message)]` from `git log <args>`, in git's order.
///
/// # Errors
///
/// git's own message when it refuses: a range it cannot read is never zero commits.
fn log(args: &[&str]) -> Result<Vec<(String, String)>, String> {
    let mut all = vec!["log", "--format=%H%x00%B%x01"];
    all.extend_from_slice(args);
    let out = git(&all)?;
    Ok(String::from_utf8_lossy(&out)
        .split('\u{1}')
        .filter_map(|chunk| {
            let (sha, body) = chunk.split_once('\0')?;
            Some((sha.trim().to_owned(), body.trim().to_owned()))
        })
        .collect())
}

fn class_of(message: &str) -> Option<String> {
    let lines = cleaned(message);
    final_trailers(&lines)
        .remove("Class")
        .or_else(|| loose_trailers(&lines).remove("Class"))
        .filter(|c| !c.is_empty())
}

/// The classes of the commits before `rev` (inclusive), oldest first; none before a first commit.
fn classes_from(rev: &str) -> Result<Vec<String>, String> {
    let born = git(&[
        "rev-parse",
        "--verify",
        "--quiet",
        &format!("{rev}^{{commit}}"),
    ])
    .is_ok();
    if !born {
        return Ok(Vec::new());
    }
    let mut found: Vec<String> = log(&[HISTORY, rev])?
        .iter()
        .filter_map(|(_, m)| class_of(m))
        .collect();
    found.reverse();
    Ok(found)
}

/// `goh commit-class [FILE | --range REV... [--report | --clusters]]`.
#[must_use]
pub fn run(file: Option<&str>, range: &[String], report: bool, clusters: bool) -> i32 {
    match file {
        Some(f) => check_file(f),
        None if clusters && !range.is_empty() => clusters::report(range),
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
    let history = match classes_from("HEAD") {
        Ok(h) => h,
        Err(e) => {
            eprintln!("✗ [commit_class] {e}");
            return 2;
        }
    };
    let why = refusals(&message, &history, &frequent(&history), true);
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
    let read = log(&args).and_then(|commits| {
        let earlier = match commits.first() {
            Some((sha, _)) => classes_from(&format!("{sha}^"))?,
            None => Vec::new(),
        };
        Ok((commits, earlier))
    });
    let (commits, mut earlier) = match read {
        Ok(r) => r,
        Err(e) => {
            eprintln!("✗ [commit_class] {e}");
            return 2;
        }
    };
    let whole: Vec<String> = earlier
        .iter()
        .cloned()
        .chain(commits.iter().filter_map(|(_, m)| class_of(m)))
        .collect();
    let vocabulary = frequent(&whole);
    let mut bad = 0;
    for (sha, body) in &commits {
        let why = refusals(body, &earlier, &vocabulary, !report);
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
#[path = "commit_class_tests.rs"]
mod tests;
