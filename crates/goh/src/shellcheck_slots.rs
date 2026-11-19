//! The machine-wide bound on concurrent shellcheck children (GOH.MEMADMIT).
//!
//! THE INCIDENT (2026-11-16). The machine thrashed for five hours and was restarted by hand: a
//! `JetsamEvent` counted 128 shellcheck processes holding 63.7 GiB (median 453 MB, max 1167 MB). Each
//! `goh shell-lint` ran one shellcheck per file on one thread per core, and about eight runs
//! overlapped (land gates, lanes' test runs under xdist, tests that lint whole trees), with nothing
//! counting across processes.
//!
//! THE MECHANISM is the gate slots' (`lib/gate_slot.py`): a counting semaphore over N files, a token
//! held while its holder holds an exclusive flock(2) on one of them. The files are never removed
//! (a removed lock file under a holder hands the next caller a fresh, unlocked inode), and the lock
//! lives exactly as long as the fd: Rust opens files close-on-exec, so no shellcheck inherits it,
//! and a lint killed by `SIGKILL` frees its tokens with its last fd. A token is held only around ONE
//! shellcheck child, and nothing is taken under it: it is the lock order's last rank.
//!
//! `GOH_SHELLCHECK_SLOTS=N` (default: one per `mib_per_token` of memory, between 1 and the core
//! count; `0` turns the bound off), in `GOH_SHELLCHECK_SLOT_DIR` (default `~/.cache/goh/slots/shellcheck`).
//! The suite does not privatise the directory as it does the gate slots: the memory is the
//! machine's, and the incident's children were mostly the suite's. Pinned by
//! `tests/test_shellcheck_slots.py`.
//!
//! NOT A GATE SLOT (`docs/decisions/D-0010`, GOH.ADMITSUITE): a gate slot admits a gate, and a lint
//! inside a gate is nested in its slot and would take none; a token admits one child, wherever it
//! runs. Measured 76 MB median, 2006 MB max per child (dataflow on), so a pool sized by memory.

use std::fs::{File, OpenOptions, TryLockError};
use std::path::{Path, PathBuf};
use std::process::Command;

/// Memory budgeted per token, FOUR times the largest child measured in the call's mode.
///
/// Dataflow ON (`style`/`info`): a shellcheck that follows its sources measured up to 2006 MB, so
/// 8 GiB. Dataflow OFF (`warning`/`error`, the default severity, `--extended-analysis=false`):
/// `/usr/bin/time -l` over every one of goh's 108 shell files measured a 34 MB median and a 255 MB
/// max (`gates/rust_gate.sh`; GOH.CIRED01392, 2026-11-18), so 1 GiB. Sized at 8 GiB for both, the
/// hosted runner's 7 GiB gave ONE token, and a cold lint of goh's tree ran one file at a time:
/// 224 s and 300 s of one test on release CI (v0.139.2, v0.139.3) against its 60 s ceiling.
#[must_use]
pub const fn mib_per_token(dataflow: bool) -> u64 {
    if dataflow {
        8 << 10
    } else {
        1 << 10
    }
}

/// The machine's token pool; `n == 0` is the bound turned off.
pub struct Slots {
    dir: PathBuf,
    n: usize,
}

/// One token, released when dropped (the kernel drops the flock with the fd).
pub struct Token {
    _file: Option<File>,
}

/// Bytes of physical memory, when the OS says.
fn physical_memory() -> Option<u64> {
    if cfg!(target_os = "linux") {
        let text = std::fs::read_to_string("/proc/meminfo").ok()?;
        let line = text.lines().find(|l| l.starts_with("MemTotal:"))?;
        let kib: u64 = line.split_whitespace().nth(1)?.parse().ok()?;
        return Some(kib * 1024);
    }
    let ran = crate::bounded::run(
        Command::new("sysctl").args(["-n", "hw.memsize"]),
        None,
        std::time::Duration::from_secs(10),
    )
    .ok()?;
    String::from_utf8_lossy(&ran.stdout).trim().parse().ok()
}

/// The default bound: one token per `mib_per_token(dataflow)`, at least one, at most one per core.
#[must_use]
pub fn default_slots(memory: Option<u64>, cores: usize, dataflow: bool) -> usize {
    let cores = cores.max(1);
    memory.map_or_else(
        || cores.div_ceil(2),
        |m| {
            let tokens = m / (mib_per_token(dataflow) << 20);
            usize::try_from(tokens)
                .unwrap_or(usize::MAX)
                .clamp(1, cores)
        },
    )
}

impl Slots {
    /// The pool the environment names, sized for a call with `dataflow` on or off; an unreadable
    /// `GOH_SHELLCHECK_SLOTS` is an error naming it.
    ///
    /// # Errors
    /// `GOH_SHELLCHECK_SLOTS` is not a non-negative integer, or the directory cannot be made.
    pub fn from_env(dataflow: bool) -> Result<Self, String> {
        let n = match std::env::var("GOH_SHELLCHECK_SLOTS") {
            Ok(v) if !v.is_empty() => v.parse::<usize>().map_err(|_| {
                format!(
                    "GOH_SHELLCHECK_SLOTS must be a non-negative integer of concurrent \
                     shellchecks (got '{v}')"
                )
            })?,
            _ => default_slots(
                physical_memory(),
                std::thread::available_parallelism().map_or(1, std::num::NonZeroUsize::get),
                dataflow,
            ),
        };
        let dir = match std::env::var_os("GOH_SHELLCHECK_SLOT_DIR") {
            Some(d) if !d.is_empty() => PathBuf::from(d),
            _ => std::env::var_os("HOME")
                .map(PathBuf::from)
                .unwrap_or_default()
                .join(".cache/goh/slots/shellcheck"),
        };
        if n > 0 {
            std::fs::create_dir_all(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
        }
        Ok(Self { dir, n })
    }

    /// How many shellchecks this pool lets run at once; `None` when the bound is off.
    #[must_use]
    pub fn bound(&self) -> Option<usize> {
        (self.n > 0).then_some(self.n)
    }

    fn open(&self, i: usize) -> Result<File, String> {
        let path = slot_file(&self.dir, i);
        OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&path)
            .map_err(|e| format!("{}: {e}", path.display()))
    }

    /// A token: the first free one, else a wait on the one `hint` names.
    ///
    /// # Errors
    /// A token file cannot be opened or locked.
    pub fn take(&self, hint: usize) -> Result<Token, String> {
        if self.n == 0 {
            return Ok(Token { _file: None });
        }
        for k in 0..self.n {
            let file = self.open((hint + k) % self.n)?;
            let got = file.try_lock(); // lock-order: shellcheck
            match got {
                Ok(()) => return Ok(Token { _file: Some(file) }),
                Err(TryLockError::WouldBlock) => {}
                Err(TryLockError::Error(e)) => return Err(format!("shellcheck token: {e}")),
            }
        }
        let file = self.open(hint % self.n)?;
        let held = file.lock(); // lock-order: shellcheck
        held.map_err(|e| format!("shellcheck token: {e}"))?;
        Ok(Token { _file: Some(file) })
    }
}

fn slot_file(dir: &Path, i: usize) -> PathBuf {
    dir.join(format!("token-{i}"))
}

#[cfg(test)]
mod tests {
    use super::default_slots;

    #[test]
    fn one_token_per_eight_gib_with_dataflow_between_one_and_the_cores() {
        assert_eq!(default_slots(Some(64 << 30), 16, true), 8);
        assert_eq!(default_slots(Some(16 << 30), 16, true), 2);
        assert_eq!(default_slots(Some(4 << 30), 16, true), 1);
        assert_eq!(default_slots(Some(512 << 30), 16, true), 16);
        assert_eq!(default_slots(None, 16, true), 8);
        assert_eq!(default_slots(None, 0, true), 1);
    }

    #[test]
    fn one_token_per_gib_without_dataflow_so_the_hosted_runner_runs_one_per_core() {
        // The macos-26 runner: 7 GiB, 3 cores. At 8 GiB a token it got one (GOH.CIRED01392).
        assert_eq!(default_slots(Some(7 << 30), 3, false), 3);
        assert_eq!(default_slots(Some(7 << 30), 3, true), 1);
        assert_eq!(default_slots(Some(4 << 30), 16, false), 4);
        assert_eq!(default_slots(Some(64 << 30), 16, false), 16);
        assert_eq!(default_slots(Some(512 << 20), 16, false), 1);
    }
}
