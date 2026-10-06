//! The reference's report, line for line: text (stdout and stderr apart) and `--json`.

use std::path::Path;

use serde_json::Value;

use super::{adopted, err, info, step, Line, Verdict, ALLOW_FILE, MAX_REPORTED, OPT_IN_KEY};

pub(super) fn json(v: &Verdict) -> (i32, String) {
    let findings: Vec<Value> = v
        .findings
        .iter()
        .map(|(rel, claim, kind, message, _)| {
            serde_json::json!({ "file": rel, "line": claim.line, "kind": kind, "detail": message })
        })
        .collect();
    let doc = serde_json::json!({
        "findings": findings,
        "stale_allow_entries": v.stale,
        "problems": v.problems,
        "notes": v.notes,
        "examined": v.examined,
        "claims": v.claims,
        "allowlisted": v.excused,
        "unreviewed": v.debt,
    });
    let code = i32::from(!v.findings.is_empty() || !v.stale.is_empty() || !v.problems.is_empty());
    (code, format!("{}\n", crate::pyjson::dumps_indent2(&doc)))
}

/// The findings, stale entries and refusals; `true` when there were any.
fn report_red(v: &Verdict, out: &mut Vec<Line>) -> bool {
    for p in &v.problems {
        err(out, &format!("[claim_derivation] {p}"));
    }
    for (rel, claim, kind, message, command) in v.findings.iter().take(MAX_REPORTED) {
        err(
            out,
            &format!(
                "[claim_derivation] {rel}:{}: {kind} — {message}",
                claim.line
            ),
        );
        let shown: String = claim.text.trim().chars().take(160).collect();
        step(out, &format!("the claim: {shown}"));
        if let Some(c) = command {
            step(out, &format!("re-derive: {c}"));
        }
    }
    if v.findings.len() > MAX_REPORTED {
        err(
            out,
            &format!("    ... and {} more", v.findings.len() - MAX_REPORTED),
        );
    }
    for e in &v.stale {
        let get = |k: &str| {
            e.get(k)
                .and_then(Value::as_str)
                .unwrap_or("None")
                .to_owned()
        };
        err(
            out,
            &format!(
                "[claim_derivation] stale entry in {ALLOW_FILE}: {}: {} — the claim it excuses is gone",
                get("path"),
                get("claim")
            ),
        );
    }
    if !v.findings.is_empty() || !v.stale.is_empty() || !v.problems.is_empty() {
        if !v.findings.is_empty() {
            err(
                out,
                &format!(
                    "--- {} finding(s): a number that stopped being true ---",
                    v.findings.len()
                ),
            );
        }
        if !v.stale.is_empty() {
            err(
                out,
                &format!(
                    "--- {} stale allowlist entr(ies): delete them ---",
                    v.stale.len()
                ),
            );
        }
        info(
            out,
            "A claim that must stand carries `claim:` plus a reason in",
        );
        info(
            out,
            &format!(
                "{ALLOW_FILE}, and an entry that matches nothing FAILS -- a fixed claim takes it."
            ),
        );
        info(
            out,
            "Otherwise: the command above is the truth, so keep the command and drop the number.",
        );
        return true;
    }
    false
}

pub(super) fn text(v: &Verdict, root: &Path, staged: bool) -> (i32, Vec<Line>) {
    let mut out = Vec::new();
    if report_red(v, &mut out) {
        return (1, out);
    }
    if !v.notes.is_empty() {
        for n in &v.notes {
            info(&mut out, &format!("[claim_derivation] {n}"));
        }
        return (0, out);
    }
    if v.examined == 0 {
        err(
            &mut out,
            "[claim_derivation] nothing to re-derive — refusing to report clean over zero files",
        );
        return (1, out);
    }
    if v.claims == 0 {
        out.push((
            false,
            format!(
                "✓ [claim_derivation] OK — {} text file(s) scanned, 0 MARKED claims",
                v.examined
            ),
        ));
        info(
            &mut out,
            "A claim is only read when the number AND the path are backticked, or the line",
        );
        info(
            &mut out,
            "carries `claim:`. Unmarked numbers are prose, and prose is not this gate's job.",
        );
        if !adopted(root, staged) {
            info(
                &mut out,
                &format!(
                    "Turn the convention on with {OPT_IN_KEY}=1 in .gatesrc once there are claims."
                ),
            );
        }
        return (0, out);
    }
    if v.debt > 0 {
        out.push((
            true,
            format!(
                "⚠ [claim_derivation]   {} of {} allowed claim(s) are 'unreviewed': seeded when the gate landed, not yet examined",
                v.debt, v.excused
            ),
        ));
    }
    let tail = if v.excused > 0 {
        format!(", {} allowlisted in {ALLOW_FILE}", v.excused)
    } else {
        String::new()
    };
    out.push((
        false,
        format!(
            "✓ [claim_derivation] OK — {} text file(s), all {} marked claim(s) re-derived from the tree{tail}",
            v.examined, v.claims
        ),
    ));
    (0, out)
}
