//! The structural pipeline -- `goh structural [--staged|--full]`. Mirrored step
//! for step by `gates/structural.sh`'s Python branch and pinned to it by
//! `tests/test_goh_structural_parity.py`.

use crate::{
    blobs, claims, gatesrc, gitutil, goh_root, killname, lockver, mdlinks, prefetch, provenance,
    scope, steps, steps_delegated, unreaped,
};

/// Delegate the structural gate to `gates/structural.sh`. Returns its exit code.
/// `goh structural`: every step, fail-fast, delegated checkers started together.
#[must_use]
pub fn run(staged: bool, full: bool) -> i32 {
    let scope = match scope::resolve(staged, full) {
        Ok(scope) => scope,
        Err(message) => {
            eprintln!("✗ {message}");
            return 2;
        }
    };
    let staged = scope == scope::Scope::Staged;
    let repo = gatesrc::config_root();
    let cfg = match gatesrc::load(&repo) {
        Ok(cfg) => cfg,
        Err(message) => {
            eprintln!("✗ structural: {message}");
            return 2;
        }
    };
    let checks = goh_root().join("checks");

    println!("\n== structural gate ==");

    // One enumeration shared by every native scanner: four separate `git
    // ls-files` spawns measured ~30 ms of the ~80 ms native total
    // (2026-09-23). The delegated steps below re-enumerate inside their
    // own processes until they are ported (Phases 2-3).
    let files = match gitutil::listed_files(&repo, staged) {
        Ok(files) => files,
        Err(message) => {
            eprintln!("✗ structural: {message}");
            return 2;
        }
    };
    // ...and at --staged one read of every staged blob, shared by every
    // native scanner (Phase 3d): it was one `git show` per file PER scanner.
    if staged {
        let _cached = blobs::prefetch_staged(&repo, &files);
    }

    // The delegated checkers start together and report in order (crate::prefetch, BACKLOG P1e);
    // `_drain` joins any still running on every return path below, the red ones included.
    let _drain = prefetch::Drain;
    let specs = prefetch::collect(|| {
        let _ = steps_delegated::step_shell(&repo, &cfg, &checks, staged);
        let _ = steps_delegated::step_credential_urls(&repo, &checks);
        let _ = steps_delegated::step_python_formatted(&repo, &cfg, &checks, staged);
        let _ = steps_delegated::step_full_only(&repo, &checks, staged);
    });
    prefetch::start(specs, &checks, &repo);

    if let Some(code) = steps::step_emoji(&repo, &files, &cfg, staged) {
        return code;
    }
    if let Some(code) = steps::step_markers(&repo, &files, staged) {
        return code;
    }
    if let Some(code) = steps::step_length(&repo, &files, &cfg, staged) {
        return code;
    }
    if let Some(code) = steps::step_ceiling(&repo, &files, &cfg, staged) {
        return code;
    }
    if let Some(code) = steps::step_corpus(&repo, &cfg, staged) {
        return code;
    }
    if let Some(code) = steps_delegated::step_shell(&repo, &cfg, &checks, staged) {
        return code;
    }
    if let Some(code) = steps::step_secrets(&repo, &files, &cfg, staged) {
        return code;
    }
    // A credential in a git remote URL, which the step above cannot see: `.git/config` is
    // untracked. Adjacent to `step_secrets` because it is the same defect class and the same
    // reasoning about it -- one committed, one not -- so a reader comparing the two steps finds
    // them adjacent rather than having to know they are related.
    if let Some(code) = steps_delegated::step_credential_urls(&repo, &checks) {
        return code;
    }
    if let Some(code) = steps::step_home_paths(&repo, &files, &cfg, staged) {
        return code;
    }
    if let Some(code) = provenance::step(&repo, staged) {
        return code;
    }
    if let Some(code) = mdlinks::step(&cfg, staged) {
        return code;
    }
    if let Some(code) = lockver::step() {
        return code;
    }
    if let Some(code) = steps_delegated::step_python_formatted(&repo, &cfg, &checks, staged) {
        return code;
    }
    if let Some(code) = killname::step(&cfg, staged) {
        return code;
    }
    if let Some(code) = unreaped::step(&cfg, staged) {
        return code;
    }
    if let Some(code) = claims::step_gate(&cfg, staged) {
        return code;
    }
    if let Some(code) = steps_delegated::step_full_only(&repo, &checks, staged) {
        return code;
    }

    println!();
    println!("✓ all structural gates passed");
    0
}
