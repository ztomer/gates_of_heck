//! The two ways in: `goh js-syntax` on its own, and the structural step.
//!
//! WHY THE STEP IS FULL SCOPE ONLY. The check answers "is this tree's
//! JavaScript sound", and the tree is what a push carries. There is no staged
//! form: a staged blob and a template's substituted form would both have to be
//! written beside the file they replace, and a gate that leaves files in the
//! tree it is judging is a gate with a side effect.
//!
//! WHY THE KEY IS RAW. `GOH_JS_SYNTAX` is `on` or `off`, and anything else is
//! an error naming the key -- a typo that silently disabled the gate would be
//! the defect the gate exists to prevent. It is opt-in because most of the
//! estate tracks no JavaScript at all, and a gate with no subject would say
//! "not applicable" on every push forever.

use std::path::PathBuf;

use goh_tui::ERR;

use super::{report, TAG};

/// Is this gate on? Unset is off: it is opt-in, like every other optional gate.
///
/// # Errors
///
/// Returns a message naming the key when its value is neither `on` nor `off`:
/// a typo that silently disabled the gate is the defect the gate exists to
/// prevent.
pub fn enabled(raw: Option<&str>) -> Result<bool, String> {
    match raw.map_or("", str::trim) {
        "" | "off" => Ok(false),
        "on" => Ok(true),
        other => Err(format!("GOH_JS_SYNTAX is neither on nor off: {other:?}")),
    }
}

/// `goh js-syntax`: the check, on its own.
#[must_use]
pub fn run_command() -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(PathBuf::from) else {
        println!("{TAG} not a git repo — skipping");
        return 0;
    };
    match super::run(&root) {
        Ok(outcome) if outcome.scope.files.is_empty() => {
            print!("{}", report::format_no_scope());
            0
        }
        Ok(outcome) => {
            print!("{}", report::format_report(&outcome));
            if !outcome.findings.is_empty() {
                return 1;
            }
            if !outcome.unmeasured.is_empty() {
                // A source the engine could not read is a subject the gate did
                // not judge: a refusal (exit 2), never a silent pass.
                return 2;
            }
            0
        }
        Err(message) => {
            goh_tui::eoutln!("{ERR} {TAG} {message}");
            2
        }
    }
}

/// The structural step: every tracked JavaScript source parses.
#[must_use]
pub fn step(cfg: &crate::gatesrc::Gatesrc) -> Option<i32> {
    let on = match enabled(cfg.js_syntax_gate.as_deref()) {
        Ok(on) => on,
        Err(message) => {
            goh_tui::eoutln!("{ERR} {TAG} {message}");
            return Some(2);
        }
    };
    if !on {
        return None;
    }
    let label = "every tracked JavaScript source parses";
    let start = crate::step_report::begin(label);
    let how = crate::step_report::ported("crates/goh/src/jssyntax/mod.rs");
    let repo = crate::gatesrc::config_root();
    let outcome = match super::run(&repo) {
        Ok(outcome) => outcome,
        Err(message) => {
            return Some(crate::step_report::fail(
                label,
                &how,
                &format!("{ERR} {TAG} {message}\n"),
                start,
            ))
        }
    };
    if outcome.scope.files.is_empty() {
        crate::subject::record(&crate::subject::JS_SYNTAX, 0);
        goh_tui::out(&report::format_no_scope());
        crate::step_report::ok(label, start);
        return None;
    }
    crate::subject::record(&crate::subject::JS_SYNTAX, outcome.judged);
    let text = report::format_report(&outcome);
    if !outcome.findings.is_empty() {
        return Some(crate::step_report::fail(label, &how, &text, start));
    }
    if !outcome.unmeasured.is_empty() {
        // A refusal, not a finding: the gate could not judge every source in
        // its scope, and says which and why.
        goh_tui::eout(&text);
        return Some(2);
    }
    goh_tui::out(&text);
    crate::step_report::ok(label, start);
    None
}
