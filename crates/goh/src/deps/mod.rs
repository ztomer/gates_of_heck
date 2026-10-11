//! Dependency currency -- Rust port of the retired `checks/check_dep_currency.py` (Phase N1).
//!
//! Two arms of different strength. A direct dependency pinned BELOW what the
//! graph already resolves (two majors of one crate because of this pin) is
//! FATAL and needs no network. Currency against crates.io REPORTS: a major
//! behind fails only with `--strict`, or when `--ratchet FILE` has not
//! triaged it; an unreachable index is said out loud, never a clean bill.
//!
//! "Major" is Cargo's compatibility boundary (`semver::compat_class`), so
//! `0.3 -> 0.4` is a major. The ratchet is one triaged crate per line,
//! `name  # why it may stand`; an entry with no reason fails, and so does an
//! entry whose crate is no longer behind a major -- the list can only shrink.

pub mod cratesio;
pub mod semver;
pub mod tree;

use goh_tui::{ERR, OK, STEP, WARN};
use std::fmt::Write as _;
use std::path::{Path, PathBuf};

use serde_json::Value;
use tree::Dep;

/// One finding: `(severity, name, detail, where)`.
#[derive(Clone)]
struct Finding {
    severity: &'static str,
    name: String,
    detail: String,
    place: String,
}

impl Finding {
    fn json(&self) -> Value {
        serde_json::json!({ "severity": self.severity, "name": self.name, "detail": self.detail, "where": self.place })
    }
}

#[derive(Default)]
struct Report {
    findings: Vec<Finding>,
    notes: Vec<String>,
    examined: usize,
    manifests_seen: usize,
    checked_currency: bool,
}

/// Python's `repr()` of a requirement string, as the finding quotes it.
fn py_repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::from(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

fn pinned_below_graph(deps: &[Dep], lock: &Path, rel: &str) -> Vec<Finding> {
    let versions = tree::lock_versions(lock);
    let mut out = Vec::new();
    for dep in deps {
        let Some((_, held)) = versions.iter().find(|(n, _)| *n == dep.name) else {
            continue;
        };
        if held.len() < 2 {
            continue;
        }
        let admitted: Vec<&str> = held
            .iter()
            .map(String::as_str)
            .filter(|v| semver::req_allows(&dep.req, v) == Some(true))
            .collect();
        let refused: Vec<&str> = held
            .iter()
            .map(String::as_str)
            .filter(|v| semver::req_allows(&dep.req, v) == Some(false))
            .collect();
        let (Some(mine), Some(theirs)) = (semver::newest(&admitted), semver::newest(&refused))
        else {
            continue;
        };
        if semver::parse_version(mine) >= semver::parse_version(theirs) {
            continue;
        }
        out.push(Finding {
            severity: "pinned-below-graph",
            name: dep.name.clone(),
            detail: format!(
                "declared {}, so the graph carries {theirs} alongside {mine}: two majors of one crate because of this pin. Move the requirement, or say why in the manifest.",
                py_repr(&dep.req)
            ),
            place: format!("{rel} [{}]", dep.section),
        });
    }
    out
}

/// The severities that fail the run without `--strict`.
const FATAL: [&str; 4] = [
    "pinned-below-graph",
    "major-not-in-ratchet",
    "ratchet-unreasoned",
    "ratchet-stale",
];

/// One `--ratchet` line: the crate held, why, and where it is written.
pub struct Entry {
    name: String,
    reason: String,
    place: String,
}

/// The ratchet's lines: `name  # reason`; blank lines and whole-line `#` comments are skipped.
#[must_use]
pub fn parse_ratchet(text: &str, file: &str) -> Vec<Entry> {
    text.lines()
        .enumerate()
        .filter(|(_, l)| !l.trim().is_empty() && !l.trim_start().starts_with('#'))
        .map(|(i, l)| {
            let (name, reason) = l.split_once('#').unwrap_or((l, ""));
            Entry {
                name: name.trim().to_owned(),
                reason: reason.trim().to_owned(),
                place: format!("{file}:{}", i + 1),
            }
        })
        .collect()
}

fn ratchet_finding(severity: &'static str, e: &Entry, detail: &str) -> Finding {
    Finding {
        severity,
        name: e.name.clone(),
        detail: detail.to_owned(),
        place: e.place.clone(),
    }
}

/// The ratchet's own verdicts: untriaged majors, entries with no reason, and -- when every
/// answer it needs came back -- entries whose crate is no longer behind a major.
fn judge_ratchet(rep: &mut Report, entries: &[Entry], answered: Option<&[String]>) {
    let mut out: Vec<Finding> = rep
        .findings
        .iter()
        .filter(|f| f.severity == "major-behind" && !entries.iter().any(|e| e.name == f.name))
        .map(|f| Finding {
            severity: "major-not-in-ratchet",
            name: f.name.clone(),
            detail: format!(
                "{}; not in the ratchet -- triage it there, or move it",
                f.detail
            ),
            place: f.place.clone(),
        })
        .collect();
    for e in entries.iter().filter(|e| e.reason.is_empty()) {
        out.push(ratchet_finding(
            "ratchet-unreasoned",
            e,
            "held with no reason -- write `name  # why it may stand`",
        ));
    }
    match answered {
        None => rep
            .notes
            .push("offline: ratchet entries not judged for staleness".to_owned()),
        Some(answered) => {
            for e in entries {
                let behind = rep
                    .findings
                    .iter()
                    .any(|f| f.severity == "major-behind" && f.name == e.name);
                let unreachable = rep
                    .notes
                    .iter()
                    .any(|n| n.starts_with(&format!("{}: ", e.name)));
                if !behind && !unreachable {
                    let why = if answered.contains(&e.name) {
                        "no longer behind a major"
                    } else {
                        "not a direct dependency of this tree"
                    };
                    out.push(ratchet_finding(
                        "ratchet-stale",
                        e,
                        &format!("{why} -- delete the entry, so the list can only shrink"),
                    ));
                }
            }
        }
    }
    rep.findings.extend(out);
}

fn currency(unique: &[(String, String)]) -> (Vec<Finding>, Vec<String>) {
    let names: Vec<String> = unique.iter().map(|(n, _)| n.clone()).collect();
    let answers = cratesio::latest_many(&names);
    let (mut findings, mut notes) = (Vec::new(), Vec::new());
    for (name, held) in unique {
        let Some(latest) = answers.get(name).cloned().flatten() else {
            notes.push(format!("{name}: crates.io unreachable, not checked"));
            continue;
        };
        let (Some(cur), Some(lat)) = (semver::parse_version(held), semver::parse_version(&latest))
        else {
            continue;
        };
        if cur >= lat {
            continue;
        }
        let major = semver::compat_class(&cur) < semver::compat_class(&lat);
        findings.push(Finding {
            severity: if major {
                "major-behind"
            } else {
                "minor-behind"
            },
            name: name.clone(),
            detail: format!(
                "locked {held}, latest stable {latest}{}",
                if major {
                    "  (MAJOR behind: moves in its own commit)"
                } else {
                    ""
                }
            ),
            place: "tree [tree]".to_owned(),
        });
    }
    (findings, notes)
}

fn run(root: &Path, offline: bool, ratchet: Option<&[Entry]>) -> Report {
    let mut rep = Report::default();
    let manifests = tree::manifests(root);
    for manifest in &manifests {
        let Some(doc) = tree::read_manifest(manifest) else {
            continue;
        };
        let rel = manifest
            .strip_prefix(root)
            .unwrap_or(manifest)
            .to_string_lossy()
            .into_owned();
        rep.manifests_seen += 1;
        let deps = tree::declared_deps(&doc, manifest);
        rep.examined += deps.len();
        if let Some(lock) = tree::nearest_lock(manifest) {
            rep.findings.extend(pinned_below_graph(&deps, &lock, &rel));
        }
    }
    if offline {
        rep.notes
            .push("offline: currency not checked (arm 2 skipped)".to_owned());
        if let Some(entries) = ratchet {
            judge_ratchet(&mut rep, entries, None);
        }
        return rep;
    }
    let mut unique: Vec<(String, String)> = Vec::new();
    for manifest in &manifests {
        let (Some(doc), Some(lock)) = (tree::read_manifest(manifest), tree::nearest_lock(manifest))
        else {
            continue;
        };
        let versions = tree::lock_versions(&lock);
        for dep in tree::declared_deps(&doc, manifest) {
            if unique.iter().any(|(n, _)| *n == dep.name) {
                continue;
            }
            if let Some((_, held)) = versions.iter().find(|(n, _)| *n == dep.name) {
                let held: Vec<&str> = held.iter().map(String::as_str).collect();
                if let Some(newest) = semver::newest(&held) {
                    unique.push((dep.name.clone(), newest.to_owned()));
                }
            }
        }
    }
    let (findings, notes) = currency(&unique);
    rep.findings.extend(findings);
    rep.checked_currency = !notes.iter().any(|n| n.contains("unreachable"));
    rep.notes.extend(notes);
    if let Some(entries) = ratchet {
        let answered: Vec<String> = unique.iter().map(|(n, _)| n.clone()).collect();
        judge_ratchet(&mut rep, entries, Some(&answered));
    }
    rep
}

fn report(rep: &Report, root: &Path, offline: bool, strict: bool, json: bool) -> (i32, String) {
    if rep.examined == 0 {
        let why = if rep.manifests_seen == 0 {
            "no manifest found".to_owned()
        } else {
            format!(
                "{} manifest(s), none declaring a dependency",
                rep.manifests_seen
            )
        };
        let msg = format!(
            "not applicable: {why} under {} -- nothing to check",
            root.display()
        );
        if json {
            let doc = serde_json::json!({ "examined": 0, "not_applicable": msg, "fatal": [] });
            return (0, format!("{}\n", crate::pyjson::dumps_indent2(&doc)));
        }
        return (0, format!("{STEP} {msg}\n"));
    }
    let by = |sev: &str| {
        rep.findings
            .iter()
            .filter(|f| f.severity == sev)
            .cloned()
            .collect::<Vec<_>>()
    };
    let mut fatal: Vec<Finding> = rep
        .findings
        .iter()
        .filter(|f| FATAL.contains(&f.severity))
        .cloned()
        .collect();
    let (majors, minors) = (by("major-behind"), by("minor-behind"));
    if strict {
        fatal.extend(majors.iter().cloned());
    }
    let code = i32::from(!fatal.is_empty());
    if json {
        let list = |v: &[Finding]| Value::Array(v.iter().map(Finding::json).collect());
        let doc = serde_json::json!({
            "examined": rep.examined,
            "currency_checked": rep.checked_currency,
            "fatal": list(&fatal),
            "major_behind": list(&majors),
            "minor_behind": list(&minors),
            "notes": rep.notes,
        });
        return (code, format!("{}\n", crate::pyjson::dumps_indent2(&doc)));
    }
    let mut s = format!(
        "{STEP} {} manifest(s), {} direct dependency declaration(s) examined\n",
        rep.manifests_seen, rep.examined
    );
    for f in &fatal {
        let _ = writeln!(
            s,
            "{ERR} [{}] {}: {}  ({})",
            f.severity, f.name, f.detail, f.place
        );
    }
    for f in &majors {
        let _ = writeln!(
            s,
            "{WARN} [major behind] {}: {}  ({})",
            f.name, f.detail, f.place
        );
    }
    for f in &minors {
        let _ = writeln!(s, "{STEP} [drift] {}: {}  ({})", f.name, f.detail, f.place);
    }
    let mut seen: Vec<&String> = Vec::new();
    for n in &rep.notes {
        if !seen.contains(&n) {
            seen.push(n);
            let _ = writeln!(s, "{WARN} {n}");
        }
    }
    if !rep.checked_currency && !offline {
        let _ = writeln!(
            s,
            "{WARN} currency NOT fully checked — this is not a clean bill"
        );
    }
    if fatal.is_empty() {
        let _ = writeln!(s, "\n{OK} no dependency pinned below the graph");
        if !majors.is_empty() {
            let _ = writeln!(
                s,
                "  ({} major(s) behind, reported not failed — --strict to make them fatal)",
                majors.len()
            );
        }
    } else {
        let _ = writeln!(s, "\n{ERR} dependency currency: {} finding(s)", fatal.len());
    }
    (code, s)
}

/// `goh deps [--root R] [--json] [--offline] [--strict] [--ratchet FILE]`.
#[must_use]
pub fn run_command(
    root: Option<&str>,
    json: bool,
    offline: bool,
    strict: bool,
    ratchet: Option<&str>,
) -> i32 {
    let root = root.map_or_else(
        || std::env::current_dir().unwrap_or_default(),
        |r| {
            Path::new(r)
                .canonicalize()
                .unwrap_or_else(|_| PathBuf::from(r))
        },
    );
    let triaged: Option<Vec<Entry>> = match ratchet {
        None => None,
        Some(file) => match std::fs::read_to_string(file) {
            Ok(text) => Some(parse_ratchet(&text, file)),
            Err(e) => {
                goh_tui::eoutln!("{ERR} [dep_currency] cannot read the ratchet {file}: {e}");
                return 2;
            }
        },
    };
    let rep = run(&root, offline, triaged.as_deref());
    let (code, out) = report(&rep, &root, offline, strict, json);
    goh_tui::out(&out);
    code
}
