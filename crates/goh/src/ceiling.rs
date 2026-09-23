//! Line-cap exclusion ceilings — Rust port of the structural step's
//! `checks/check_exclusion_has_ceiling.py` use, plus the step orchestration
//! shared with the ratchet engine (`ratchet.rs`).
//!
//! Every file exempted from the line cap (`GOH_LINE_EXCLUDE`) is either
//! under the cap anyway, waived by nature (`GOH_LINE_UNBOUNDED`), or
//! carries a ceiling in the ratchet baseline — an exemption from the cap
//! is not an exemption from every bound.

use std::collections::BTreeSet;
use std::fmt::Write as _;

use crate::ratchet::{compare, format_ratchet, measure_loc, parse_baseline};

/// Paths carrying a ceiling.
///
/// Mirrors `baseline_keys`: the same two formats the ratchet reads, with
/// inline `#` comments stripped (the ceiling reader strips them; the
/// ratchet value parser does not — each side is mirrored exactly,
/// divergence included).
#[must_use]
pub fn baseline_keys(text: &str) -> BTreeSet<String> {
    if text.trim_start().starts_with('{') {
        if let Ok(serde_json::Value::Object(obj)) = serde_json::from_str::<serde_json::Value>(text)
        {
            return obj.keys().cloned().collect();
        }
        return BTreeSet::new();
    }
    text.lines()
        .map(|line| line.split('#').next().unwrap_or("").trim())
        .filter(|line| !line.is_empty())
        .filter_map(|line| {
            let raw: Vec<&str> = if line.contains('\t') {
                line.splitn(2, '\t').collect()
            } else {
                line.splitn(2, char::is_whitespace).collect()
            };
            (raw.len() == 2).then(|| raw[1].trim().to_owned())
        })
        .collect()
}

/// One exclusion-check finding: an exempt file over the cap with no ceiling.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Unceiled {
    /// Repo-relative path.
    pub path: String,
    /// Today's line count.
    pub lines: usize,
}

/// The exclusion check: every file matching `line_exclude` (minus
/// `unbounded` waivers) is under `max` or carries a ceiling in `have`.
///
/// Returns `(offenders, inspected, waived)`. Staleness is the caller's job:
/// a waiver matching nothing, or an exemption matching nothing, fails —
/// mirroring the reference exactly.
#[must_use]
pub fn check_exclusions(
    root: &std::path::Path,
    files: &[String],
    max: usize,
    line_exclude: &regex::Regex,
    unbounded: Option<&regex::Regex>,
    have: &BTreeSet<String>,
) -> (Vec<Unceiled>, usize, usize) {
    let mut offenders = Vec::new();
    let mut inspected = 0;
    let mut waived = 0;
    for rel in files {
        if !line_exclude.is_match(rel) {
            continue;
        }
        if let Some(waive) = unbounded {
            if waive.is_match(rel) {
                waived += 1;
                continue;
            }
        }
        inspected += 1;
        // Exempt from the cap but under it anyway: the cap would bind it
        // if the exemption were removed, so no ceiling is needed.
        let lines = crate::gitutil::content_bytes(root, rel, false)
            .map_or(0, |blob| crate::gitutil::line_count(&blob));
        if lines > max && !have.contains(rel) {
            offenders.push(Unceiled {
                path: rel.clone(),
                lines,
            });
        }
    }
    (offenders, inspected, waived)
}

/// The exclusion verdict block, mirroring the reference's tui text
/// (`err` → stderr with ✗, `info` → stdout with →, `ok` → stdout with ✓).
/// Returns `(ok, stdout_text, stderr_text)`.
#[must_use]
pub fn format_ceiling(
    offenders: &[Unceiled],
    inspected: usize,
    waived: usize,
    max: usize,
    baseline_name: &str,
    unbounded_active: bool,
) -> (bool, String, String) {
    if !offenders.is_empty() {
        let err = format!(
            "✗ [ceiling] {} file(s) exempt from the cap with NO ceiling\n",
            offenders.len()
        );
        let mut out = String::new();
        for hit in offenders {
            let _ = writeln!(
                out,
                "→ {} ({} lines) is in GOH_LINE_EXCLUDE, so the {max}-line cap does not apply, and is absent from {baseline_name}, so no ratchet ceiling applies either. It can grow without limit.",
                hit.path, hit.lines
            );
        }
        let _ = writeln!(
            out,
            "→ Fix: split it, or add '{} {}' to {baseline_name}.",
            offenders[0].lines, offenders[0].path
        );
        return (false, out, err);
    }
    if unbounded_active && waived == 0 {
        return (
            false,
            "→     nothing. Prune it, or fix the pattern; a waiver for material\n→     that is gone reads exactly like one that is doing its job.\n"
                .to_owned(),
            "✗ [ceiling] GOH_LINE_UNBOUNDED matched 0 tracked files — it waives\n".to_owned(),
        );
    }
    if inspected == 0 && waived == 0 {
        return (
            false,
            "→     list names paths that no longer exist. Prune it; an exemption for\n→     a file that is gone is indistinguishable from one that is working.\n"
                .to_owned(),
            "✗ [ceiling] GOH_LINE_EXCLUDE matched 0 tracked files — the exemption\n".to_owned(),
        );
    }
    let tail = if waived > 0 {
        format!(", {waived} waived as unbounded by nature")
    } else {
        String::new()
    };
    (
        true,
        format!(
            "✓ [ceiling] OK — {inspected} exempt file(s), each under the cap or carrying a ceiling{tail}\n"
        ),
        String::new(),
    )
}

/// Outcome of one check.
///
/// Carries the exit code plus the exact stdout/stderr split the reference
/// produces. `Skipped` means the step's conditions did not call for this
/// check at all (nothing prints, like the shell pipeline).
pub enum CheckOutcome {
    /// Nothing to run.
    Skipped,
    /// A warning that prints but never fails (the unconfigured-exemption
    /// branch), carried as stderr text.
    Warn(String),
    /// Ran: exit code, stdout text, stderr text. The ratchet prints passes
    /// to stdout and failures to stderr, exactly like the reference (whose
    /// OK line is a bare `print` and whose violations go to stderr).
    Ran {
        /// Exit code.
        code: i32,
        /// Stdout text.
        out: String,
        /// Stderr text.
        err: String,
    },
}

/// The exclusion half.
///
/// Every `line_exclude` file is under `max` or carries a ceiling in the
/// baseline. Mirrors the shell pipeline's conditions — without cap +
/// baseline configured there is no check (or a named warning when
/// exemptions exist but no baseline binds them).
#[must_use]
pub fn run_exclusion(
    root: &std::path::Path,
    files: &[String],
    max: Option<usize>,
    line_exclude: &str,
    line_unbounded: &str,
    line_baseline: Option<&str>,
) -> CheckOutcome {
    let (Some(baseline_name), Some(max)) = (line_baseline, max) else {
        if line_exclude.is_empty() {
            return CheckOutcome::Skipped;
        }
        return CheckOutcome::Warn(
            "⚠ GOH_LINE_EXCLUDE is set but GOH_LINE_BASELINE is not — an exempted file\n⚠   is bounded by nothing. Point GOH_LINE_BASELINE at the ratchet baseline.\n"
                .to_owned(),
        );
    };
    let baseline_path = root.join(baseline_name);
    if !baseline_path.is_file() {
        return CheckOutcome::Ran {
            code: 2,
            out: String::new(),
            err: format!(
                "✗ [ceiling] baseline {baseline_name} not found — fix GOH_LINE_BASELINE\n"
            ),
        };
    }
    if line_exclude.is_empty() {
        return CheckOutcome::Ran {
            code: 0,
            out:
                "✓ [ceiling] OK — no GOH_LINE_EXCLUDE entries, so nothing is exempt from the cap\n"
                    .to_owned(),
            err: String::new(),
        };
    }
    let line_exclude = match regex::Regex::new(line_exclude) {
        Ok(rx) => rx,
        Err(e) => {
            return CheckOutcome::Ran {
                code: 2,
                out: String::new(),
                err: format!("✗ [ceiling] bad GOH_LINE_EXCLUDE regex: {e}\n"),
            };
        }
    };
    let unbounded = if line_unbounded.is_empty() {
        None
    } else {
        match regex::Regex::new(line_unbounded) {
            Ok(rx) => Some(rx),
            Err(e) => {
                return CheckOutcome::Ran {
                    code: 2,
                    out: String::new(),
                    err: format!("✗ [ceiling] bad GOH_LINE_UNBOUNDED regex: {e}\n"),
                };
            }
        }
    };
    let baseline_text = std::fs::read_to_string(&baseline_path).unwrap_or_default();
    let have = baseline_keys(&baseline_text);
    let (offenders, inspected, waived) =
        check_exclusions(root, files, max, &line_exclude, unbounded.as_ref(), &have);
    let (ok, out, err) = format_ceiling(
        &offenders,
        inspected,
        waived,
        max,
        baseline_name,
        unbounded.is_some(),
    );
    CheckOutcome::Ran {
        code: i32::from(!ok),
        out,
        err,
    }
}

/// The ratchet half: today's line counts for the baseline's own keys,
/// shrink-only. Runs whenever the baseline file exists, mirroring the
/// shell pipeline. Returns `Skipped` when it does not.
#[must_use]
pub fn run_ratchet(root: &std::path::Path, line_baseline: Option<&str>) -> CheckOutcome {
    let Some(baseline_name) = line_baseline else {
        return CheckOutcome::Skipped;
    };
    let baseline_path = root.join(baseline_name);
    if !baseline_path.is_file() {
        return CheckOutcome::Skipped;
    }
    let text = std::fs::read_to_string(&baseline_path).unwrap_or_default();
    match parse_baseline(&text, baseline_name) {
        Err(message) => CheckOutcome::Ran {
            code: 2,
            out: String::new(),
            err: format!("✗ [ratchet] precondition missing: {message}\n"),
        },
        Ok(baseline) => {
            let current = measure_loc(root, &baseline);
            let cmp = compare(&baseline, &current);
            let (ok, report) = format_ratchet(&baseline, &current, &cmp);
            // The reference prints its OK line to stdout and every failure
            // to stderr — the split is part of the parity surface.
            let (out, err) = if ok {
                (report, String::new())
            } else {
                (String::new(), report)
            };
            CheckOutcome::Ran {
                code: i32::from(!ok),
                out,
                err,
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keys_cover_both_formats() {
        assert!(baseline_keys("600\tsrc/a.rs\n# comment\n450 src/b.rs\n").contains("src/a.rs"));
        assert!(baseline_keys("{\"x\": 1}").contains("x"));
    }
}
