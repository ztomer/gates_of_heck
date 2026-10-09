//! The one place `gates_of_heck` calls into libc: four things safe `std` cannot do, each a single
//! syscall, for `goh step` (the native step wrapper, roadmap 4C.1).
//!
//! * [`ignored_at_entry`] -- `nohup`'s contract: a signal the caller IGNORED must stay ignored, so
//!   the wrapper must not install a handler over it, and only `sigaction` reads the disposition;
//! * [`killpg`] -- signal a whole process group (the step's tree, reparented children included);
//! * [`group_alive`] -- `killpg(pgid, 0)`: whether anything is left in the group;
//! * [`children_cpu_ms`] -- `getrusage(RUSAGE_CHILDREN)`: the CPU every reaped descendant spent,
//!   so a step's own work is on record beside its wall time (BACKLOG 4.4: the box is never quiet).
//!
//! `unsafe` is denied everywhere else in the workspace; `tests/test_unsafe_scope.py` holds it here.

#![warn(clippy::undocumented_unsafe_blocks)]

use std::io;

/// True when `signal` is set to `SIG_IGN` -- by whoever started this process.
///
/// A failed query reads as "not ignored": the wrapper then handles the signal, which is the
/// behaviour of every other wrapper.
#[must_use]
pub fn ignored_at_entry(signal: i32) -> bool {
    let mut old = std::mem::MaybeUninit::<libc::sigaction>::zeroed();
    // SAFETY: a null new action only QUERIES the current disposition into `old`, which is a
    // zero-initialised `sigaction` this frame owns; nothing is changed.
    let rc = unsafe { libc::sigaction(signal, std::ptr::null(), old.as_mut_ptr()) };
    if rc != 0 {
        return false;
    }
    // SAFETY: sigaction returned 0, so it wrote a complete `sigaction` into `old`.
    let old = unsafe { old.assume_init() };
    old.sa_sigaction == libc::SIG_IGN
}

/// `killpg(pgid, signal)`.
///
/// # Errors
/// The kernel's: ESRCH when the group is empty, EPERM when a member is not ours to signal.
pub fn killpg(pgid: i32, signal: i32) -> io::Result<()> {
    // SAFETY: a plain syscall taking two integers; it touches no memory of ours.
    if unsafe { libc::killpg(pgid, signal) } == 0 {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    }
}

/// Whether any process is left in `pgid`: ESRCH means empty; EPERM means a member exists that is
/// not ours to signal, which is not empty.
#[must_use]
pub fn group_alive(pgid: i32) -> bool {
    match killpg(pgid, 0) {
        Ok(()) => true,
        Err(e) => e.raw_os_error() != Some(libc::ESRCH),
    }
}

/// User + system CPU, in milliseconds, of every child this process has reaped.
///
/// Each child's own reaped descendants are folded in by the kernel at each wait, so a delta
/// around one step is that step's tree. A failed query reads as 0.
#[must_use]
pub fn children_cpu_ms() -> f64 {
    let mut usage = std::mem::MaybeUninit::<libc::rusage>::zeroed();
    // SAFETY: getrusage writes one `rusage` into `usage`, a zero-initialised value this frame owns.
    let rc = unsafe { libc::getrusage(libc::RUSAGE_CHILDREN, usage.as_mut_ptr()) };
    if rc != 0 {
        return 0.0;
    }
    // SAFETY: getrusage returned 0, so it wrote a complete `rusage` into `usage`.
    let usage = unsafe { usage.assume_init() };
    let span = |t: libc::timeval| {
        std::time::Duration::from_secs(u64::try_from(t.tv_sec).unwrap_or(0))
            + std::time::Duration::from_micros(u64::try_from(t.tv_usec).unwrap_or(0))
    };
    (span(usage.ru_utime) + span(usage.ru_stime)).as_secs_f64() * 1000.0
}

#[cfg(test)]
mod tests {
    #[test]
    fn a_default_signal_is_not_ignored_and_an_empty_group_is_not_alive() {
        assert!(!super::ignored_at_entry(libc::SIGUSR2));
        assert!(!super::group_alive(i32::MAX - 7));
    }

    #[test]
    fn a_reaped_child_adds_its_cpu() {
        let before = super::children_cpu_ms();
        let status = std::process::Command::new("/bin/sh")
            .args(["-c", "i=0; while [ $i -lt 200000 ]; do i=$((i+1)); done"])
            .status()
            .unwrap_or_else(|e| panic!("spawn: {e}"));
        assert!(status.success());
        assert!(super::children_cpu_ms() > before);
    }
}
