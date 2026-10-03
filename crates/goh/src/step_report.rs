//! How a structural step reports.
//!
//! The announce / pass / fail lines every step prints, and the subprocess
//! runner for the checkers still delegated to Python. Split out of
//! `steps.rs` when the native ports pushed it past the line cap; the steps
//! decide, this prints.

use std::process::Command;
use std::time::Instant;

/// `GOH_TAIL`'s default: lines of a failing step's captured output shown
/// (documented in `docs/config.md`, as `gates/_common.sh` sets it).
const DEFAULT_TAIL_LINES: usize = 60;

/// The status a shell gives a command it could not start (POSIX 127),
/// used when the checker itself fails to spawn.
const SPAWN_FAILED: i32 = 127;

/// Lines of captured log printed on step failure. Mirrors `GOH_TAIL`.
fn tail_len() -> usize {
    std::env::var("GOH_TAIL")
        .ok()
        .and_then(|raw| raw.parse().ok())
        .unwrap_or(DEFAULT_TAIL_LINES)
}

/// `GOH_TIME` appends per-step seconds. Off unless set non-empty.
fn show_time() -> bool {
    std::env::var("GOH_TIME").is_ok_and(|raw| !raw.is_empty())
}

/// Announce a step; returns its start time.
pub(crate) fn begin(label: &str) -> Instant {
    println!("· {label}");
    Instant::now()
}

/// Announce a passed step.
pub(crate) fn ok(label: &str, start: Instant) {
    if show_time() {
        println!("✓ {label} ({}s)", start.elapsed().as_secs());
    } else {
        println!("✓ {label}");
    }
}

/// Fail the gate like `goh_step`: blank line, failing-output tail, blank
/// line, then the failure. `how` names the runner (`native`, or the command
/// with the checker's own path in it) and is followed by the docs route, so the
/// failure says both WHAT ran and WHERE the rule for it is written.
pub(crate) fn fail(label: &str, how: &str, report: &str, _start: Instant) -> i32 {
    println!();
    let lines: Vec<&str> = report.lines().collect();
    let tail = tail_len();
    let from = lines.len().saturating_sub(tail);
    for line in &lines[from..] {
        eprintln!("{line}");
    }
    println!();
    eprintln!("✗ structural: {label} failed ({how})");
    eprintln!("  → {}", docs_route());
    1
}

/// Where the rules behind a failing step are WRITTEN.
///
/// docs/SUPERSOTA.md R8: a statement about the house belongs where the failure
/// happens. A gate names the file that failed and says where it lives; a docs
/// page states the rule; the output routes to it. This used to print the bare
/// filename from the tier that actually runs, so a session that hit a surprising
/// house gate had no way to find the file from the message it was given — the
/// Python tier printed an absolute path and the native one did not.
///
/// Both pages, because they answer different questions: `map.md` says what a
/// gate or checker DOES, `config.md` says what a `GOH_*` key MEANS, and a step
/// that failed on configuration needs the second more than the first.
fn docs_route() -> String {
    let root = crate::goh_root();
    let root = root.display();
    format!("{root}/docs/map.md documents this gate; its GOH_* keys are in {root}/docs/config.md")
}

/// The command line as it was run, with the checker's FULL path in argument 0.
///
/// The bare filename was the R8 defect: `command: python3 check_md_links.py`
/// names something the reader has to go and find, from the tier that runs in
/// every repo. `dir` is what the child was actually handed, so this is the same
/// string the OS saw.
fn command_line(program: &str, args: &[String], dir: &std::path::Path) -> String {
    let mut spelled_out = args.to_vec();
    spelled_out[0] = dir.join(&args[0]).to_string_lossy().into_owned();
    format!("command: {program} {}", spelled_out.join(" "))
}

/// Run a remaining checker as a subprocess, capturing merged output.
/// The child runs with the target repo as its working directory (checkers
/// resolve the repo from the cwd); the script itself resolves under the
/// shared checkout. Returns 0 on pass, 1 on any failure (fail-fast).
pub(crate) fn delegated(
    checks: &std::path::Path,
    repo: &std::path::Path,
    label: &str,
    program: &str,
    args: &[String],
) -> i32 {
    let start = begin(label);
    let (code, out) = run_child(program, args, checks, repo);
    if code == 0 {
        ok(label, start);
        0
    } else {
        fail(label, &command_line(program, args, checks), &out, start)
    }
}

/// Run `program` with `args` (first arg: script name under `checks`),
/// working directory `repo`, merging stdout and stderr like `goh_step`'s
/// capture. Returns (exit code, merged output).
pub(crate) fn run_child(
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
        Err(e) => (SPAWN_FAILED, format!("spawn failed: {e}\n")),
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
