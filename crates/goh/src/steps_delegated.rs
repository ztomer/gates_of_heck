//! The steps this binary DELEGATES to the Python checkers.
//!
//! Split out of `steps.rs` when the line cap caught up with it, and cut at a
//! real boundary rather than an arbitrary one: every function here is a
//! `delegated()` call that spawns a checker and returns its verdict, with no
//! native scanning of its own. The steps that DO scan (emoji, markers, length,
//! ceiling, secrets, home paths, skills corpus) share a shape these do not --
//! they take the pre-read file list, and their output is a finding list -- so
//! the two clusters have no call sites in common beyond the module itself.
//!
//! Each one is mirrored in `gates/structural.sh`, which execs this binary and
//! so discards the shell; `tests/test_goh_structural_parity.py` compares the
//! two pipelines' verdicts step for step. A step added here and not there is a
//! step that runs in exactly one of the two, which is how a gate ends up
//! present in the source and absent from every push.

use crate::gatesrc;
use crate::step_report::delegated;

#[must_use]
pub fn step_shell(
    repo: &std::path::Path,
    cfg: &gatesrc::Gatesrc,
    checks: &std::path::Path,
    staged: bool,
) -> Option<i32> {
    // 6. Bash is the most-edited language under these gates.
    let label = if staged {
        "shell lint (staged)"
    } else {
        "shell lint"
    };
    let mut args = vec!["check_shell_lint.sh".to_owned()];
    if staged {
        args.push("--staged".to_owned());
    }
    if !cfg.exclude.is_empty() {
        args.push("--exclude".to_owned());
        args.push(cfg.exclude.clone());
    }
    let code = delegated(checks, repo, label, "bash", &args);
    if code != 0 {
        return Some(code);
    }
    None
}

#[must_use]
pub fn step_full_only(
    repo: &std::path::Path,
    checks: &std::path::Path,
    staged: bool,
) -> Option<i32> {
    // 8-9. Full scope only: empty-tree refusal and gate self-proofs.
    if staged {
        return None;
    }
    let empty_code = delegated(
        checks,
        repo,
        "gates refuse to pass over an empty tree",
        "python3",
        &["check_empty_scope.py".to_owned()],
    );
    if empty_code != 0 {
        return Some(empty_code);
    }
    let probes_code = delegated(
        checks,
        repo,
        "gate self-proofs still pass",
        "python3",
        &["check_probes_pass.py".to_owned()],
    );
    if probes_code != 0 {
        return Some(probes_code);
    }
    None
}
