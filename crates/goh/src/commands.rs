//! Single-checker subcommands (`markers`, `length`, `emoji`, `secrets`).
//! Extracted from `main.rs`: past the cap, the split is the fix.

use goh_tui::{ERR, OK, START, WARN};
use std::path::PathBuf;

use crate::{
    ceiling, emoji, gitutil, homepaths, length, lints, markers, noallow, screen, secrets, skills,
    steps,
};

pub fn run_markers(staged: bool, commits: &[String]) -> i32 {
    let Some(root) = gitutil::repo_root().map(PathBuf::from) else {
        goh_tui::eoutln!("{WARN} [no_conflict_markers] not a git repo — skipping");
        return 0;
    };
    if !commits.is_empty() {
        return run_markers_commits(&root, commits);
    }
    match markers::scan_root(&root, staged) {
        Err(message) => {
            goh_tui::eoutln!("{ERR} [no_conflict_markers] {message}");
            2
        }
        Ok(bad) if !bad.is_empty() => {
            goh_tui::eout(&markers::format_report(&bad));
            1
        }
        Ok(_) => {
            let scope = if staged { "staged" } else { "tracked" };
            goh_tui::outln!("{START} [no_conflict_markers] OK — no markers in {scope} files");
            0
        }
    }
}

/// `goh busy-loop`: a tracked shell file that runs a backgrounded busy loop.
///
/// No path exclusion: a busy loop is not a style, it is a leak, and the one escape is the
/// reasoned per-line marker (`# load-ok: <reason>`), like `goh secrets`.
pub fn run_busy_loop(staged: bool) -> i32 {
    let Some(root) = gitutil::repo_root().map(PathBuf::from) else {
        goh_tui::eoutln!("{WARN} [busy_loop] not a git repo — skipping");
        return 0;
    };
    crate::busyloop::run(&root, None, staged)
}

/// `markers --commits`: what each commit changed. A commit is never judged clean unread (exit 2).
fn run_markers_commits(root: &std::path::Path, commits: &[String]) -> i32 {
    match markers::scan_commits(root, commits) {
        Err(message) => {
            goh_tui::eoutln!("{ERR} [no_conflict_markers] {message}");
            2
        }
        Ok((bad, _)) if !bad.is_empty() => {
            goh_tui::eout(&markers::format_report(&bad));
            1
        }
        Ok((_, read)) => {
            let noun = if read == 1 { "commit" } else { "commits" };
            goh_tui::outln!("{START} [no_conflict_markers] OK — no markers in {read} {noun}");
            0
        }
    }
}

/// Fail on source files over `max` lines. Returns 0 clean, 1 violations, 2 on error.
pub fn run_length(max: usize, exclude: &str, staged: bool) -> i32 {
    let filter = match steps::compile_exclude(exclude) {
        Ok(filter) => filter,
        Err(message) => {
            goh_tui::eoutln!("{ERR} [file_length] {message}");
            return 2;
        }
    };
    let Some(root) = gitutil::repo_root().map(PathBuf::from) else {
        goh_tui::eoutln!("{WARN} [file_length] not a git repo — skipping");
        return 0;
    };
    match length::scan_root(&root, max, filter.as_ref(), staged) {
        Err(message) => {
            goh_tui::eoutln!("{ERR} [file_length] {message}");
            2
        }
        Ok((over, _checked)) if !over.is_empty() => {
            goh_tui::eout(&length::format_report(&over, max));
            1
        }
        Ok((_, checked)) => {
            goh_tui::outln!("{START} [file_length] OK — {checked} file(s) within {max} lines");
            0
        }
    }
}

/// Fail on disallowed emoji. Returns 0 clean, 1 violations, 2 on error.
pub fn run_emoji(exclude: &str, allow: &str, staged: bool) -> i32 {
    let filter = match steps::compile_exclude(exclude) {
        Ok(filter) => filter,
        Err(message) => {
            goh_tui::eoutln!("{ERR} [no_emoji] {message}");
            return 2;
        }
    };
    let extra = emoji::parse_allow(allow);
    let Some(root) = gitutil::repo_root().map(PathBuf::from) else {
        println!("[no_emoji] not a git repo — skipping");
        return 0;
    };
    match emoji::scan_root(&root, filter.as_ref(), &extra, staged) {
        Err(message) => {
            goh_tui::eoutln!("{ERR} [no_emoji] {message}");
            2
        }
        Ok((hits, _checked)) if !hits.is_empty() => {
            // NOTE: violations go to stdout, matching the reference checker
            // (the markers/length checkers use stderr — each port mirrors
            // its own reference, not the family average).
            goh_tui::out(&emoji::format_report(&hits, staged, allow));
            1
        }
        Ok((_, checked)) => {
            let scope = if staged { "staged" } else { "tracked" };
            goh_tui::outln!("{OK} [no_emoji] OK — {checked} {scope} files clean");
            0
        }
    }
}

/// Fail on committed secrets. Returns 0 clean, 1 violations, 2 on error. No path exemption
/// (see `crate::secrets`).
pub fn run_secrets(staged: bool) -> i32 {
    let Some(root) = gitutil::repo_root().map(PathBuf::from) else {
        println!("[no_secrets] not a git repo — skipping");
        return 0;
    };
    match secrets::scan_root(&root, staged) {
        Err(message) => {
            goh_tui::eoutln!("{ERR} [no_secrets] {message}");
            2
        }
        // NOTE: violations go to stdout, matching the reference checker.
        Ok((bad, _checked)) if !bad.is_empty() => {
            goh_tui::out(&secrets::format_report(&bad, staged));
            1
        }
        Ok((_, checked)) => {
            let scope = if staged { "staged" } else { "tracked" };
            goh_tui::outln!("{OK} [no_secrets] OK — {checked} {scope} files clean");
            0
        }
    }
}

/// Fail on hard-coded home paths. Returns 0 clean, 1 violations, 2 on error.
pub fn run_home_paths(exclude: &str, staged: bool) -> i32 {
    let filter = match steps::compile_exclude(exclude) {
        Ok(filter) => filter,
        Err(message) => {
            goh_tui::eoutln!("{ERR} [no_home_paths] {message}");
            return 2;
        }
    };
    let Some(root) = gitutil::repo_root().map(PathBuf::from) else {
        println!("[no_home_paths] not a git repo — skipping");
        return 0;
    };
    match homepaths::scan_root(&root, filter.as_ref(), staged) {
        Err(message) => {
            goh_tui::eoutln!("{ERR} [no_home_paths] {message}");
            2
        }
        Ok((hits, checked)) => report_home_paths(&hits, checked, staged),
    }
}

/// Shared verdict printer: the subcommand and the structural pipeline
/// classify identically (violations, empty-index honesty, empty-tree
/// refusal, clean pass).
#[must_use]
pub fn report_home_paths(hits: &[homepaths::Hit], checked: usize, staged: bool) -> i32 {
    match homepaths::classify(hits, checked, staged) {
        homepaths::ScopeVerdict::Violations => {
            // NOTE: violations go to stdout, matching the reference checker.
            goh_tui::out(&homepaths::format_report(hits, staged));
            1
        }
        homepaths::ScopeVerdict::Clean => {
            goh_tui::outln(&homepaths::format_ok(checked, staged));
            0
        }
        empty => {
            // Every empty-scope line goes to stdout, exactly like the
            // reference: the refusal exits 1, the honest empty index 0.
            goh_tui::out(&homepaths::format_empty(empty));
            i32::from(empty == homepaths::ScopeVerdict::NothingToCheck)
        }
    }
}

/// Enforce line-cap ceilings. Returns 0 pass, 1 violation, 2 on error.
pub fn run_ceiling(
    max: Option<usize>,
    line_exclude: &str,
    unbounded: &str,
    baseline: Option<&str>,
) -> i32 {
    let Some(root) = gitutil::repo_root().map(PathBuf::from) else {
        println!("[ceiling] not a git repo — skipping");
        return 0;
    };
    let files = match gitutil::listed_files(&root, false) {
        Ok(files) => files,
        Err(message) => {
            goh_tui::eoutln!("{ERR} [ceiling] {message}");
            return 2;
        }
    };
    print_ceiling(&root, &files, max, line_exclude, unbounded, baseline)
}

/// Run both ceiling checks, printing each checker's stdout/stderr split
/// exactly where the reference prints it. Shared by the `ceiling`
/// subcommand and the structural pipeline.
#[must_use]
pub fn print_ceiling(
    root: &std::path::Path,
    files: &[String],
    max: Option<usize>,
    line_exclude: &str,
    unbounded: &str,
    baseline: Option<&str>,
) -> i32 {
    match ceiling::run_exclusion(root, files, max, line_exclude, unbounded, baseline) {
        ceiling::CheckOutcome::Skipped => {}
        ceiling::CheckOutcome::Warn(text) => eprint!("{text}"),
        ceiling::CheckOutcome::Ran { code, out, err } => {
            goh_tui::out(&out);
            goh_tui::eout(&err);
            if code != 0 {
                return code;
            }
        }
    }
    match ceiling::run_ratchet(root, baseline) {
        ceiling::CheckOutcome::Skipped | ceiling::CheckOutcome::Warn(_) => 0,
        ceiling::CheckOutcome::Ran { code, out, err } => {
            goh_tui::out(&out);
            goh_tui::eout(&err);
            code
        }
    }
}

/// Fail on `#[allow]`/`#[expect]` suppressions. Returns 0 clean,
/// 1 violations, 2 on error.
#[must_use]
pub fn run_no_allow(exclude: &str, staged: bool) -> i32 {
    let filter = match steps::compile_exclude(exclude) {
        Ok(filter) => filter,
        Err(message) => {
            goh_tui::eoutln!("{ERR} [no_allow] {message}");
            return 2;
        }
    };
    let Some(root) = gitutil::repo_root().map(PathBuf::from) else {
        println!("[no_allow] not a git repo — skipping");
        return 0;
    };
    let files = match gitutil::listed_files(&root, staged) {
        Ok(files) => files,
        Err(message) => {
            goh_tui::eoutln!("{ERR} [no_allow] {message}");
            return 2;
        }
    };
    match noallow::scan_root(&root, &files, filter.as_ref(), staged) {
        Err(message) => {
            goh_tui::eoutln!("{ERR} [no_allow] {message}");
            2
        }
        Ok((hits, scanned)) => {
            match noallow::classify(
                hits.len(),
                scanned.len(),
                noallow::has_own_rust(&files, filter.as_ref(), staged),
            ) {
                noallow::ScopeVerdict::Violations => {
                    // NOTE: violations go to stdout, matching the reference.
                    goh_tui::out(&noallow::format_report(&hits, staged));
                    1
                }
                noallow::ScopeVerdict::BlindLayout => {
                    goh_tui::eout(&noallow::format_blind());
                    1
                }
                noallow::ScopeVerdict::Clean => {
                    goh_tui::out(&noallow::format_ok(scanned.len()));
                    0
                }
            }
        }
    }
}

/// Fail on live-display calls in test targets. Returns 0 clean,
/// 1 violations, 2 on error. Streams mirror the reference: violations
/// and usage errors to stderr, the pass line to stdout.
#[must_use]
pub fn run_screen(paths: &[String], scope: Option<&str>, staged: bool) -> i32 {
    if paths.is_empty() && scope.is_none() {
        goh_tui::eoutln!(
            "{ERR} [no_screen] no targets: pass paths or --scope (e.g. --scope 'tests/*')"
        );
        return 2;
    }
    let Some(root) = gitutil::repo_root().map(PathBuf::from) else {
        println!("[no_screen] not a git repo — skipping");
        return 0;
    };
    let scanner = match screen::Scanner::compile() {
        Ok(scanner) => scanner,
        Err(message) => {
            goh_tui::eoutln!("{ERR} [no_screen] {message}");
            return 2;
        }
    };
    let (explicit, scoped) = match screen::collect_targets(&root, staged, paths, scope) {
        Ok(targets) => targets,
        Err(message) => {
            goh_tui::eoutln!("{ERR} [no_screen] {message}");
            return 2;
        }
    };
    let targets = if explicit.is_empty() {
        scoped
    } else {
        explicit
    };
    if paths.is_empty() && targets.is_empty() {
        goh_tui::eoutln!("{ERR} [no_screen] --scope matched no tracked files");
        return 1;
    }
    let mut bad = Vec::new();
    let mut checked = 0;
    for rel in &targets {
        if screen::language_of(rel).is_none() {
            continue;
        }
        let Some(blob) = gitutil::content_bytes(&root, rel, staged) else {
            continue;
        };
        // Binary blobs (NUL in the shared scan window) are skipped, like
        // the reference.
        if blob[..blob.len().min(gitutil::BINARY_SCAN_WINDOW)].contains(&0) {
            continue;
        }
        checked += 1;
        let text = String::from_utf8_lossy(&blob);
        for hit in screen::check_text(&scanner, rel, &text) {
            bad.push((rel.clone(), hit.lineno, hit.why));
        }
    }
    if bad.is_empty() {
        let scope = if staged { "staged" } else { "scanned" };
        goh_tui::outln!(
            "{START} [no_screen] OK — {checked} test target(s) {scope}, nothing renders to the display"
        );
        return 0;
    }
    goh_tui::eoutln!(
        "{ERR} [no_screen] {} presentation call(s) in test targets:",
        bad.len()
    );
    for (rel, lineno, why) in &bad {
        eprintln!("    {rel}:{lineno}: {why}");
    }
    eprintln!(
        "\n  Render offscreen instead, or mark the line `screen-ok:` with\n  a reason. Runtime half: source lib/headless_env.sh and launch\n  GUI children under GOH_HEADLESS=1."
    );
    1
}

/// Fail when a crate is exempt from its workspace lint policy.
/// Returns 0 clean, 1 violations. Streams mirror the reference.
#[must_use]
pub fn run_lints(self_test: bool) -> i32 {
    if self_test {
        return match lints::Scanner::compile().and_then(|s| lints::self_test(&s)) {
            Ok(()) => {
                goh_tui::outln!("{OK} [lints_optin] self-test passed");
                0
            }
            Err(message) => {
                goh_tui::eoutln!("{ERR} [lints_optin] self-test: {message}");
                1
            }
        };
    }
    let Some(root) = gitutil::repo_root().map(PathBuf::from) else {
        println!("[lints_optin] not a git repo — skipping");
        return 0;
    };
    let scanner = match lints::Scanner::compile() {
        Ok(scanner) => scanner,
        Err(message) => {
            goh_tui::eoutln!("{ERR} [lints_optin] {message}");
            return 2;
        }
    };
    let (findings, inspected, manifests, policy, members) = lints::audit(&scanner, &root);
    let (ok, out, err) = lints::format_report(&findings, inspected, manifests, policy, members);
    goh_tui::out(&out);
    goh_tui::eout(&err);
    i32::from(!ok)
}

/// Compare two images within tolerances.
///
/// Returns 0 within tolerance, 1 exceeded, 2 precondition (mirroring the
/// reference exit codes). Streams mirror the reference: reports to stdout,
/// preconditions to stderr.
#[must_use]
pub fn run_golden(a: &str, b: &str, tolerances: &str, as_json: bool) -> i32 {
    let overrides: serde_json::Value = match serde_json::from_str(tolerances) {
        Ok(value) => value,
        Err(e) => {
            goh_tui::eoutln!("{ERR} precondition: --tolerances is not valid JSON: {e}");
            return 2;
        }
    };
    let Some(overrides) = overrides.as_object() else {
        goh_tui::eoutln!("{ERR} precondition: --tolerances must be a JSON object");
        return 2;
    };
    let images = (|| {
        let a = goh_golden::load_rgb(std::path::Path::new(a)).map_err(|e| e.message)?;
        let b = goh_golden::load_rgb(std::path::Path::new(b)).map_err(|e| e.message)?;
        Ok::<_, String>((a, b))
    })();
    let (a, b) = match images {
        Ok(pair) => pair,
        Err(message) => {
            goh_tui::eoutln!("{ERR} precondition: {message}");
            return 2;
        }
    };
    let tol = match goh_golden::resolve_tolerances(overrides) {
        Ok(tol) => tol,
        Err(e) => {
            goh_tui::eoutln!("{ERR} precondition: {}", e.message);
            return 2;
        }
    };
    let verdict = match goh_golden::compare(&a, &b, &tol) {
        Ok(verdict) => verdict,
        Err(e) => {
            goh_tui::eoutln!("{ERR} precondition: {}", e.message);
            return 2;
        }
    };
    if as_json {
        println!(
            "{}",
            serde_json::to_string_pretty(&goh_golden::verdict_json(&verdict)).unwrap_or_default()
        );
    } else {
        println!("{}", goh_golden::verdict_line(&verdict));
    }
    i32::from(!verdict.ok)
}

/// Audit an agent skills corpus. Returns 0 clean, 1 violations, 2 on
/// scope/precondition errors. Everything prints to stdout, exactly like
/// the reference.
#[must_use]
pub fn run_skills(
    root: &str,
    max_words: usize,
    min_skills: usize,
    baseline: Option<&str>,
    update_baseline: bool,
) -> i32 {
    let scanner = match skills::Scanner::compile() {
        Ok(scanner) => scanner,
        Err(message) => {
            goh_tui::eoutln!("{ERR} [skills_corpus] {message}");
            return 2;
        }
    };
    let root_path = PathBuf::from(root);
    if !root_path.is_dir() {
        println!("  ✗ {root} is not a directory");
        return 2;
    }
    let baseline_path =
        baseline.map_or_else(|| root_path.join(skills::BASELINE_NAME), PathBuf::from);
    let outcome = skills::audit(
        &scanner,
        &root_path,
        &skills::AuditInputs {
            max_words,
            min_skills,
            baseline: baseline_path,
            update: update_baseline,
        },
    );
    goh_tui::out(&outcome.out);
    outcome.code
}
