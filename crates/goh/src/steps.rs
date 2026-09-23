//! Structural pipeline steps — one function per gate step.
//! Extracted from `main.rs`: past the cap, the split is the fix.

use std::process::Command;
use std::time::Instant;

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
pub fn step_emoji(repo: &std::path::Path, cfg: &gatesrc::Gatesrc, staged: bool) -> Option<i32> {
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
    match emoji::scan_root(repo, exclude.as_ref(), &extra, staged) {
        Err(message) => Some(fail(label, "native", &format!("{message}\n"), start)),
        Ok((hits, _)) if !hits.is_empty() => Some(fail(
            label,
            "native",
            &emoji::format_report(&hits, staged, &cfg.allow),
            start,
        )),
        Ok(_) => {
            ok(label, start);
            None
        }
    }
}

#[must_use]
pub fn step_markers(repo: &std::path::Path, staged: bool) -> Option<i32> {
    // 2. A conflict marker that reaches a commit is a merge walked away from.
    let start = begin("no conflict markers");
    match markers::scan_root(repo, staged) {
        Err(message) => Some(fail(
            "no conflict markers",
            "native",
            &format!("{message}\n"),
            start,
        )),
        Ok(bad) if !bad.is_empty() => Some(fail(
            "no conflict markers",
            "native",
            &markers::format_report(&bad),
            start,
        )),
        Ok(_) => {
            ok("no conflict markers", start);
            None
        }
    }
}

#[must_use]
pub fn step_length(repo: &std::path::Path, cfg: &gatesrc::Gatesrc, staged: bool) -> Option<i32> {
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
    match length::scan_root(repo, max, filter.as_ref(), staged) {
        Err(message) => Some(fail(&label, "native", &format!("{message}\n"), start)),
        Ok((over, _)) if !over.is_empty() => Some(fail(
            &label,
            "native",
            &length::format_report(&over, max),
            start,
        )),
        Ok(_) => {
            ok(&label, start);
            None
        }
    }
}

#[must_use]
pub fn step_ceiling(
    repo: &std::path::Path,
    cfg: &gatesrc::Gatesrc,
    checks: &std::path::Path,
    staged: bool,
) -> Option<i32> {
    // Both bounds read a baseline and measure files: at --staged, the index.
    let Some(view) = IndexView::repo(repo, staged && cfg.line_baseline.is_some()) else {
        return Some(2);
    };
    // 4. An exemption from the cap is not an exemption from any bound at all.
    if cfg.max_lines.is_some() && cfg.line_baseline.is_some() {
        let mut args = vec![
            "check_exclusion_has_ceiling.py".to_owned(),
            "--max".to_owned(),
            cfg.max_lines.unwrap_or(0).to_string(),
            "--baseline".to_owned(),
            cfg.line_baseline.clone().unwrap_or_default(),
        ];
        if !cfg.line_exclude.is_empty() {
            args.push("--line-exclude".to_owned());
            args.push(cfg.line_exclude.clone());
        }
        if !cfg.line_unbounded.is_empty() {
            args.push("--unbounded".to_owned());
            args.push(cfg.line_unbounded.clone());
        }
        let code = delegated(
            checks,
            view.root(),
            "line-cap exemptions carry a ceiling",
            "python3",
            &args,
        );
        if code != 0 {
            return Some(code);
        }
    } else if !cfg.line_exclude.is_empty() {
        eprintln!("⚠ GOH_LINE_EXCLUDE is set but GOH_LINE_BASELINE is not — an exempted file");
        eprintln!("⚠   is bounded by nothing. Point GOH_LINE_BASELINE at the ratchet baseline.");
    }
    // 4b. ...and the ceilings are enforced here, not left to each repo's own
    // gate script: a baseline nobody ratchets is a list, not a bound.
    if let Some(baseline) = cfg.line_baseline.as_deref() {
        if view.root().join(baseline).is_file() {
            let measure = format!(
                "python3 '{}' '{baseline}'",
                checks.join("loc_of_baseline_files.py").display()
            );
            let args = vec![
                "check_baseline_ratchet.py".to_owned(),
                "--baseline".to_owned(),
                baseline.to_owned(),
                "--current-from-command".to_owned(),
                measure,
            ];
            let code = delegated(
                checks,
                view.root(),
                "cap-exempt files within their ceilings",
                "python3",
                &args,
            );
            if code != 0 {
                return Some(code);
            }
        }
    }
    None
}

#[must_use]
pub fn step_corpus(
    repo: &std::path::Path,
    cfg: &gatesrc::Gatesrc,
    checks: &std::path::Path,
    staged: bool,
) -> Option<i32> {
    // 5. Skills corpus (opt-in; the corpus may live outside the repo).
    if !cfg.skills_corpus {
        return None;
    }
    let root = cfg
        .skills_root
        .clone()
        .unwrap_or_else(|| repo.to_string_lossy().into_owned());
    if !std::path::Path::new(&root).is_dir() {
        eprintln!("⚠ skills corpus not present at {root} — NOT checked");
        return None;
    }
    let view = match crate::index_view::IndexView::at_scope(repo, &root, staged) {
        Ok(view) => view,
        Err(outcome) => return outcome,
    };
    let mut args = vec![
        "check_skills_corpus.py".to_owned(),
        "--root".to_owned(),
        view.root_arg(),
    ];
    if let Some(words) = &cfg.skills_max_words {
        args.push("--max-words".to_owned());
        args.push(words.clone());
    }
    let code = delegated(checks, repo, "skills corpus", "python3", &args);
    (code != 0).then_some(code)
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
pub fn step_secrets(repo: &std::path::Path, cfg: &gatesrc::Gatesrc, staged: bool) -> Option<i32> {
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
    match secrets::scan_root(repo, exclude.as_ref(), staged) {
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
    cfg: &gatesrc::Gatesrc,
    checks: &std::path::Path,
    staged: bool,
) -> Option<i32> {
    // 7b. Hard-coded home paths (opt-in per repo; delegated to the Python
    // checker, the same file the shell pipeline runs). A location under
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
    let mut args = vec!["check_no_home_paths.py".to_owned()];
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

/// Lines of captured log printed on step failure. Mirrors `GOH_TAIL`.
fn tail_len() -> usize {
    std::env::var("GOH_TAIL")
        .ok()
        .and_then(|raw| raw.parse().ok())
        .unwrap_or(60)
}

/// `GOH_TIME` appends per-step seconds. Off unless set non-empty.
fn show_time() -> bool {
    std::env::var("GOH_TIME").is_ok_and(|raw| !raw.is_empty())
}

/// Announce a step; returns its start time.
fn begin(label: &str) -> Instant {
    println!("· {label}");
    Instant::now()
}

/// Announce a passed step.
fn ok(label: &str, start: Instant) {
    if show_time() {
        println!("✓ {label} ({}s)", start.elapsed().as_secs());
    } else {
        println!("✓ {label}");
    }
}

/// Fail the gate like `goh_step`: blank line, failing-output tail, blank
/// line, then the failure. `how` names the runner (`native` or the command).
fn fail(label: &str, how: &str, report: &str, _start: Instant) -> i32 {
    println!();
    let lines: Vec<&str> = report.lines().collect();
    let tail = tail_len();
    let from = lines.len().saturating_sub(tail);
    for line in &lines[from..] {
        eprintln!("{line}");
    }
    println!();
    eprintln!("✗ structural: {label} failed ({how})");
    1
}

/// Run a remaining checker as a subprocess, capturing merged output.
/// The child runs with the target repo as its working directory (checkers
/// resolve the repo from the cwd); the script itself resolves under the
/// shared checkout. Returns 0 on pass, 1 on any failure (fail-fast).
fn delegated(
    checks: &std::path::Path,
    repo: &std::path::Path,
    label: &str,
    program: &str,
    args: &[String],
) -> i32 {
    let start = begin(label);
    let command_line = args.join(" ");
    let (code, out) = run_child(program, args, checks, repo);
    if code == 0 {
        ok(label, start);
        0
    } else {
        fail(
            label,
            &format!("command: {program} {command_line}"),
            &out,
            start,
        )
    }
}

/// Run `program` with `args` (first arg: script name under `checks`),
/// working directory `repo`, merging stdout and stderr like `goh_step`'s
/// capture. Returns (exit code, merged output).
fn run_child(
    program: &str,
    args: &[String],
    checks: &std::path::Path,
    repo: &std::path::Path,
) -> (i32, String) {
    let script = checks.join(&args[0]);
    let mut cmd = Command::new(program);
    cmd.arg(&script);
    cmd.args(&args[1..]);
    cmd.current_dir(repo);
    match cmd.output() {
        Err(e) => (127, format!("spawn failed: {e}\n")),
        Ok(out) => {
            let code = out.status.code().unwrap_or(1);
            let mut text = String::from_utf8_lossy(&out.stdout).into_owned();
            let err = String::from_utf8_lossy(&out.stderr);
            if !err.is_empty() {
                if !text.is_empty() && !text.ends_with('\n') {
                    text.push('\n');
                }
                text.push_str(&err);
            }
            (code, text)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn checks_dir() -> std::path::PathBuf {
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../checks")
    }

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
        // At the ceiling: both the "carries a ceiling" and the ratchet pass.
        assert_eq!(step_ceiling(repo, &cfg, &checks_dir(), false), None);
        // One line over: the ratchet fails the step.
        std::fs::write(repo.join("src/big.rs"), lines(601)).expect("write");
        git(repo, &["add", "-A"]);
        assert!(step_ceiling(repo, &cfg, &checks_dir(), false).is_some_and(|c| c != 0));
        // Shrinking is always allowed.
        std::fs::write(repo.join("src/big.rs"), lines(550)).expect("write");
        git(repo, &["add", "-A"]);
        assert_eq!(step_ceiling(repo, &cfg, &checks_dir(), false), None);
        // A baseline path that does not exist: the exempt file has no bound at
        // all, which the ceiling check reports -- the ratchet never runs.
        let unbaselined = gatesrc::Gatesrc {
            line_baseline: Some("missing.txt".to_owned()),
            ..cfg
        };
        assert!(step_ceiling(repo, &unbaselined, &checks_dir(), false).is_some_and(|c| c != 0));
    }
}
