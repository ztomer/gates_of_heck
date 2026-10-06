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
//! * with `GOH_TIMINGS` set, one JSON line, tier `step`, and the child learns its parent's label.

use std::os::unix::process::{CommandExt, ExitStatusExt};
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

/// `goh step`. `timeout` is the raw flag, so a non-positive or unreadable one is refused here
/// exactly as the Python refuses it.
#[must_use]
pub fn run(timeout: &str, grace: u64, label: &str, argv: &[String]) -> i32 {
    let Ok(limit) = timeout.parse::<i64>() else {
        eprintln!("goh step: --timeout must be an integer of seconds (got {timeout})");
        return 2;
    };
    if limit <= 0 {
        eprintln!("goh step: --timeout must be positive (got {limit})");
        return 2;
    }
    if argv.is_empty() {
        eprintln!("goh step: no command: goh step --timeout N -- CMD [ARG...]");
        return 2;
    }
    let label = if label.is_empty() {
        argv.join(" ")
    } else {
        label.to_owned()
    };
    let start = Instant::now();
    let (code, left) = bounded(argv, limit.unsigned_abs(), grace, &label, None);
    if !left.is_empty() {
        let shown: Vec<String> = left.iter().take(20).map(ToString::to_string).collect();
        eprintln!(
            "  left {} process(es) running under it: {}",
            left.len(),
            shown.join(", ")
        );
    }
    record(&label, start, code);
    code
}

/// Parse a `--timeout` flag: `Some(seconds)`, or `None` after printing `who`'s refusal.
pub(crate) fn ceiling(timeout: &str, who: &str) -> Option<u64> {
    match timeout.parse::<i64>() {
        Ok(limit) if limit > 0 => Some(limit.unsigned_abs()),
        Ok(limit) => {
            eprintln!("{who}: --timeout must be positive (got {limit})");
            None
        }
        Err(_) => {
            eprintln!("{who}: --timeout must be an integer of seconds (got {timeout})");
            None
        }
    }
}

/// Run `argv` under the ceiling; `(its code, the pids still in its group when it exited)`. With
/// `out`, the step's stdout and stderr are appended to that file, merged.
pub(crate) fn bounded(
    argv: &[String],
    limit: u64,
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
    let mut child = match cmd.spawn() {
        Ok(child) => child,
        Err(e) => {
            eprintln!("goh step: cannot start {label}: {e}");
            return (127, Vec::new());
        }
    };
    let Ok(pgid) = i32::try_from(child.id()) else {
        let _ = child.kill();
        let _ = child.wait();
        eprintln!("goh step: {label}: a pid that is not a pid");
        return (127, Vec::new());
    };
    let (tx, rx) = mpsc::channel();
    let waiter = {
        let tx = tx.clone();
        std::thread::spawn(move || {
            let _ = tx.send(Event::Exited(child.wait()));
        })
    };
    let handled: Vec<i32> = STOPS
        .into_iter()
        .filter(|s| !goh_sys::ignored_at_entry(*s))
        .collect();
    let signals = signal_hook::iterator::Signals::new(&handled).ok();
    let listener = signals.as_ref().map(signal_hook::iterator::Signals::handle);
    if let Some(mut signals) = signals {
        std::thread::spawn(move || {
            if let Some(sig) = signals.forever().next() {
                let _ = tx.send(Event::Stopped(sig));
            }
        });
    }
    let mut left = Vec::new();
    let code = match rx.recv_timeout(Duration::from_secs(limit)) {
        Ok(Event::Exited(status)) => {
            let code = status.map_or(127, |s| {
                s.code().unwrap_or_else(|| 128 + s.signal().unwrap_or(0))
            });
            left = survivors(pgid);
            code
        }
        Ok(Event::Stopped(sig)) => {
            sweep(pgid, grace);
            let _ = rx.recv_timeout(Duration::from_secs(grace.max(1)));
            eprintln!(
                "STOPPED by {}: {label} -- its process group was swept",
                signal_name(sig)
            );
            128 + sig
        }
        Err(_) => {
            sweep(pgid, grace);
            let _ = rx.recv_timeout(Duration::from_secs(grace.max(1)));
            eprintln!("TIMED OUT after {limit}s: {label}");
            TIMEOUT_EXIT
        }
    };
    if let Some(handle) = listener {
        handle.close();
    }
    drop(waiter);
    (code, left)
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

/// The `GOH_TIMINGS` line, in `lib/step_timings.py`'s shape, tier `step`.
pub(crate) fn record(label: &str, start: Instant, rc: i32) {
    use std::io::Write as _;
    let Some(path) = std::env::var_os("GOH_TIMINGS").filter(|p| !p.is_empty()) else {
        return;
    };
    let cwd = std::env::var("GOH_TIMINGS_CWD").unwrap_or_else(|_| {
        std::env::current_dir().map_or_else(|_| String::new(), |p| p.display().to_string())
    });
    let parent = std::env::var("GOH_TIMINGS_PARENT").unwrap_or_default();
    let cut = |s: &str| s.chars().take(300).collect::<String>();
    let ms = (start.elapsed().as_secs_f64() * 10_000.0).round() / 10.0;
    let row = serde_json::json!({
        "label": cut(label), "ms": ms, "rc": rc, "tier": "step", "parent": cut(&parent),
        "cwd": cut(&cwd),
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
