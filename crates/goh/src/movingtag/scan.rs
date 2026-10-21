//! One file's image references judged, with the reasoned exemption applied.
//!
//! The references come from `dockerfile.rs` (a Dockerfile or Containerfile) or `yaml.rs` (every
//! `*.yml`/`*.yaml`); `reference.rs` judges each. The one escape is
//! `# moving-tag-ok: <reason>` on the reference's line or in the comment lines directly above it;
//! an empty reason and a marker with no moving reference under it are findings.

use super::reference::{verdict, Verdict};
use super::{dockerfile, yaml};

/// The per-reference exemption: `# moving-tag-ok: <reason>`.
pub const MARKER: &str = "moving-tag-ok:";

/// One image reference: the lines it spans (1-based, inclusive) and its text, substituted.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Ref {
    pub start: usize,
    pub end: usize,
    pub raw: String,
}

/// Whether `rel` is read: a Dockerfile/Containerfile, or YAML.
#[must_use]
pub fn in_scope(rel: &str) -> bool {
    dockerfile::is_dockerfile(rel) || is_yaml(rel)
}

fn is_yaml(rel: &str) -> bool {
    std::path::Path::new(rel)
        .extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| e.eq_ignore_ascii_case("yml") || e.eq_ignore_ascii_case("yaml"))
}

/// What one finding is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Problem {
    /// No tag: whatever `latest` points at.
    NoTag(String),
    /// A moving tag: `(reference, tag)`.
    Moving(String, String),
    /// The marker, with no reason after it.
    EmptyReason,
    /// The marker, with no moving reference under it: it exempts nothing.
    StaleMarker,
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
    /// Image references found (the scanner's subject).
    pub refs: usize,
    /// Pinned by digest.
    pub digests: usize,
    /// Pinned by a commit id: the other pin nothing can move.
    pub commits: usize,
    /// Moving, and exempted with a reason.
    pub exempt: usize,
    /// Carried by a variable or template this check cannot resolve.
    pub unreadable: usize,
    /// YAML the parser refused, read line by line instead.
    pub unparsed: bool,
}

/// `value` up to its YAML comment: a `#` at the start or after a blank, outside quotes.
#[must_use]
pub fn strip_comment(value: &str) -> &str {
    crate::checkoutcreds::scan::comment_of(value).map_or(value, |c| &value[..value.len() - c.len()])
}

fn is_comment(line: &str) -> bool {
    line.trim_start().starts_with('#')
}

/// The marker on `line`, if any: its reason (possibly empty).
fn marker_on(line: &str) -> Option<&str> {
    let at = line.find(MARKER)?;
    line[..at]
        .contains('#')
        .then(|| line[at + MARKER.len()..].trim())
}

/// The marker covering lines `start..=end` (1-based): on them, or in the comment lines directly
/// above `start`. Returns its line and reason.
fn covering_marker<'a>(lines: &[&'a str], start: usize, end: usize) -> Option<(usize, &'a str)> {
    for n in start..=end.min(lines.len()) {
        if let Some(reason) = marker_on(lines[n - 1]) {
            return Some((n, reason));
        }
    }
    let mut n = start.saturating_sub(1);
    while n >= 1 && is_comment(lines[n - 1]) {
        if let Some(reason) = marker_on(lines[n - 1]) {
            return Some((n, reason));
        }
        n -= 1;
    }
    None
}

/// Judge every image reference in the file `rel` holding `text`.
#[must_use]
pub fn scan(rel: &str, text: &str) -> Scan {
    let (refs, parsed) = if dockerfile::is_dockerfile(rel) {
        (dockerfile::refs(text), true)
    } else {
        yaml::refs(rel, text)
    };
    let lines: Vec<&str> = text.lines().collect();
    let mut out = Scan {
        unparsed: !parsed,
        ..Scan::default()
    };
    let mut used = std::collections::BTreeSet::new();
    for r in refs {
        let problem = match verdict(&r.raw) {
            Verdict::NotAReference => continue,
            Verdict::Unreadable => {
                out.refs += 1;
                out.unreadable += 1;
                continue;
            }
            Verdict::Digest => {
                out.refs += 1;
                out.digests += 1;
                continue;
            }
            Verdict::Commit => {
                out.refs += 1;
                out.commits += 1;
                continue;
            }
            Verdict::Released => {
                out.refs += 1;
                continue;
            }
            Verdict::NoTag => Problem::NoTag(r.raw.trim().to_owned()),
            Verdict::Moving(tag) => Problem::Moving(r.raw.trim().to_owned(), tag),
        };
        out.refs += 1;
        match covering_marker(&lines, r.start, r.end) {
            Some((at, reason)) => {
                used.insert(at);
                if reason.is_empty() {
                    out.findings.push(Finding {
                        line: r.start,
                        problem: Problem::EmptyReason,
                    });
                } else {
                    out.exempt += 1;
                }
            }
            None => out.findings.push(Finding {
                line: r.start,
                problem,
            }),
        }
    }
    for (i, line) in lines.iter().enumerate() {
        if marker_on(line).is_some() && !used.contains(&(i + 1)) {
            out.findings.push(Finding {
                line: i + 1,
                problem: Problem::StaleMarker,
            });
        }
    }
    out.findings.sort_by_key(|f| f.line);
    out
}
