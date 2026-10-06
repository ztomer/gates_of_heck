//! A child process under a CEILING, for the native steps that spawn a tool
//! themselves (ruff, shellcheck) instead of delegating to a Python checker.
//!
//! `step_report::run_child` routes delegated checkers through
//! `lib/bounded_run.py` so both tiers share one bound. A ported step that
//! spawns its tool directly must not be the unbounded exception -- one hang
//! with two answers is the defect that file exists for. So: the child gets
//! its own process GROUP (the safe `process_group(0)`; `unsafe` is denied
//! here), a waiter thread reports its exit, and at the ceiling the whole
//! group is killed (`kill -KILL -<pgid>`, a group kill without FFI) and the
//! run says `TIMED OUT`, as `bounded_run` does.

use std::io::{Read, Write};
use std::os::unix::process::CommandExt;
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::Duration;

/// What a bounded run ended as.
pub struct Ran {
    /// The exit code; `None` for a signal or a timeout.
    pub code: Option<i32>,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
    pub timed_out: bool,
}

/// After a ceiling kill, how long the pipe readers get before they are abandoned. A process
/// that left the group (`setsid`) can hold a pipe open forever, and a reader joined without a
/// bound turns the ceiling back into a hang -- measured: killing only the leader hung the run on
/// its child's `sleep 300`.
const DRAIN_GRACE: Duration = Duration::from_secs(2);

fn drain(mut pipe: impl Read + Send + 'static) -> mpsc::Receiver<Vec<u8>> {
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = pipe.read_to_end(&mut buf);
        let _ = tx.send(buf);
    });
    rx
}

/// Run `cmd` with `input` on stdin (or none), under `timeout`.
///
/// # Errors
/// The program could not be started.
pub fn run(cmd: &mut Command, input: Option<Vec<u8>>, timeout: Duration) -> std::io::Result<Ran> {
    cmd.process_group(0)
        .stdin(if input.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = cmd.spawn()?;
    let pid = child.id();
    let feeder = child.stdin.take().zip(input).map(|(mut stdin, bytes)| {
        std::thread::spawn(move || {
            let _ = stdin.write_all(&bytes);
        })
    });
    let out = child.stdout.take().map(drain);
    let err = child.stderr.take().map(drain);
    let (tx, rx) = mpsc::channel();
    let waiter = std::thread::spawn(move || {
        let _ = tx.send(child.wait());
    });
    let (code, timed_out) = rx.recv_timeout(timeout).map_or_else(
        |_| {
            let _ = Command::new("kill")
                .args(["-KILL", "--", &format!("-{pid}")])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
            let _ = rx.recv();
            (None, true)
        },
        |status| (status.ok().and_then(|s| s.code()), false),
    );
    let _ = waiter.join();
    if !timed_out {
        if let Some(f) = feeder {
            let _ = f.join();
        }
    }
    let take = |rx: Option<mpsc::Receiver<Vec<u8>>>| {
        rx.and_then(|rx| {
            if timed_out {
                rx.recv_timeout(DRAIN_GRACE).ok()
            } else {
                rx.recv().ok()
            }
        })
        .unwrap_or_default()
    };
    Ok(Ran {
        code,
        stdout: take(out),
        stderr: take(err),
        timed_out,
    })
}

/// The ceiling every step runs under (`GOH_STEP_TIMEOUT`, default 1800 s).
#[must_use]
pub fn step_ceiling() -> Duration {
    Duration::from_secs(crate::step_report::ceiling_secs())
}
