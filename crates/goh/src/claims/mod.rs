//! Re-derive every MARKED number in a repo's prose from the tree it
//! describes -- Rust port of the retired `checks/check_claim_derivation.py` (Phase N1).
//!
//! A number in prose cannot watch itself (`roadmap_state.py` said "64
//! declared gates" against 70). A marked claim -- `` `N` unit in `PATH` `` or
//! `claim: N unit in PATH` -- is re-derived on every run and a stale one is a
//! finding that prints the command producing the truth. Exemptions are a
//! ratchet in `claim_derivation_allow.json`; opt in with
//! `GOH_CLAIM_DERIVATION`. The grammar is `text`, the derivations `derive`.

pub mod derive;
mod report;
pub use report::parse_claims_command;
pub mod text;

use std::collections::BTreeSet;
use std::path::Path;

use serde_json::Value;

const ALLOW_FILE: &str = "claim_derivation_allow.json";
const STATUSES: [&str; 2] = ["legitimate", "unreviewed"];
const OPT_IN_KEY: &str = "GOH_CLAIM_DERIVATION";
const MAX_REPORTED: usize = 40;

/// One reported line: (to stderr?, text).
type Line = (bool, String);

fn err(out: &mut Vec<Line>, m: &str) {
    out.push((true, format!("✗ {m}")));
}
fn info(out: &mut Vec<Line>, m: &str) {
    out.push((false, format!("→ {m}")));
}
fn step(out: &mut Vec<Line>, m: &str) {
    out.push((false, format!("· {m}")));
}

/// Has this repo opted in, by its own `.gatesrc` (the reference's parse)?
fn adopted(root: &Path, staged: bool) -> bool {
    let Some(blob) = crate::gitutil::content_bytes(root, ".gatesrc", staged) else {
        return false;
    };
    let text = String::from_utf8_lossy(&blob);
    for raw in crate::mdtext::splitlines(&text) {
        let mut line = raw.trim();
        if let Some(rest) = line.strip_prefix("export ") {
            line = rest.trim_start();
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        if key.trim() != OPT_IN_KEY {
            continue;
        }
        let mut value = value.trim().to_owned();
        if let Some(q) = ['\'', '"'].into_iter().find(|q| value.starts_with(*q)) {
            value = value[1..].split(q).next().unwrap_or("").to_owned();
        } else {
            for cut in [" #", "\t#"] {
                if let Some(i) = value.find(cut) {
                    value.truncate(i);
                }
            }
        }
        if !value.trim().is_empty() {
            return true;
        }
    }
    false
}

/// The allowlist, raw entries kept (the JSON report prints stale ones as read).
fn load_allow(root: &Path, staged: bool) -> (Vec<Value>, Vec<String>) {
    let Some(blob) = crate::gitutil::content_bytes(root, ALLOW_FILE, staged) else {
        return (Vec::new(), Vec::new());
    };
    let data: Value = match std::str::from_utf8(&blob)
        .map_err(|e| e.to_string())
        .and_then(|t| serde_json::from_str(t).map_err(|e| e.to_string()))
    {
        Ok(v) => v,
        Err(e) => {
            return (
                Vec::new(),
                vec![format!("{ALLOW_FILE} is not valid JSON: {e}")],
            )
        }
    };
    let Some(entries) = data.get("entries").and_then(Value::as_array).cloned() else {
        return (
            Vec::new(),
            vec![format!("{ALLOW_FILE} must be {{\"entries\": [...]}}")],
        );
    };
    let field = |e: &Value, k: &str| {
        e.get(k)
            .and_then(Value::as_str)
            .is_some_and(|s| !s.trim().is_empty())
    };
    let mut problems = Vec::new();
    for (i, e) in entries.iter().enumerate() {
        if !e.is_object() || !["path", "claim", "reason"].iter().all(|k| field(e, k)) {
            problems.push(format!(
                "{ALLOW_FILE} entry {i} needs a non-empty path, claim and reason"
            ));
        } else if !e
            .get("status")
            .is_none_or(|s| s.as_str().is_some_and(|s| STATUSES.contains(&s)))
        {
            problems.push(format!(
                "{ALLOW_FILE} entry {i}: status must be one of {}",
                STATUSES.join(", ")
            ));
        }
    }
    (entries, problems)
}

fn key(text: &str) -> String {
    text.split_whitespace().collect()
}

/// `(rel, claim, value or None, command-or-reason)` for every marked claim.
type Scanned = (String, text::Claim, Option<u64>, String);
/// `(rel, claim, kind, message, command, the value the tree gave)`.
type Finding = (
    String,
    text::Claim,
    &'static str,
    String,
    Option<String>,
    Option<u64>,
);

struct Verdict {
    findings: Vec<Finding>,
    stale: Vec<Value>,
    problems: Vec<String>,
    notes: Vec<String>,
    examined: usize,
    claims: usize,
    excused: usize,
    debt: usize,
}

impl Verdict {
    const fn refusal(problems: Vec<String>, notes: Vec<String>, examined: usize) -> Self {
        Self {
            findings: Vec::new(),
            stale: Vec::new(),
            problems,
            notes,
            examined,
            claims: 0,
            excused: 0,
            debt: 0,
        }
    }
}

fn scan(
    grammar: &text::Grammar,
    root: &Path,
    files: &[String],
    staged: bool,
) -> (Vec<Scanned>, usize) {
    let mut out = Vec::new();
    let mut read = 0usize;
    for rel in files {
        let Some(blob) = crate::gitutil::content_bytes(root, rel, staged) else {
            continue;
        };
        if blob[..blob.len().min(8000)].contains(&0) {
            continue;
        }
        read += 1;
        for claim in grammar.claims_in_file(rel, &String::from_utf8_lossy(&blob)) {
            if let Some(why) = claim.malformed.clone() {
                out.push((rel.clone(), claim, None, why));
                continue;
            }
            match derive::derive(
                root,
                staged,
                claim.kind,
                &claim.target,
                &claim.name,
                claim.glob.as_deref(),
            ) {
                Ok((value, command)) => out.push((rel.clone(), claim, Some(value), command)),
                Err(why) => out.push((rel.clone(), claim, None, why)),
            }
        }
    }
    (out, read)
}

/// `(findings, stale entries, excused, unreviewed debt)` -- the ratchet.
fn judge(
    root: &Path,
    scanned: &[Scanned],
    entries: &[Value],
    files: &[String],
) -> (Vec<Finding>, Vec<Value>, usize, usize) {
    let files_set: BTreeSet<&str> = files.iter().map(String::as_str).collect();
    let mut used: BTreeSet<usize> = BTreeSet::new();
    let (mut findings, mut excused) = (Vec::new(), 0usize);
    for (rel, claim, value, detail) in scanned {
        let k = key(&claim.text);
        let hit = entries.iter().position(|e| {
            e.get("path").and_then(Value::as_str) == Some(rel.as_str())
                && key(e.get("claim").and_then(Value::as_str).unwrap_or("")) == k
        });
        if let Some(i) = hit {
            used.insert(i);
            excused += 1;
            continue;
        }
        match value {
            None => findings.push((
                rel.clone(),
                claim.clone(),
                "UNRESOLVED",
                detail.clone(),
                None,
                None,
            )),
            Some(v) if *v != claim.number => findings.push((
                rel.clone(),
                claim.clone(),
                "STALE",
                format!("claims {}, the tree says {v}", claim.number),
                Some(detail.clone()),
                Some(*v),
            )),
            Some(_) => {}
        }
    }
    let stale: Vec<Value> = entries
        .iter()
        .enumerate()
        .filter(|(i, e)| {
            !used.contains(i)
                // In scope, or GONE: an entry for a file that no longer exists excuses nothing at
                // any scope and would excuse the next file to take the path.
                && e.get("path")
                    .and_then(Value::as_str)
                    .is_some_and(|p| files_set.contains(p) || !root.join(p).exists())
        })
        .map(|(_, e)| e.clone())
        .collect();
    let debt = used
        .iter()
        .filter(|i| {
            entries[**i]
                .get("status")
                .is_none_or(|s| s.as_str() == Some("unreviewed"))
        })
        .count();
    (findings, stale, excused, debt)
}

fn check(root: &Path, exclude: Option<&str>, staged: bool) -> Result<Verdict, String> {
    let pattern = match exclude.filter(|e| !e.is_empty()) {
        Some(e) => {
            Some(regex::Regex::new(e).map_err(|x| format!("bad --exclude regex '{e}': {x}"))?)
        }
        None => None,
    };
    let grammar = text::Grammar::new()?;
    let files: Vec<String> = crate::gitutil::listed_files(root, staged)?
        .into_iter()
        .filter(|f| text::scannable(f) && !pattern.as_ref().is_some_and(|p| p.is_match(f)))
        .collect();
    if files.is_empty() {
        if staged {
            return Ok(Verdict::refusal(
                Vec::new(),
                vec!["nothing staged — 0 text files to re-derive".to_owned()],
                0,
            ));
        }
        return Ok(Verdict::refusal(
            vec!["no text file in scope — refusing to report clean over zero files".to_owned()],
            Vec::new(),
            0,
        ));
    }
    let (entries, problems) = load_allow(root, staged);
    if !problems.is_empty() {
        return Ok(Verdict::refusal(problems, Vec::new(), files.len()));
    }
    let (scanned, examined) = scan(&grammar, root, &files, staged);
    let (findings, stale, excused, debt) = judge(root, &scanned, &entries, &files);
    if !staged && scanned.is_empty() && adopted(root, staged) {
        return Ok(Verdict {
            problems: vec![format!(
                "{OPT_IN_KEY}=1 and no marked claim in the tree: the convention is adopted and no \
                 number is under it, so this gate is inspecting nothing. Mark a claim (`claim: N \
                 lines in path`), or turn the key off in .gatesrc."
            )],
            examined,
            excused,
            debt,
            ..Verdict::refusal(Vec::new(), Vec::new(), examined)
        });
    }
    Ok(Verdict {
        findings,
        stale,
        problems: Vec::new(),
        notes: Vec::new(),
        examined,
        claims: scanned.len(),
        excused,
        debt,
    })
}

/// (exit code, report lines).
fn run(root: Option<&str>, staged: bool, exclude: Option<&str>, json: bool) -> (i32, Vec<Line>) {
    let root = root.map_or_else(
        || crate::gitutil::repo_root().unwrap_or_else(|| ".".to_owned()),
        str::to_owned,
    );
    let root_path = Path::new(&root);
    if !root_path.is_dir() {
        return (
            2,
            vec![(
                true,
                format!("✗ [claim_derivation] not a directory: {root}"),
            )],
        );
    }
    match check(root_path, exclude, staged) {
        Err(e) => (2, vec![(true, format!("✗ [claim_derivation] {e}"))]),
        Ok(v) if json => {
            let (code, text) = report::json(&v);
            (code, vec![(false, text.trim_end_matches('\n').to_owned())])
        }
        Ok(v) => report::text(&v, root_path, staged),
    }
}

/// `goh claim-derivation [--root R] [--staged] [--exclude RE] [--json]`.
#[must_use]
pub fn run_command(root: Option<&str>, staged: bool, exclude: Option<&str>, json: bool) -> i32 {
    let (code, lines) = run(root, staged, exclude, json);
    for (to_err, line) in lines {
        if to_err {
            eprintln!("{line}");
        } else {
            println!("{line}");
        }
    }
    code
}

/// The structural step (opt-in, `GOH_CLAIM_DERIVATION`); the delegated step's label.
#[must_use]
pub fn step_gate(cfg: &crate::gatesrc::Gatesrc, staged: bool) -> Option<i32> {
    if !crate::gatesrc::opt_in(cfg, "GOH_CLAIM_DERIVATION") {
        return None;
    }
    let label = if staged {
        "prose claims are derived (staged)"
    } else {
        "prose claims are derived"
    };
    let start = crate::step_report::begin(label);
    let exclude = (!cfg.exclude.is_empty()).then_some(cfg.exclude.as_str());
    match run(None, staged, exclude, false) {
        (0, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, lines) => {
            let text: String = lines.into_iter().map(|(_, l)| l + "\n").collect();
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported("crates/goh/src/claims/mod.rs"),
                &text,
                start,
            );
            Some(code)
        }
    }
}
