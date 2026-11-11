//! One workflow's runner labels and action references judged, with the reasoned escape applied.
//!
//! A YAML parser decides what is a reference (`saphyr`, with source spans): a `uses:` inside a
//! `run: |` block is script text, a matrix list is a list of values, an alias resolves to what it
//! names. Comments are not in the parse, so the escape `# workflow-pin-ok: <reason>` is read from
//! the source lines, on the reference's line or in the comment lines directly above it.

use crate::movingtag::scan::{covering, marker_in};
use saphyr::{LoadableYamlNode, MarkedYaml, Scalar, YamlData};

pub use crate::checkoutcreds::scan::in_scope;

/// The per-reference escape: `# workflow-pin-ok: <reason>`.
pub const MARKER: &str = "workflow-pin-ok:";

/// The hosted runner families whose `-latest` label GitHub re-points to a new image.
const FAMILIES: [&str; 3] = ["ubuntu", "macos", "windows"];

/// What one finding is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Problem {
    /// `uses: owner/repo` with no `@ref` at all.
    NoRef(String),
    /// An action on a ref its owner moves: `(uses, ref)` -- a branch, or a major-only tag.
    MovingAction(String, String),
    /// A `*-latest` runner label: GitHub re-points it to a new OS image.
    MovingRunner(String),
    /// The marker, with no reason after it.
    EmptyReason,
    /// The marker, with no moving reference under it: it exempts nothing.
    StaleMarker,
    /// Not YAML the check can read -- nothing in it can be shown pinned.
    Unparsable(String),
}

/// One finding: the 1-based line and what is wrong there.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub line: usize,
    pub problem: Problem,
}

/// One file's verdict.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Scan {
    pub findings: Vec<Finding>,
    /// Action references and runner labels read (the scanner's subject).
    pub refs: usize,
    /// Actions pinned by a commit id.
    pub shas: usize,
    /// Actions on a full release tag (`v7.0.1`).
    pub releases: usize,
    /// Moving, and exempted with a reason.
    pub exempt: usize,
    /// Carried by an expression this check cannot resolve.
    pub unreadable: usize,
}

/// What a `uses:` value is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Use {
    /// A path in this repo (`./.github/actions/x`): pinned by the commit that holds it.
    Local,
    /// `docker://...`: judged by `goh moving-tags`.
    Docker,
    /// Built from an expression.
    Unreadable,
    /// A commit id.
    Sha,
    /// A full release tag.
    Released,
    /// No `@ref`.
    NoRef,
    /// A ref its owner moves.
    Moving(String),
}

fn all_hex(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(|b| b.is_ascii_hexdigit())
}

/// A full release tag: `MAJOR.MINOR.PATCH`, a leading `v` and a `-pre` / `+build` tail allowed.
/// `v4` and `v4.1` name a channel, not a release: their maintainer re-points them.
#[must_use]
pub fn is_release(r: &str) -> bool {
    let r = r.strip_prefix('v').unwrap_or(r);
    let core = r.split(['-', '+']).next().unwrap_or(r);
    let parts: Vec<&str> = core.split('.').collect();
    parts.len() == 3
        && parts
            .iter()
            .all(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()))
}

/// Judge one `uses:` value.
#[must_use]
pub fn judge_uses(value: &str) -> Use {
    let v = value.trim();
    if v.starts_with("./") || v.starts_with("../") {
        return Use::Local;
    }
    if v.starts_with("docker://") {
        return Use::Docker;
    }
    if v.contains("${{") {
        return Use::Unreadable;
    }
    let Some((_, r)) = v.rsplit_once('@') else {
        return Use::NoRef;
    };
    if matches!(r.len(), 40 | 64) && all_hex(r) {
        Use::Sha
    } else if is_release(r) {
        Use::Released
    } else {
        Use::Moving(r.to_owned())
    }
}

/// Whether `label` is a hosted runner label that moves: `ubuntu-latest`, `macos-latest-xlarge`.
#[must_use]
pub fn is_moving_runner(label: &str) -> bool {
    let l = label.trim().to_ascii_lowercase();
    FAMILIES.iter().any(|f| {
        l.strip_prefix(f)
            .and_then(|rest| rest.strip_prefix("-latest"))
            .is_some_and(|tail| tail.is_empty() || tail.starts_with('-'))
    })
}

/// The moving label a scalar holds: the whole value, or (in an expression) a word of it.
fn moving_label(s: &str) -> Option<String> {
    if is_moving_runner(s) {
        return Some(s.trim().to_owned());
    }
    if !s.contains("${{") {
        return None;
    }
    s.split(|c: char| !(c.is_ascii_alphanumeric() || c == '-'))
        .find(|w| is_moving_runner(w))
        .map(str::to_owned)
}

fn str_of<'a>(node: &'a MarkedYaml) -> Option<&'a str> {
    match &node.data {
        YamlData::Value(Scalar::String(s)) => Some(s),
        _ => None,
    }
}

struct Walk {
    /// `(marker-search start line, finding line, problem)`.
    raw: Vec<(usize, usize, Problem)>,
    out: Scan,
}

impl Walk {
    fn add(&mut self, start: usize, line: usize, problem: Problem) {
        // An alias resolves to its anchor's node: the same reference, found twice at one line.
        let seen = self.raw.iter().any(|(_, l, p)| *l == line && *p == problem);
        if !seen {
            self.raw.push((start, line, problem));
        }
    }

    fn uses(&mut self, key: &MarkedYaml, value: &MarkedYaml, v: &str) {
        let start = key.span.start.line().max(1);
        let line = value.span.start.line().max(start);
        match judge_uses(v) {
            Use::Local | Use::Docker => {}
            Use::Unreadable => {
                self.out.refs += 1;
                self.out.unreadable += 1;
            }
            Use::Sha => {
                self.out.refs += 1;
                self.out.shas += 1;
            }
            Use::Released => {
                self.out.refs += 1;
                self.out.releases += 1;
            }
            Use::NoRef => {
                self.out.refs += 1;
                self.add(start, line, Problem::NoRef(v.trim().to_owned()));
            }
            Use::Moving(r) => {
                self.out.refs += 1;
                self.add(start, line, Problem::MovingAction(v.trim().to_owned(), r));
            }
        }
    }

    fn scalar(&mut self, node: &MarkedYaml, s: &str, in_runs_on: bool) {
        let line = node.span.start.line().max(1);
        if in_runs_on {
            self.out.refs += 1;
        }
        if let Some(label) = moving_label(s) {
            self.add(line, line, Problem::MovingRunner(label));
        }
    }

    fn walk(&mut self, node: &MarkedYaml, in_runs_on: bool) {
        match &node.data {
            YamlData::Mapping(m) => {
                for (key, value) in m {
                    let name = str_of(key);
                    if name == Some("uses") {
                        if let Some(v) = str_of(value) {
                            self.uses(key, value, v);
                            continue;
                        }
                    }
                    self.walk(value, in_runs_on || name == Some("runs-on"));
                }
            }
            YamlData::Sequence(items) => {
                for item in items {
                    self.walk(item, in_runs_on);
                }
            }
            YamlData::Value(Scalar::String(s)) => self.scalar(node, s, in_runs_on),
            _ => {}
        }
    }
}

/// Judge every runner label and action reference in the workflow `text`.
#[must_use]
pub fn scan(text: &str) -> Scan {
    let docs = match MarkedYaml::load_from_str(text) {
        Ok(docs) => docs,
        Err(e) => {
            return Scan {
                findings: vec![Finding {
                    line: e.marker().line().max(1),
                    problem: Problem::Unparsable(e.to_string()),
                }],
                ..Scan::default()
            }
        }
    };
    let mut w = Walk {
        raw: Vec::new(),
        out: Scan::default(),
    };
    for doc in &docs {
        w.walk(doc, false);
    }
    let lines: Vec<&str> = text.lines().collect();
    let mut used = std::collections::BTreeSet::new();
    let Walk { raw, mut out } = w;
    for (start, line, problem) in raw {
        match covering(&lines, MARKER, start, line) {
            Some((at, reason)) => {
                used.insert(at);
                if reason.is_empty() {
                    out.findings.push(Finding {
                        line,
                        problem: Problem::EmptyReason,
                    });
                } else {
                    out.exempt += 1;
                }
            }
            None => out.findings.push(Finding { line, problem }),
        }
    }
    for (i, l) in lines.iter().enumerate() {
        if marker_in(l, MARKER).is_some() && !used.contains(&(i + 1)) {
            out.findings.push(Finding {
                line: i + 1,
                problem: Problem::StaleMarker,
            });
        }
    }
    out.findings.sort_by_key(|f| f.line);
    out
}
