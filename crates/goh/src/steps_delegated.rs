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

/// A credential in a git remote URL.
///
/// `check_no_secrets.py` reads TRACKED FILES and `.git/config` is untracked by
/// definition, so this class was invisible to the whole estate. Proven in a
/// scratch repo on 2026-10-05: `check_no_secrets.py` reported `OK` over a repo
/// whose `remote.origin.url` carried a live `gho_` token.
///
/// DELEGATED rather than ported, deliberately. Every other native step here is a
/// port of a Python checker over files this binary has already read; this one
/// reads `git config`, which the native tier does not load, and the reason to
/// get it right is a MEASURED TABLE of remote-URL shapes (26 rows, in the
/// checker's docstring and its `--probe`) rather than a port. Duplicating that
/// table in Rust would be a second copy of a rule about credentials, which is the
/// last thing that should have two.
///
/// No `--staged` and no `--exclude`: the subject is `.git/config`, which is not
/// a file the staged scope reads and not a tree anything excludes. A `(staged)`
/// label would be a lie about what was read.
#[must_use]
pub fn step_credential_urls(repo: &std::path::Path, checks: &std::path::Path) -> Option<i32> {
    let code = delegated(
        checks,
        repo,
        "no credential in a git remote URL",
        "python3",
        &["check_no_credential_urls.py".to_owned()],
    );
    if code != 0 {
        return Some(code);
    }
    None
}

/// Python shape, decided by the repo's own declared rule set.
///
/// Both scopes. A staged pass judges the staged `.py` files' index blobs
/// (`--staged`) and reformats nothing; a full pass judges the tree. The checker
/// resolves ruff's settings from the tree it runs in, which is why it takes the
/// repo root.
///
/// Paired with the `goh_step` of the same name in gates/structural.sh; the two
/// are kept in step by `tests/test_goh_structural_parity.py`.
#[must_use]
pub fn step_python_formatted(
    repo: &std::path::Path,
    cfg: &gatesrc::Gatesrc,
    checks: &std::path::Path,
    staged: bool,
) -> Option<i32> {
    // Opt-in, stated at the call site: a repo that has not declared a rule set
    // should not learn one by going red. Both scopes: staged mode judges the
    // staged blobs and reformats nothing (v0.20.0's refused push was the cost of
    // keeping it full-only).
    if !gatesrc::opt_in(cfg, "GOH_PYTHON_FORMATTED") {
        return None;
    }
    let (label, args) = if staged {
        (
            "python is ruff-formatted (staged)",
            vec![
                "check_python_formatted.py".to_owned(),
                "--staged".to_owned(),
            ],
        )
    } else {
        (
            "python is ruff-formatted",
            vec!["check_python_formatted.py".to_owned()],
        )
    };
    let code = delegated(checks, repo, label, "python3", &args);
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
