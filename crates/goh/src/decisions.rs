//! `goh decisions`: every decision record is whole and points at what exists (GOH.LEDGERGATE).
//!
//! A decision ledger is one file per settled question, `docs/decisions/D-NNNN-<slug>.md`, with
//! seven fields (the decision-ledger skill's schema). A record missing its `Falsifier` is a ruling
//! nobody can prove wrong; a `Lives in` naming a path the tree deleted sends the next reader to
//! nothing. Before this step goh carried twelve records and no check, so either passed. The rules:
//!
//!   1. the ledger is not empty: a gate over no records passes nothing;
//!   2. each record opens `# D-NNNN — title`, and the id is the filename's;
//!   3. each of the seven fields is present and non-empty (a field may wrap onto indented lines);
//!   4. every backticked `Lives in` token that reads as a path (a `/`, or a file extension) is a
//!      tracked file or directory; any other token is a symbol `git grep -w` finds in the tree
//!      outside the ledger (the record naming it is not evidence that it exists);
//!   5. ids are unique, no two records share a Question, and every `D-NNNN` a record cites exists.
//!
//! Opt-in (`GOH_DECISIONS=on`), because most of the estate keeps no ledger. At `--staged` the
//! records and the tree are the index's, so a commit that deletes a path a record names is red.

use goh_tui::{ERR, OK};
use regex::Regex;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write as _;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::LazyLock;

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
}

static TITLE: LazyLock<Regex> = LazyLock::new(|| re(r"^# (D-\d{4}) \x{2014} \S"));
static FIELD: LazyLock<Regex> = LazyLock::new(|| re(r"^- \*\*(.+?):\*\*\s*(.*)$"));
static EXT: LazyLock<Regex> = LazyLock::new(|| re(r"\.[A-Za-z0-9]+$"));
static NAME: LazyLock<Regex> = LazyLock::new(|| re(r"^D-\d{4}-[^/]+\.md$"));
static CITE: LazyLock<Regex> = LazyLock::new(|| re(r"\bD-\d{4}\b"));

/// The report prefix.
pub const TAG: &str = "[decisions]";
/// The step's label, as `structural_steps.txt` names it.
pub const LABEL: &str = "every decision record is whole";
/// Where the ledger lives, repo-relative.
pub const DIR: &str = "docs/decisions";
/// The seven fields every record carries.
pub const FIELDS: [&str; 7] = [
    "Question",
    "Evidence",
    "Options priced",
    "Ruling",
    "Falsifier",
    "Re-open when",
    "Lives in",
];

/// Is this gate on? Unset is off.
///
/// # Errors
///
/// A value that is neither `on` nor `off`, named: a typo that disabled the gate is the defect it
/// exists to prevent.
pub fn enabled(raw: Option<&str>) -> Result<bool, String> {
    match raw.map_or("", str::trim) {
        "" | "off" => Ok(false),
        "on" => Ok(true),
        other => Err(format!("GOH_DECISIONS is neither on nor off: {other:?}")),
    }
}

/// One record as read: its heading id and its fields (a wrapped field joined by spaces).
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Record {
    pub id: Option<String>,
    pub fields: BTreeMap<String, String>,
}

/// Read one record's text.
#[must_use]
pub fn parse(text: &str) -> Record {
    let mut rec = Record::default();
    let mut open: Option<String> = None;
    for line in text.lines() {
        if rec.id.is_none() {
            if let Some(m) = TITLE.captures(line) {
                rec.id = Some(m[1].to_owned());
                continue;
            }
        }
        if let Some(m) = FIELD.captures(line) {
            let name = m[1].to_owned();
            rec.fields.insert(name.clone(), m[2].trim().to_owned());
            open = Some(name);
        } else if line.starts_with([' ', '\t']) && !line.trim().is_empty() {
            if let Some(value) = open.as_ref().and_then(|n| rec.fields.get_mut(n)) {
                if !value.is_empty() {
                    value.push(' ');
                }
                value.push_str(line.trim());
            }
        } else {
            open = None;
        }
    }
    rec
}

/// The backticked tokens of a `Lives in` value.
#[must_use]
pub fn tokens(value: &str) -> Vec<String> {
    value
        .split('`')
        .skip(1)
        .step_by(2)
        .map(str::trim)
        .filter(|t| !t.is_empty())
        .map(str::to_owned)
        .collect()
}

/// Does `token` read as a path (rule 4)? A space makes it a command (`goh land`), judged by its
/// first word as a symbol.
#[must_use]
pub fn is_path(token: &str) -> bool {
    if token.contains(char::is_whitespace) {
        return false;
    }
    token.contains('/') || EXT.is_match(token)
}

/// Is `path` a tracked file, or a directory holding one?
#[must_use]
pub fn tracked(path: &str, files: &BTreeSet<String>) -> bool {
    let path = path.trim_start_matches("./").trim_end_matches('/');
    files.contains(path) || {
        let dir = format!("{path}/");
        files
            .range(dir.clone()..)
            .next()
            .is_some_and(|f| f.starts_with(&dir))
    }
}

/// Does `git grep -w` find `word` in the tree outside the ledger?
fn symbol_found(root: &Path, word: &str, staged: bool) -> bool {
    let mut cmd = Command::new("git");
    cmd.arg("-C").arg(root).args(["grep", "-q", "-w", "-F"]);
    if staged {
        cmd.arg("--cached");
    }
    cmd.env("GIT_OPTIONAL_LOCKS", "0")
        .args(["-e", word, "--", ".", &format!(":(exclude){DIR}")])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    cmd.status().is_ok_and(|s| s.success())
}

/// Every finding over the ledger at `root`, and how many records it read.
///
/// # Errors
///
/// A git that cannot list the tree: an unreadable ledger is not a clean one.
pub fn judge(root: &Path, staged: bool) -> Result<(usize, Vec<String>), String> {
    let files: BTreeSet<String> = crate::gitutil::index_files(root)?.into_iter().collect();
    let records: Vec<&String> = files.iter().filter(|f| shapes::is_record(f)).collect();
    if records.is_empty() {
        return Ok((
            0,
            vec![format!(
                "{DIR}: no records, and an empty ledger is not a clean one: write \
                 {DIR}/D-0001-<slug>.md with the seven fields, or turn GOH_DECISIONS off"
            )],
        ));
    }
    let mut out = Vec::new();
    let mut ids: BTreeMap<String, String> = BTreeMap::new();
    let mut questions: BTreeMap<String, String> = BTreeMap::new();
    let mut cited: BTreeSet<(String, String)> = BTreeSet::new();
    for rel in &records {
        let name = rel.rsplit('/').next().unwrap_or(rel);
        let text = crate::gitutil::content_bytes(root, rel, staged)
            .map(|b| String::from_utf8_lossy(&b).into_owned())
            .unwrap_or_default();
        let rec = parse(&text);
        match &rec.id {
            None => out.push(format!("{rel}:1: no `# D-NNNN \u{2014} title` heading")),
            Some(id) if !name.starts_with(&format!("{id}-")) => {
                out.push(format!(
                    "{rel}:1: the heading says {id} and the filename does not"
                ));
            }
            Some(id) => {
                if let Some(first) = ids.insert(id.clone(), (*rel).clone()) {
                    out.push(format!("{rel}:1: id {id} is already {first}'s"));
                }
            }
        }
        for f in FIELDS {
            if rec.fields.get(f).is_none_or(String::is_empty) {
                out.push(format!("{rel}: `{f}` is missing or empty"));
            }
        }
        let q = rec
            .fields
            .get("Question")
            .map(|q| {
                q.to_lowercase()
                    .split_whitespace()
                    .collect::<Vec<_>>()
                    .join(" ")
            })
            .unwrap_or_default();
        if !q.is_empty() {
            if let Some(first) = questions.insert(q, (*rel).clone()) {
                out.push(format!(
                    "{rel}: the same Question as {first}: one record per question"
                ));
            }
        }
        for token in tokens(rec.fields.get("Lives in").map_or("", String::as_str)) {
            if is_path(&token) {
                if !tracked(&token, &files) {
                    out.push(format!(
                        "{rel}: `Lives in` names `{token}`, which is not in the tree"
                    ));
                }
            } else {
                let word = token.split_whitespace().next().unwrap_or(&token);
                if !symbol_found(root, word, staged) {
                    out.push(format!(
                        "{rel}: `Lives in` names `{token}`, which nothing outside {DIR} mentions"
                    ));
                }
            }
        }
        for m in CITE.find_iter(&text) {
            cited.insert((m.as_str().to_owned(), (*rel).clone()));
        }
    }
    for (id, by) in cited {
        if !ids.contains_key(&id) {
            out.push(format!("{by}: cites {id}, and no such record exists"));
        }
    }
    Ok((records.len(), out))
}

/// The verdict as text and exit code.
#[must_use]
pub fn verdict(root: &Path, staged: bool) -> (i32, String) {
    match judge(root, staged) {
        Err(message) => (2, format!("{ERR} {TAG} {message}\n")),
        Ok((n, found)) if found.is_empty() => (
            0,
            format!(
                "{OK} {TAG} {}, seven fields each, every `Lives in` found, no question twice\n",
                goh_tui::counted(n, "record")
            ),
        ),
        Ok((n, found)) => {
            let mut text = String::new();
            for f in &found {
                let _ = writeln!(text, "{ERR} {TAG} {f}");
            }
            let _ = writeln!(
                text,
                "{ERR} {TAG} {} over {} (schema: the decision-ledger skill)",
                goh_tui::counted(found.len(), "problem"),
                goh_tui::counted(n, "record"),
            );
            (1, text)
        }
    }
}

/// `goh decisions`: the check on its own, whatever `.gatesrc` says.
#[must_use]
pub fn run_command(staged: bool) -> i32 {
    let root = crate::gatesrc::config_root();
    let (code, text) = verdict(&root, staged);
    if code == 0 {
        goh_tui::out(&text);
    } else {
        goh_tui::eout(&text);
    }
    code
}

/// The structural step, when `GOH_DECISIONS=on`.
#[must_use]
pub fn step(repo: &Path, cfg: &crate::gatesrc::Gatesrc, staged: bool) -> Option<i32> {
    match enabled(cfg.decisions.as_deref()) {
        Ok(true) => {}
        Ok(false) => return None,
        Err(message) => {
            goh_tui::eoutln!("{ERR} {TAG} {message}");
            return Some(2);
        }
    }
    let start = crate::step_report::begin(LABEL);
    let (code, text) = verdict(repo, staged);
    if code == 0 {
        goh_tui::out(&text);
        crate::step_report::ok(LABEL, start);
        return None;
    }
    let how = crate::step_report::ported("crates/goh/src/decisions.rs");
    let _ = crate::step_report::fail(LABEL, &how, &text, start);
    Some(code)
}

#[path = "decisions_shapes.rs"]
pub mod shapes;

#[cfg(test)]
#[path = "decisions_tests.rs"]
mod tests;
