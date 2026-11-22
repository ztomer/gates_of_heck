//! `goh step`: a command under a ceiling -- the native port of `lib/bounded_run.py`'s CLI.
//!
//! Roadmap 4C.1. Every gate step ran that wrapper, and its Python start-up (26 ms even lean) was
//! paid per step, per gate, in every consumer; this is one exec of a binary already on the box.
//!
//! The contract is the Python's, pinned against both by `tests/test_step_wrapper_cli.py`:
//! * the step runs in its OWN process group, stdin closed, output inherited;
//! * its exit code passes through; a step that cannot start is 127;
//! * at the ceiling the whole group gets TERM, then -- after `--grace` -- KILL, and the run is
//!   `TIMED OUT after Ns: <label>`, exit 124;
//! * a TERM/INT/HUP to the wrapper sweeps the group the same way and exits 128+signal -- EXCEPT a
//!   signal the caller ignored at entry, which stays ignored (`nohup`'s contract; only `sigaction`
//!   can tell, hence `goh_sys::ignored_at_entry`);
//! * a step that exits leaving members in its group is named (`left N process(es) ...`);
//! * with `GOH_TIMINGS` set, one JSON line, tier `step`, and the child learns its parent's label;
//!   the line carries the step tree's CPU (`cpu_ms`, from the rusage of what this wrapper reaped)
//!   beside its wall time, because on a box that is never quiet the wall measures the box too.

use std::os::unix::process::CommandExt;
use std::process::{Command, ExitStatus, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant};

const TIMEOUT_EXIT: i32 = 124;
const POLL: Duration = Duration::from_millis(50);
const STOPS: [i32; 3] = [
    signal_hook::consts::SIGTERM,
    signal_hook::consts::SIGINT,
    signal_hook::consts::SIGHUP,
];

enum Event {
    Exited(std::io::Result<ExitStatus>),
    Stopped(i32),
}

#[derive(Clone, Copy)]
pub(crate) struct Bounds {
    pub(crate) wall: u64,
    pub(crate) cpu: Option<u64>,
    pub(crate) stall: Option<u64>,
}

/// Parse `--timeout`, `--cpu-timeout`, and `--stall` from shared `StepBoundArgs`.
pub(crate) fn parse_bound_args(b: &crate::cli_args::StepBoundArgs, who: &str) -> Option<Bounds> {
    let wall = ceiling(&b.timeout, who)?;
    let cpu = opt_ceiling(b.cpu_timeout.as_deref(), "--cpu-timeout", who).ok()?;
    let stall = opt_ceiling(b.stall.as_deref(), "--stall", who).ok()?;
    Some(Bounds { wall, cpu, stall })
}

/// `goh step`. `timeout` is the raw flag, so a non-positive or unreadable one is refused here
/// exactly as the Python refuses it.
#[must_use]
pub fn run_cli(a: &crate::cli_args::StepArgs) -> i32 {
    let Some(bounds) = parse_bound_args(&a.bound, "goh step") else {
        return 2;
    };
    if a.bound.command.is_empty() {
        eprintln!("goh step: no command: goh step --timeout N -- CMD [ARG...]");
        return 2;
    }
    let label = if a.bound.label.is_empty() {
        a.bound.command.join(" ")
    } else {
        a.bound.label.clone()
    };
    let span = Span::begin();
    let (code, left) = bounded(&a.bound.command, bounds, a.bound.grace, &label, None);
    if !left.is_empty() {
        let shown: Vec<String> = left.iter().take(20).map(ToString::to_string).collect();
        eprintln!(
            "  left {} running under it: {}",
            goh_tui::counted_as(left.len(), "process", "processes"),
            shown.join(", ")
        );
    }
    record(&label, &span, code);
    code
}

/// Parse one positive-integer bound flag (`--timeout`, `--cpu-timeout`, `--stall`).
fn parse_pos(raw: &str, flag: &str, who: &str) -> Option<u64> {
    match raw.parse::<i64>() {
        Ok(limit) if limit > 0 => Some(limit.unsigned_abs()),
        Ok(limit) => {
            eprintln!("{who}: {flag} must be positive (got {limit})");
            None
        }
        Err(_) => {
            eprintln!("{who}: {flag} must be an integer of seconds (got {raw})");
            None
        }
    }
}

/// Parse a `--timeout` flag: `Some(seconds)`, or `None` after printing `who`'s refusal.
pub(crate) fn ceiling(timeout: &str, who: &str) -> Option<u64> {
    parse_pos(timeout, "--timeout", who)
}

/// Parse an optional bound flag (`--cpu-timeout`, `--stall`); `Err(())` after refusal.
pub(crate) fn opt_ceiling(raw: Option<&str>, flag: &str, who: &str) -> Result<Option<u64>, ()> {
    raw.map_or(Ok(None), |v| parse_pos(v, flag, who).map(Some).ok_or(()))
}

/// Gate `(wall_backstop, cpu_budget, stall)` derived from a nominal step ceiling `limit`.
pub(crate) fn gate_bounds(limit: u64) -> (u64, u64, u64) {
    let ncpu = std::thread::available_parallelism().map_or(1, |n| n.get() as u64);
    let env_u64 = |key: &str| {
        std::env::var(key)
            .ok()
            .and_then(|v| v.trim().parse::<u64>().ok())
            .filter(|&n| n > 0)
    };
    let cpu = env_u64("GOH_STEP_CPU_TIMEOUT").unwrap_or_else(|| limit.saturating_mul(ncpu));
    let wall = env_u64("GOH_STEP_WALL_BACKSTOP").unwrap_or_else(|| limit.saturating_mul(4));
    (wall, cpu, limit)
}

/// Run `argv` under the ceiling; `(its code, the pids still in its group when it exited)`. With
/// `out`, the step's stdout and stderr are appended to that file, merged.
pub(crate) fn bounded(
    argv: &[String],
    bounds: Bounds,
    grace: u64,
    label: &str,
    out: Option<&std::path::Path>,
) -> (i32, Vec<i32>) {
    let mut cmd = Command::new(&argv[0]);
    cmd.args(&argv[1..]).stdin(Stdio::null()).process_group(0);
    if let Some(path) = out {
        match std::fs::OpenOptions::new()
            .append(true)
            .create(true)
            .open(path)
        {
            Ok(file) => {
                let err = file.try_clone().map_or_else(|_| Stdio::null(), Stdio::from);
                cmd.stdout(Stdio::from(file)).stderr(err);
            }
            Err(e) => {
                eprintln!("goh step: cannot open {}: {e}", path.display());
                return (2, Vec::new());
            }
        }
    }
    if std::env::var_os("GOH_TIMINGS").is_some_and(|v| !v.is_empty()) {
        cmd.env("GOH_TIMINGS_PARENT", label);
    }
    // The kind labels THIS gate's steps; a gate the step runs declares its own, and every other
    // child (a test suite's fixtures, a tool) declares none (5.3).
    cmd.env_remove("GOH_TIMINGS_KIND");
    // The stops are taken BEFORE the spawn: a stop between the spawn and the listener would meet the
    // default action, kill this wrapper, and leave the step running unowned. Taken first, it waits
    // in the channel and sweeps the step the moment there is one (bounded_run.py holds it the same
    // way). A later stop is absorbed by the still-registered listener, so it cannot cut a sweep short.
    let (tx, rx) = mpsc::channel();
    let handled: Vec<i32> = STOPS
        .into_iter()
        .filter(|s| !goh_sys::ignored_at_entry(*s))
        .collect();
    let signals = signal_hook::iterator::Signals::new(&handled).ok();
    let listener = signals.as_ref().map(signal_hook::iterator::Signals::handle);
    if let Some(mut signals) = signals {
        let tx = tx.clone();
        std::thread::spawn(move || {
            if let Some(sig) = signals.forever().next() {
                let _ = tx.send(Event::Stopped(sig));
            }
        });
    }
    let close = || {
        if let Some(handle) = &listener {
            handle.close();
        }
    };
    let mut child = match cmd.spawn() {
        Ok(child) => child,
        Err(e) => {
            close();
            eprintln!("goh step: cannot start {label}: {e}");
            return (127, Vec::new());
        }
    };
    let Ok(pgid) = i32::try_from(child.id()) else {
        let _ = child.kill();
        let _ = child.wait();
        close();
        eprintln!("goh step: {label}: a pid that is not a pid");
        return (127, Vec::new());
    };
    let waiter = std::thread::spawn(move || {
        let _ = tx.send(Event::Exited(child.wait()));
    });
    let (code, left) = wait_bounded(&rx, pgid, bounds, grace, label);
    close();
    drop(waiter);
    (code, left)
}

fn finish_exit(status: std::io::Result<std::process::ExitStatus>, pgid: i32) -> (i32, Vec<i32>) {
    let code = status.map_or(127, crate::exit_status::code_of);
    (code, survivors(pgid))
}

fn finish_stop(
    rx: &mpsc::Receiver<Event>,
    pgid: i32,
    sig: i32,
    grace: u64,
    label: &str,
) -> (i32, Vec<i32>) {
    sweep(pgid, grace);
    let _ = rx.recv_timeout(Duration::from_secs(grace.max(1)));
    eprintln!(
        "STOPPED by {}: {label} -- its process group was swept",
        signal_name(sig)
    );
    (128 + sig, Vec::new())
}

fn finish_timeout(rx: &mpsc::Receiver<Event>, pgid: i32, grace: u64, msg: &str) -> (i32, Vec<i32>) {
    sweep(pgid, grace);
    let _ = rx.recv_timeout(Duration::from_secs(grace.max(1)));
    eprintln!("{msg}");
    (TIMEOUT_EXIT, Vec::new())
}

fn wait_bounded(
    rx: &mpsc::Receiver<Event>,
    pgid: i32,
    bounds: Bounds,
    grace: u64,
    label: &str,
) -> (i32, Vec<i32>) {
    if bounds.cpu.is_none() && bounds.stall.is_none() {
        return match rx.recv_timeout(Duration::from_secs(bounds.wall)) {
            Ok(Event::Exited(status)) => finish_exit(status, pgid),
            Ok(Event::Stopped(sig)) => finish_stop(rx, pgid, sig, grace, label),
            Err(_) => {
                let msg = format!("TIMED OUT after {}s: {label}", bounds.wall);
                finish_timeout(rx, pgid, grace, &msg)
            }
        };
    }
    let start = Instant::now();
    let mut last_progress = start;
    let mut peak_cpu = 0.0_f64;
    loop {
        match rx.recv_timeout(POLL) {
            Ok(Event::Exited(status)) => return finish_exit(status, pgid),
            Ok(Event::Stopped(sig)) => return finish_stop(rx, pgid, sig, grace, label),
            Err(mpsc::RecvTimeoutError::Disconnected) => return (127, Vec::new()),
            Err(mpsc::RecvTimeoutError::Timeout) => {
                if let Some((cpu_s, runnable)) = goh_sys::pgid_cpu_and_runnable(pgid) {
                    if cpu_s > peak_cpu || runnable > 0 {
                        peak_cpu = peak_cpu.max(cpu_s);
                        last_progress = Instant::now();
                    }
                } else {
                    last_progress = Instant::now();
                }
                if let Some(cpu_limit) = bounds.cpu {
                    let cpu_dur = Duration::try_from_secs_f64(peak_cpu).unwrap_or(Duration::MAX);
                    if cpu_dur >= Duration::from_secs(cpu_limit) {
                        let msg = format!("TIMED OUT after {cpu_limit}s: {label} (CPU budget)");
                        return finish_timeout(rx, pgid, grace, &msg);
                    }
                }
                if let Some(stall_limit) = bounds.stall {
                    if last_progress.elapsed() >= Duration::from_secs(stall_limit) {
                        let msg = format!("TIMED OUT after {stall_limit}s: {label} (stalled)");
                        return finish_timeout(rx, pgid, grace, &msg);
                    }
                }
                if start.elapsed() >= Duration::from_secs(bounds.wall) {
                    let msg = format!("TIMED OUT after {}s: {label}", bounds.wall);
                    return finish_timeout(rx, pgid, grace, &msg);
                }
            }
        }
    }
}

/// TERM the whole group, wait up to `grace` for it to empty, then KILL whatever is left.
fn sweep(pgid: i32, grace: u64) {
    let _ = goh_sys::killpg(pgid, signal_hook::consts::SIGTERM);
    let deadline = Instant::now() + Duration::from_secs(grace);
    while goh_sys::group_alive(pgid) && Instant::now() < deadline {
        std::thread::sleep(POLL);
    }
    let _ = goh_sys::killpg(pgid, signal_hook::consts::SIGKILL);
}

/// The pids left in `pgid` after its leader exited -- the step's leaks. Asked of the group first
/// (one syscall); the process table is read only when something is there.
pub(crate) fn survivors(pgid: i32) -> Vec<i32> {
    if !goh_sys::group_alive(pgid) {
        return Vec::new();
    }
    let table = Command::new("ps")
        .args(["-Ao", "pid=,pgid="])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).into_owned())
        .unwrap_or_default();
    table
        .lines()
        .filter_map(|l| {
            let mut f = l.split_whitespace();
            let member: i32 = f.next()?.parse().ok()?;
            let group: i32 = f.next()?.parse().ok()?;
            (group == pgid && member != pgid).then_some(member)
        })
        .collect()
}

const fn signal_name(sig: i32) -> &'static str {
    match sig {
        signal_hook::consts::SIGTERM => "SIGTERM",
        signal_hook::consts::SIGINT => "SIGINT",
        signal_hook::consts::SIGHUP => "SIGHUP",
        _ => "a signal",
    }
}

/// When a step began, by the wall clock and by the CPU its reaped children had spent.
pub(crate) struct Span {
    start: Instant,
    cpu_ms: f64,
}

impl Span {
    pub(crate) fn begin() -> Self {
        Self {
            start: Instant::now(),
            cpu_ms: goh_sys::children_cpu_ms(),
        }
    }
}

/// The `GOH_TIMINGS` line, in `lib/step_timings.py`'s shape, tier `step`.
pub(crate) fn record(label: &str, span: &Span, rc: i32) {
    use std::io::Write as _;
    let cpu_ms = goh_sys::children_cpu_ms() - span.cpu_ms;
    let Some(path) = std::env::var_os("GOH_TIMINGS").filter(|p| !p.is_empty()) else {
        return;
    };
    let cwd = std::env::var("GOH_TIMINGS_CWD").unwrap_or_else(|_| {
        std::env::current_dir().map_or_else(|_| String::new(), |p| p.display().to_string())
    });
    let parent = std::env::var("GOH_TIMINGS_PARENT").unwrap_or_default();
    let cut = |s: &str| s.chars().take(300).collect::<String>();
    let tenths = |ms: f64| (ms * 10.0).round() / 10.0;
    let ms = tenths(span.start.elapsed().as_secs_f64() * 1000.0);
    let mut row = serde_json::json!({
        "label": cut(label), "ms": ms, "rc": rc, "tier": "step", "parent": cut(&parent),
        "cwd": cut(&cwd), "cpu_ms": tenths(cpu_ms),
    });
    // The gate's declared kind, so the report divides this step by its kind's control (5.3).
    if let Some(kind) = std::env::var("GOH_TIMINGS_KIND")
        .ok()
        .filter(|k| !k.is_empty())
    {
        row["kind"] = serde_json::Value::String(kind.chars().take(40).collect());
    }
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
