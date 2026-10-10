//! The structural pipeline -- `goh structural [--staged|--full]`, the only tier
//! since Phase N3 retired `gates/structural.sh`'s Python branch.

use crate::{
    blobs, checkoutcreds, claims, credurls, deadexec, earlypipe, gatesrc, gitutil, goh_root,
    hookindex, killname, lockver, mdlinks, prefetch, provenance, pyformat, scope, shell_lint,
    steps, steps_delegated, steps_rust, substdin, unreaped, vendored,
};

mod trackedignored;

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
    gatesrc::adopt_into_env(&repo);
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
    if let Some(code) = shell_steps(&repo, &files, &cfg, staged) {
        return code;
    }
    if let Some(code) = steps::step_secrets(&repo, &files, staged) {
        return code;
    }
    if let Some(code) = git_config_steps(&repo, &files, staged) {
        return code;
    }
    if let Some(code) = steps::step_home_paths(&repo, &files, &cfg, staged) {
        return code;
    }
    // A tracked file the repo's own `.gitignore` matches; beside the home-path step because both
    // find a file in the tree that one machine's state put there.
    if let Some(code) = trackedignored::step(&repo, &files, staged) {
        return code;
    }
    if let Some(code) = steps_rust::step(&repo, &files, &cfg, staged) {
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
    if let Some(code) = pyformat::step(&cfg, staged) {
        return code;
    }
    if let Some(code) = killname::step(&cfg, staged) {
        return code;
    }
    if let Some(code) = unreaped::step(&cfg, staged) {
        return code;
    }
    if let Some(code) = late_steps(&repo, &files, &checks, &cfg, staged) {
        return code;
    }

    println!();
    println!("✓ all structural gates passed");
    0
}

/// The last four steps, which are the ones that read the repo's own configuration and
/// helper files rather than its source: a documented claim, a vendored copy of a house
/// checker, the rules `GOH_REQUIRES_CALL` names, and every child process's stdin.
///
/// They are last because each is a finding about a file the repo CHOOSED to write, and a
/// source-shaped defect earlier in the pipeline is the one that has to be fixed first. The
/// group is a function so `run` stays inside the line cap rather than growing a step per
/// release forever.
fn late_steps(
    repo: &std::path::Path,
    files: &[String],
    checks: &std::path::Path,
    cfg: &gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    claims::step_gate(cfg, staged)
        .or_else(|| vendored::step(repo, files, staged))
        .or_else(|| crate::requires_call::step(repo, cfg, staged))
        .or_else(|| substdin::step(cfg, files, staged))
        .or_else(|| steps_delegated::step_full_only(repo, checks, staged))
}

/// The shell-source steps, in order: shell lint, then two defects shellcheck 0.11.0 exits 0 on,
/// over the same files -- a race (an early-exit pipe) and dead code (a statement after `exec`) --
/// and contract #12's carried hook index read anywhere but `goh_bind_hook_index`.
fn shell_steps(
    repo: &std::path::Path,
    files: &[String],
    cfg: &gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    shell_lint::step(cfg, staged)
        .or_else(|| earlypipe::step(repo, files, cfg, staged))
        .or_else(|| deadexec::step(repo, files, cfg, staged))
        .or_else(|| hookindex::step(repo, files, cfg, staged))
}

/// A credential in git config, which `step_secrets` cannot see: `.git/config` is untracked.
/// Adjacent to `step_secrets` because it is the same defect class and the same reasoning about
/// it -- one committed, one not -- so a reader comparing the steps finds them adjacent rather than
/// having to know they are related. Then the commonest way a token GETS there: `actions/checkout`
/// persists the job token as `http.*.extraheader` unless its step sets
/// `persist-credentials: false`. The first step finds it in CI, after the checkout ran; the second
/// finds it in the workflow, at the commit.
fn git_config_steps(repo: &std::path::Path, files: &[String], staged: bool) -> Option<i32> {
    credurls::step().or_else(|| checkoutcreds::step(repo, files, staged))
}
