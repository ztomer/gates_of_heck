//! Structural pipeline steps — one function per gate step.
//! Extracted from `main.rs`: past the cap, the split is the fix.

use crate::step_report::{begin, delegated, fail, ok};
use crate::{emoji, gatesrc, index_view::IndexView, length, markers, secrets};

pub(crate) fn compile_exclude(exclude: &str) -> Result<Option<regex::Regex>, String> {
    if exclude.is_empty() {
        return Ok(None);
    }
    regex::Regex::new(exclude)
        .map(Some)
        .map_err(|e| format!("bad --exclude regex: {e}"))
}

#[must_use]
pub fn step_emoji(
    repo: &std::path::Path,
    files: &[String],
    cfg: &gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    // 1. Emoji are a failure state.
    let label = if staged {
        "no disallowed emoji (staged)"
    } else {
        "no disallowed emoji"
    };
    let exclude = match compile_exclude(&cfg.exclude) {
        Ok(filter) => filter,
        Err(message) => {
            eprintln!("✗ [no_emoji] {message}");
            return Some(2);
        }
    };
    let extra = emoji::parse_allow(&cfg.allow);
    let start = begin(label);
    let (hits, _) = emoji::scan_files(repo, files, exclude.as_ref(), &extra, staged);
    if hits.is_empty() {
        ok(label, start);
        return None;
    }
    Some(fail(
        label,
        "native",
        &emoji::format_report(&hits, staged, &cfg.allow),
        start,
    ))
}

#[must_use]
pub fn step_markers(repo: &std::path::Path, files: &[String], staged: bool) -> Option<i32> {
    // 2. A conflict marker that reaches a commit is a merge walked away from.
    let start = begin("no conflict markers");
    let bad = markers::scan_files(repo, files, staged);
    if bad.is_empty() {
        ok("no conflict markers", start);
        return None;
    }
    Some(fail(
        "no conflict markers",
        "native",
        &markers::format_report(&bad),
        start,
    ))
}

#[must_use]
pub fn step_length(
    repo: &std::path::Path,
    files: &[String],
    cfg: &gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    // 3. One cap, one name.
    let Some(max) = cfg.max_lines else {
        eprintln!("⚠ file-length cap not set — add GOH_MAX_LINES to .gatesrc to enable it");
        return None;
    };
    let label = format!("file length <= {max}");
    let union = gatesrc::length_exclude(cfg);
    let filter = match compile_exclude(&union) {
        Ok(filter) => filter,
        Err(message) => {
            eprintln!("✗ [file_length] {message}");
            return Some(2);
        }
    };
    let start = begin(&label);
    let (over, _) = length::scan_files(repo, files, max, filter.as_ref(), staged);
    if over.is_empty() {
        ok(&label, start);
        return None;
    }
    Some(fail(
        &label,
        "native",
        &length::format_report(&over, max),
        start,
    ))
}

#[must_use]
pub fn step_ceiling(
    repo: &std::path::Path,
    files: &[String],
    cfg: &gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    use crate::ceiling::CheckOutcome;
    // Both bounds read a baseline and measure files: at --staged, the index
    // (see index_view). The ceiling check walks every TRACKED file, as the
    // Python checker does, so at --staged it lists the view, not the diff.
    let Some(view) = IndexView::repo(repo, staged && cfg.line_baseline.is_some()) else {
        return Some(2);
    };
    let view_files;
    let files = if view.root() == repo {
        files
    } else {
        view_files = match crate::gitutil::listed_files(view.root(), false) {
            Ok(listed) => listed,
            Err(message) => {
                eprintln!("✗ structural: {message}");
                return Some(2);
            }
        };
        &view_files
    };
    let repo = view.root();
    // 4. An exemption from the cap is not an exemption from any bound at all.
    match crate::ceiling::run_exclusion(
        repo,
        files,
        cfg.max_lines,
        &cfg.line_exclude,
        &cfg.line_unbounded,
        cfg.line_baseline.as_deref(),
    ) {
        CheckOutcome::Skipped => {}
        CheckOutcome::Warn(text) => eprint!("{text}"),
        CheckOutcome::Ran { code, out, err } => {
            let label = "line-cap exemptions carry a ceiling";
            let start = begin(label);
            if code != 0 {
                return Some(fail(label, "native", &format!("{out}{err}"), start));
            }
            ok(label, start);
        }
    }
    // 4b. ...and the ceilings are enforced here, not left to each repo's own
    // gate script: a baseline nobody ratchets is a list, not a bound.
    match crate::ceiling::run_ratchet(repo, cfg.line_baseline.as_deref()) {
        CheckOutcome::Skipped | CheckOutcome::Warn(_) => None,
        CheckOutcome::Ran { code, out: _, err } => {
            let label = "cap-exempt files within their ceilings";
            let start = begin(label);
            if code != 0 {
                return Some(fail(label, "native", &err, start));
            }
            ok(label, start);
            None
        }
    }
}

#[must_use]
pub fn step_corpus(repo: &std::path::Path, cfg: &gatesrc::Gatesrc, staged: bool) -> Option<i32> {
    // 5. Skills corpus (opt-in; the corpus may live outside the repo).
    if !cfg.skills_corpus {
        return None;
    }
    let label = "skills corpus";
    let root = cfg
        .skills_root
        .clone()
        .unwrap_or_else(|| repo.to_string_lossy().into_owned());
    if !std::path::Path::new(&root).is_dir() {
        eprintln!("⚠ skills corpus not present at {root} — NOT checked");
        return None;
    }
    // At --staged, the corpus AS THE INDEX HOLDS IT (see index_view).
    let view = match crate::index_view::IndexView::at_scope(repo, &root, staged) {
        Ok(view) => view,
        Err(outcome) => return outcome,
    };
    let max_words = match &cfg.skills_max_words {
        None => crate::skills::DEFAULT_MAX_WORDS,
        Some(raw) => {
            let Ok(n) = raw.parse() else {
                eprintln!("✗ [skills_corpus] bad GOH_SKILLS_MAX_WORDS value: {raw:?}");
                return Some(2);
            };
            n
        }
    };
    let start = begin(label);
    let outcome = crate::skills::audit(
        &match crate::skills::Scanner::compile() {
            Ok(scanner) => scanner,
            Err(message) => return Some(fail(label, "native", &format!("{message}\n"), start)),
        },
        view.root(),
        &crate::skills::AuditInputs {
            max_words,
            min_skills: crate::skills::DEFAULT_MIN_SKILLS,
            baseline: view.root().join(crate::skills::BASELINE_NAME),
            update: false,
        },
    );
    if outcome.code != 0 {
        return Some(fail(label, "native", &outcome.out, start));
    }
    ok(label, start);
    None
}

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
pub fn step_secrets(
    repo: &std::path::Path,
    files: &[String],
    cfg: &gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    // 7. Committed secrets outrank every other defect class.
    let label = if staged {
        "no committed secrets (staged)"
    } else {
        "no committed secrets"
    };
    let exclude = match compile_exclude(&cfg.exclude) {
        Ok(filter) => filter,
        Err(message) => {
            eprintln!("✗ [no_secrets] {message}");
            return Some(2);
        }
    };
    let start = begin(label);
    match secrets::scan_files(repo, files, exclude.as_ref(), staged) {
        Err(message) => Some(fail(label, "native", &format!("{message}\n"), start)),
        Ok((bad, _)) if !bad.is_empty() => Some(fail(
            label,
            "native",
            &secrets::format_report(&bad, staged),
            start,
        )),
        Ok(_) => {
            ok(label, start);
            None
        }
    }
}

#[must_use]
pub fn step_home_paths(
    repo: &std::path::Path,
    files: &[String],
    cfg: &gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    use crate::homepaths::ScopeVerdict;
    // 7b. Hard-coded home paths (opt-in per repo). A location under
    // someone's HOME in a shipped script or binary works on one machine at
    // one moment — the salary CLI outage of 2026-09.
    if !cfg.no_home_paths {
        return None;
    }
    let label = if staged {
        "no hard-coded home paths (staged)"
    } else {
        "no hard-coded home paths"
    };
    let exclude = match compile_exclude(&cfg.exclude) {
        Ok(filter) => filter,
        Err(message) => {
            eprintln!("✗ [no_home_paths] {message}");
            return Some(2);
        }
    };
    let start = begin(label);
    match crate::homepaths::scan_files(repo, files, exclude.as_ref(), staged) {
        Err(message) => Some(fail(label, "native", &format!("{message}\n"), start)),
        Ok((hits, checked)) => match crate::homepaths::classify(&hits, checked, staged) {
            ScopeVerdict::Violations => Some(fail(
                label,
                "native",
                &crate::homepaths::format_report(&hits, staged),
                start,
            )),
            ScopeVerdict::Clean => {
                ok(label, start);
                None
            }
            ScopeVerdict::NothingStaged => {
                print!(
                    "{}",
                    crate::homepaths::format_empty(ScopeVerdict::NothingStaged)
                );
                ok(label, start);
                None
            }
            ScopeVerdict::NothingToCheck => Some(fail(
                label,
                "native",
                &crate::homepaths::format_empty(ScopeVerdict::NothingToCheck),
                start,
            )),
        },
    }
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;

    fn git(repo: &std::path::Path, args: &[&str]) {
        let status = Command::new("git")
            .args(args)
            .current_dir(repo)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
        assert!(status.is_ok_and(|s| s.success()), "git {args:?}");
    }

    fn lines(n: usize) -> String {
        use std::fmt::Write as _;
        (0..n).fold(String::new(), |mut out, i| {
            let _ = writeln!(out, "// {i}");
            out
        })
    }

    #[test]
    fn ceiling_step_enforces_the_baseline_both_ways() {
        let dir = tempfile::tempdir().expect("tempdir");
        let repo = dir.path();
        git(repo, &["init", "-q"]);
        std::fs::create_dir_all(repo.join("src")).expect("mkdir");
        std::fs::write(repo.join("src/big.rs"), lines(600)).expect("write");
        std::fs::write(repo.join("base.txt"), "# ceilings\n600\tsrc/big.rs\n").expect("write");
        git(repo, &["add", "-A"]);
        let cfg = gatesrc::Gatesrc {
            max_lines: Some(500),
            line_exclude: "src/big\\.rs".to_owned(),
            line_baseline: Some("base.txt".to_owned()),
            ..gatesrc::Gatesrc::default()
        };
        let files = crate::gitutil::listed_files(repo, false).expect("list");
        // At the ceiling: both the "carries a ceiling" and the ratchet pass.
        assert_eq!(step_ceiling(repo, &files, &cfg, false), None);
        // One line over: the ratchet fails the step.
        std::fs::write(repo.join("src/big.rs"), lines(601)).expect("write");
        git(repo, &["add", "-A"]);
        let files = crate::gitutil::listed_files(repo, false).expect("list");
        assert!(step_ceiling(repo, &files, &cfg, false).is_some_and(|c| c != 0));
        // Shrinking is always allowed.
        std::fs::write(repo.join("src/big.rs"), lines(550)).expect("write");
        git(repo, &["add", "-A"]);
        let files = crate::gitutil::listed_files(repo, false).expect("list");
        assert_eq!(step_ceiling(repo, &files, &cfg, false), None);
        // A baseline path that does not exist: the exempt file has no bound at
        // all, which the ceiling check reports -- the ratchet never runs.
        let unbaselined = gatesrc::Gatesrc {
            line_baseline: Some("missing.txt".to_owned()),
            ..cfg
        };
        assert!(step_ceiling(repo, &files, &unbaselined, false).is_some_and(|c| c != 0));
    }
}
