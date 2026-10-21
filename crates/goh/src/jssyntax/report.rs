//! The report: the scope, the engine, and every finding.
//!
//! WHY THE SCOPE IS PRINTED, EVERY TIME. A gate that measured nothing and a
//! gate that measured everything and found nothing wrong print the same word
//! otherwise, and an exclusion can hide the subject -- so the count, the shape
//! of the paths, and every path the vendor and build list took out of the
//! scope are all here, in the open.

use std::fmt::Write as _;

use super::{Outcome, Scope, EXTENSIONS, TAG};

/// The report: the scope, the engine, and every finding.
#[must_use]
pub fn format_report(outcome: &Outcome) -> String {
    let mut out = String::new();
    let _ = writeln!(
        out,
        "{TAG} scope: {} tracked JavaScript file(s); shape: {}",
        outcome.scope.tracked,
        shape_text(&outcome.scope)
    );
    if !outcome.scope.excluded.is_empty() {
        let _ = writeln!(
            out,
            "{TAG} excluded {} file(s) as vendored or built output: {}",
            outcome.scope.excluded.len(),
            outcome.scope.excluded.join(", ")
        );
    }
    let _ = writeln!(out, "{TAG} engine: {}", outcome.engine);
    if outcome.findings.is_empty() && outcome.unmeasured.is_empty() {
        let _ = write!(out, "{TAG} OK — {} file(s) parsed clean", outcome.judged);
        if outcome.templates > 0 {
            let _ = write!(
                out,
                "; {} template(s) judged by their substituted form",
                outcome.templates
            );
        }
        out.push('\n');
        return out;
    }
    for finding in &outcome.findings {
        let _ = writeln!(
            out,
            "{TAG} {}:{}: {}",
            finding.path,
            finding
                .line
                .map_or_else(|| "-".to_owned(), |n| n.to_string()),
            finding.reason
        );
    }
    for (path, why) in &outcome.unmeasured {
        let _ = writeln!(out, "{TAG} {path}: not measured — {why}");
    }
    out
}

/// The scope's shape, as one line.
fn shape_text(scope: &Scope) -> String {
    let shape = scope.shape();
    if shape.is_empty() {
        return "none".to_owned();
    }
    shape
        .iter()
        .map(|(name, count)| format!("{name} ({count})"))
        .collect::<Vec<_>>()
        .join(", ")
}

/// The report for a repository with no JavaScript at all.
#[must_use]
pub fn format_no_scope() -> String {
    format!(
        "{TAG} not applicable — this repository tracks no JavaScript source (looked for {})\n",
        EXTENSIONS.join(", ")
    )
}
