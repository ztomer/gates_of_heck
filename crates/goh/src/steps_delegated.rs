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

/// Version provenance: a `--version` flag that carries no commit.
///
/// A number answers "is this current?"; only a commit answers "what am I
/// actually running?" -- the question that matters when behaviour disagrees
/// with the tree you are reading, which a shared target directory nobody
/// refreshes makes possible with no other outward sign.
///
/// STATIC by design, and that is load-bearing rather than incidental: a gate
/// that must run every repo's binary is a gate that gets skipped on exactly the
/// repos it would catch (wrong architecture, missing toolchain, a library with
/// no binary). This asserts the DECLARATION instead.
///
/// Opt-in by convention, not by environment variable: a repo ships
/// `.gates-version-baseline.json` and entries may only shrink. The shell is
/// discarded by `structural.sh`, which execs this binary, so the step has to
/// live here or not at all -- which is also why the same block is mirrored into
/// the Python pipeline below for `GOH_NO_NATIVE=1` parity.
#[must_use]
pub fn step_version_provenance(
    repo: &std::path::Path,
    checks: &std::path::Path,
    staged: bool,
) -> Option<i32> {
    let baseline = ".gates-version-baseline.json";
    if !repo.join(baseline).is_file() {
        return None;
    }
    let mut args = vec!["check_version_provenance.py".to_owned()];
    if staged {
        args.push("--staged".to_owned());
    }
    args.push("--baseline".to_owned());
    args.push(baseline.to_owned());
    let code = delegated(checks, repo, "version provenance", "python3", &args);
    if code != 0 {
        return Some(code);
    }
    None
}

/// Cross-file markdown links: a relative link must resolve to a file AND to an
/// anchor that file actually has.
///
/// Both halves fail silently. A link to a heading that does not exist renders
/// fine and 404s only on click, so no linter or CI notices; and the anchor is
/// not the heading, because GitHub lowercases, drops the punctuation and turns
/// spaces into hyphens -- a six-step transformation a human must redo at every
/// link. An audit of `app_updates` (2026-10-01) had to hand-derive
/// `#48-what-90-can-actually-do-measured` into a sibling file and had already
/// written a wrong one.
///
/// `GOH_EXCLUDE` covers vendored docs: ztools' camoufox-rs PROTOCOL.md carries
/// upstream's own broken anchor, which is upstream's to fix.
#[must_use]
pub fn step_md_links(
    repo: &std::path::Path,
    checks: &std::path::Path,
    cfg: &gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    let label = if staged {
        "markdown links resolve (staged)"
    } else {
        "markdown links resolve"
    };
    let mut args = vec!["check_md_links.py".to_owned()];
    if staged {
        args.push("--staged".to_owned());
    }
    if !cfg.exclude.is_empty() {
        args.push("--exclude".to_owned());
        args.push(cfg.exclude.clone());
    }
    let code = delegated(checks, repo, label, "python3", &args);
    if code != 0 {
        return Some(code);
    }
    None
}

/// A committed `Cargo.lock` must agree with the manifest it was generated from.
///
/// `app_updates`, 2026-10-01: a release commit bumped `[workspace.package]
/// version` to 1.36.0 and shipped a lockfile still saying 1.35.0 for all four
/// crates. Nothing noticed, and the reason is structural rather than accidental:
/// any `cargo build` silently rewrites the lockfile, so the working tree heals on
/// the next compile while the COMMIT -- which is what gets published and what a
/// clone reproduces -- stays wrong.
///
/// The tag checker cannot see it: `check_tag_version.py` reads `Cargo.toml` and
/// never opens `Cargo.lock`. Same class as the tag incident, one file over.
///
/// NOT gated on being a Rust repo. The checker reports absence as a named
/// non-run, which is what `check_empty_scope.py` requires of it; a guard here
/// would be a second thing that could be mis-wired.
#[must_use]
pub fn step_lock_version(repo: &std::path::Path, checks: &std::path::Path) -> Option<i32> {
    let code = delegated(
        checks,
        repo,
        "Cargo.lock matches its manifests",
        "python3",
        &["check_lock_version.py".to_owned()],
    );
    if code != 0 {
        return Some(code);
    }
    None
}

#[must_use]
pub fn step_kill_by_name(
    repo: &std::path::Path,
    cfg: &gatesrc::Gatesrc,
    checks: &std::path::Path,
    staged: bool,
) -> Option<i32> {
    // 7c. Process kills by name (opt-in per repo): a name matches processes
    // the caller does not own. Delegated to the Python checker, which owns
    // the comment stripping and the allowlist ratchet.
    if !cfg.no_kill_by_name {
        return None;
    }
    let label = if staged {
        "no process kill by name (staged)"
    } else {
        "no process kill by name"
    };
    let mut args = vec!["check_no_kill_by_name.py".to_owned()];
    if staged {
        args.push("--staged".to_owned());
    }
    if !cfg.exclude.is_empty() {
        args.push("--exclude".to_owned());
        args.push(cfg.exclude.clone());
    }
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
