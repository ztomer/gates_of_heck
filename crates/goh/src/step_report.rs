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

/// `GOH_STEP_TIMEOUT`'s default, in seconds. THE SAME constant as `gates/_common.sh`'s
/// `_goh_step_default_timeout`, because a ceiling that differs by tier is not a ceiling.
///
/// Measured rather than chosen: the slowest single step in this estate is mediaops' rust gate at
/// 168 s and the whole pytest suite is 76 s, so 1800 is an order of magnitude above anything
/// legitimate. The point is not the number — it is that it EXISTS, is printed on every step line,
/// and that exceeding it says "TIMED OUT after Ns" instead of hanging until something kills the run
/// and takes the report with it (`media_server`, 2026-10-03: nine orphans, each holding the cargo
/// build lock, every later `cargo test` blocked, and nothing anywhere said why).
const fn default_timeout() -> u64 {
    1800
}

/// The ceiling a spawned tool runs under: the configured one, or the default
/// for an explicit opt-out (`run_child`'s rule -- one bound, decided once).
pub(crate) fn ceiling_secs() -> u64 {
    ceiling().unwrap_or_else(default_timeout)
}

/// The ceiling in force, or `None` when `GOH_STEP_TIMEOUT=0` opted out.
fn ceiling() -> Option<u64> {
    let raw = std::env::var("GOH_STEP_TIMEOUT").ok();
    match raw.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        None => Some(default_timeout()),
        Some("0") => None,
        // A typo'd ceiling must never silently leave a step UNBOUNDED, which is the defect. Named
        // and refused rather than guessed at: a wrong guess reads as a working gate.
        Some(other) => other.parse::<u64>().ok().filter(|n| *n > 0).or_else(|| {
            eprintln!(
                "✗ structural: GOH_STEP_TIMEOUT must be a non-negative integer of seconds \
                 (got {other:?}) — refusing to run a step with an unknown ceiling"
            );
            None
        }),
    }
}

/// The suffix both tiers put on a step line, so the parity comparison is over the same string.
///
/// A step whose ceiling is off says `UNBOUNDED` rather than looking identical to a bounded one:
/// `docs/contracts.md` R4 is that a step which does not run must be visible, and an unbounded step
/// that reads like a bounded one is the same shape.
fn ceiling_suffix() -> String {
    ceiling().map_or_else(
        || " (UNBOUNDED — GOH_STEP_TIMEOUT=0)".to_owned(),
        |n| format!(" (≤{n}s)"),
    )
}

/// Announce a step; returns its start time.
pub(crate) fn begin(label: &str) -> Instant {
    println!("· {label}{}", ceiling_suffix());
    Instant::now()
}

/// Append this step's line to `$GOH_TIMINGS`, the same shape `lib/step_timings.py` writes.
///
/// ONE `write` of one line under `O_APPEND`, below `PIPE_BUF`, so concurrent gates interleave
/// whole lines. Every failure is dropped on purpose: the instrument must never turn a gate red.
fn record(label: &str, start: Instant, rc: i32) {
    let Some(path) = std::env::var_os("GOH_TIMINGS").filter(|p| !p.is_empty()) else {
        return;
    };
    let cwd = std::env::current_dir().map_or_else(|_| String::new(), |p| p.display().to_string());
    let parent = std::env::var("GOH_TIMINGS_PARENT").unwrap_or_default();
    let ms = (start.elapsed().as_secs_f64() * 10_000.0).round() / 10.0;
    record_to(std::path::Path::new(&path), label, ms, rc, &parent, &cwd);
}

/// The write itself, environment-free so it is testable: one JSON line appended to `path`.
pub(crate) fn record_to(
    path: &std::path::Path,
    label: &str,
    ms: f64,
    rc: i32,
    parent: &str,
    cwd: &str,
) {
    use std::io::Write as _;
    let row = serde_json::json!({
        "label": label, "ms": ms, "rc": rc, "tier": "native", "parent": parent, "cwd": cwd,
    });
    let mut line = row.to_string();
    line.push('\n');
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .append(true)
        .create(true)
        .open(path)
    {
        let _ = f.write_all(line.as_bytes());
    }
}

/// Announce a passed step.
pub(crate) fn ok(label: &str, start: Instant) {
    record(label, start, 0);
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
pub(crate) fn fail(label: &str, how: &str, report: &str, start: Instant) -> i32 {
    record(label, start, 1);
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

/// `how` for a failing PORTED step (Phase N1): the native source that ran and
/// the Python checker it ports, both as full paths -- R8, so the reader can
/// find the rule from the message, as a delegated step's command line lets them.
#[must_use]
pub(crate) fn ported(source: &str, reference: &str) -> String {
    let root = crate::goh_root();
    format!(
        "native {}, the port of {}",
        root.join(source).display(),
        root.join("checks").join(reference).display()
    )
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
    if crate::prefetch::record_if_collecting(program, args) {
        return 0;
    }
    let start = begin(label);
    let (code, out) = crate::prefetch::take(program, args)
        .unwrap_or_else(|| run_child(program, args, checks, repo));
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
///
/// THROUGH `lib/bounded_run.py`, which is the whole point of this function's
/// shape. `Command::output()` blocks with no ceiling of its own — the very
/// disposition `checks/check_no_unreaped_spawn.py` MEASURES and documents as
/// "reaps by blocking, so it cannot leak but has no timeout" — and a structural
/// step is a whole-tree scan of another repo. Bounding the shell tier and
/// leaving this one unbounded is one hang with two answers, so BOTH tiers route
/// through the one implementation: same ceiling, same whole-subtree sweep on
/// expiry (a group SIGKILL, where a direct kill would leave the grandchildren),
/// same `TIMED OUT after Ns`, same exit 124.
///
/// Exit 127 (the child's own "could not start") is passed through rather than
/// collapsed, and a missing `bounded_run.py` is named instead of silently
/// running unbounded.
pub(crate) fn run_child(
    program: &str,
    args: &[String],
    checks: &std::path::Path,
    repo: &std::path::Path,
) -> (i32, String) {
    let runner = crate::goh_root().join("lib").join("bounded_run.py");
    if !runner.is_file() {
        return (
            SPAWN_FAILED,
            format!(
                "the step runner is missing: {}\n  every structural step is supposed to run under \
                 a ceiling, and without it they would run unbounded\n",
                runner.display()
            ),
        );
    }
    let mut cmd = Command::new("python3");
    cmd.arg(&runner);
    // `ceiling()` is `None` only for an explicit opt-out, and bounded_run's own default would
    // then apply anyway -- so pass the DEFAULT rather than the opt-out, and let the step line be
    // the only place the opt-out is visible. Two places deciding one bound is how they disagree.
    cmd.arg("--timeout")
        .arg(ceiling().unwrap_or_else(default_timeout).to_string());
    cmd.arg("--label").arg(args[0].clone());
    cmd.arg("--");
    cmd.arg(program);
    cmd.arg(checks.join(&args[0]));
    cmd.args(&args[1..]);
    cmd.current_dir(repo);
    // This tier records the step itself (`ok` / `fail`); the runner recording it too would
    // count every delegated step twice.
    cmd.env_remove("GOH_TIMINGS");
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

#[cfg(test)]
mod tests {
    #[test]
    fn a_timing_line_is_one_whole_json_object_and_a_bad_path_is_dropped() {
        let dir = tempfile::tempdir().unwrap_or_else(|e| panic!("tempdir: {e}"));
        let path = dir.path().join("t.jsonl");
        super::record_to(&path, "emoji", 12.5, 0, "outer", "/repo");
        super::record_to(&path, "markers", 3.0, 1, "", "/repo");
        let text = std::fs::read_to_string(&path).unwrap_or_default();
        let rows: Vec<serde_json::Value> = text
            .lines()
            .filter_map(|l| serde_json::from_str(l).ok())
            .collect();
        assert_eq!(rows.len(), 2, "{text}");
        assert_eq!(rows[0]["label"], "emoji");
        assert_eq!(rows[0]["parent"], "outer");
        assert_eq!(rows[1]["rc"], 1);
        // The instrument never fails a gate: an unwritable path is silently dropped.
        super::record_to(&dir.path().join("no/such/dir/t"), "x", 1.0, 0, "", "");
    }
}
