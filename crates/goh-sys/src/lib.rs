//! The one place `gates_of_heck` calls into libc: five things safe `std` cannot do, each a single
//! syscall, for `goh step` (the native step wrapper, roadmap 4C.1).
//!
//! * [`ignored_at_entry`] -- `nohup`'s contract: a signal the caller IGNORED must stay ignored, so
//!   the wrapper must not install a handler over it, and only `sigaction` reads the disposition;
//! * [`killpg`] -- signal a whole process group (the step's tree, reparented children included);
//! * [`group_alive`] -- `killpg(pgid, 0)`: whether anything is left in the group;
//! * [`euid`] -- `geteuid`: whose repos git trusts, for the in-process repo-root lookup;
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

/// The effective user id of this process (`geteuid`): who git would call the owner of a repo it
/// trusts, which `goh`'s in-process repo-root lookup must match before it answers for git.
#[must_use]
pub fn euid() -> u32 {
    // SAFETY: a plain syscall with no arguments; it cannot fail and touches no memory of ours.
    unsafe { libc::geteuid() }
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

/// An instrumented build's profile goes to `path` when nobody named one (GOH.STRAYPROFRAW).
///
/// The LLVM runtime's own default is the RELATIVE `default_%m_%p.profraw`, so a coverage build run
/// with no `LLVM_PROFILE_FILE` (an agent probing the ship's binary from the loop's checkout,
/// 2026-11-20) wrote into whatever directory it was started in. `__llvm_profile_set_filename`
/// outranks the environment, so the caller asks only when the variable is unset: a run the coverage
/// gate points somewhere still profiles there. Only in the coverage build (`cfg(coverage)`, set by
/// cargo-llvm-cov), the one that links the runtime defining it.
#[cfg(coverage)]
pub fn name_profile(path: &'static std::ffi::CStr) {
    extern "C" {
        fn __llvm_profile_set_filename(name: *const libc::c_char);
    }
    // SAFETY: the runtime reads a NUL-terminated pattern and keeps a copy; `path` is 'static anyway.
    unsafe { __llvm_profile_set_filename(path.as_ptr()) }
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

/// Cumulative CPU seconds (own + reaped descendants) and runnable thread count for process group
/// `pgid`, or `None` when the OS or query is unavailable.
#[cfg(target_os = "macos")]
#[must_use]
pub fn pgid_cpu_and_runnable(pgid: i32) -> Option<(f64, u32)> {
    #[repr(C)]
    #[derive(Clone, Copy)]
    struct Timebase {
        numer: u32,
        denom: u32,
    }
    extern "C" {
        fn mach_timebase_info(info: *mut Timebase) -> libc::c_int;
    }
    const PROC_PGRP_ONLY: u32 = 2;
    let Ok(pgid_u32) = u32::try_from(pgid) else {
        return None;
    };
    let mut tb = Timebase { numer: 0, denom: 0 };
    // SAFETY: mach_timebase_info writes two u32 fields into `tb`, which this frame owns.
    if unsafe { mach_timebase_info(&raw mut tb) } != 0 || tb.denom == 0 {
        return None;
    }

    let mut pids = vec![0_i32; 512];
    let count = loop {
        let buf_bytes = i32::try_from(pids.len() * std::mem::size_of::<i32>()).unwrap_or(i32::MAX);
        // SAFETY: `pids` is a writable buffer of `buf_bytes` bytes; kernel writes <= `buf_bytes`.
        let nbytes = unsafe {
            libc::proc_listpids(
                PROC_PGRP_ONLY,
                pgid_u32,
                pids.as_mut_ptr().cast(),
                buf_bytes,
            )
        };
        if nbytes < 0 {
            return None;
        }
        let n = usize::try_from(nbytes).unwrap_or(0) / std::mem::size_of::<i32>();
        if n < pids.len() || pids.len() >= 8192 {
            break n;
        }
        pids.resize(pids.len() * 2, 0);
    };

    let mut ticks: u64 = 0;
    let mut runnable: u32 = 0;
    let ti_size = i32::try_from(std::mem::size_of::<libc::proc_taskinfo>()).unwrap_or(0);
    for &pid in &pids[..count.min(pids.len())] {
        if pid <= 0 {
            continue;
        }
        let mut ru = std::mem::MaybeUninit::<libc::rusage_info_v2>::zeroed();
        // SAFETY: proc_pid_rusage writes one `rusage_info_v2` struct into `ru` for RUSAGE_INFO_V2.
        let rc =
            unsafe { libc::proc_pid_rusage(pid, libc::RUSAGE_INFO_V2, ru.as_mut_ptr().cast()) };
        if rc == 0 {
            // SAFETY: proc_pid_rusage returned 0, so `ru` is initialized.
            let ru = unsafe { ru.assume_init() };
            ticks = ticks
                .saturating_add(ru.ri_user_time)
                .saturating_add(ru.ri_system_time)
                .saturating_add(ru.ri_child_user_time)
                .saturating_add(ru.ri_child_system_time);
        }
        let mut ti = std::mem::MaybeUninit::<libc::proc_taskinfo>::zeroed();
        // SAFETY: proc_pidinfo with PROC_PIDTASKINFO writes one `proc_taskinfo` of `ti_size` bytes.
        let got = unsafe {
            libc::proc_pidinfo(
                pid,
                libc::PROC_PIDTASKINFO,
                0,
                ti.as_mut_ptr().cast(),
                ti_size,
            )
        };
        if got == ti_size {
            // SAFETY: `got == ti_size` guarantees the kernel filled all of `proc_taskinfo`.
            let ti = unsafe { ti.assume_init() };
            if let Ok(n) = u32::try_from(ti.pti_numrunning) {
                runnable = runnable.saturating_add(n);
            }
        }
    }
    let ns = u128::from(ticks) * u128::from(tb.numer) / u128::from(tb.denom);
    let secs = u32::try_from(ns / 1_000_000_000).unwrap_or(u32::MAX);
    let sub_ns = u32::try_from(ns % 1_000_000_000).unwrap_or(0);
    Some((
        f64::from(secs) + f64::from(sub_ns) / 1_000_000_000.0,
        runnable,
    ))
}

/// Parse `/proc/<pid>/stat` into `(pgrp, state, total_jiffies)` across own and reaped children.
#[cfg(any(target_os = "linux", test))]
fn parse_proc_stat(stat: &str) -> Option<(i32, char, u64)> {
    let rparen = stat.rfind(')')?;
    let rest: Vec<&str> = stat.get(rparen + 1..)?.split_whitespace().collect();
    if rest.len() <= 14 {
        return None;
    }
    let state = rest[0].chars().next()?;
    let pgrp: i32 = rest[2].parse().ok()?;
    let own_user: u64 = rest[11].parse().ok()?;
    let own_sys: u64 = rest[12].parse().ok()?;
    let child_user: u64 = rest[13].parse().ok()?;
    let child_sys: u64 = rest[14].parse().ok()?;
    Some((
        pgrp,
        state,
        own_user
            .saturating_add(own_sys)
            .saturating_add(child_user)
            .saturating_add(child_sys),
    ))
}

/// Cumulative CPU seconds (own + reaped descendants) and runnable thread count for process group
/// `pgid`, or `None` when the OS or query is unavailable.
#[cfg(target_os = "linux")]
#[must_use]
pub fn pgid_cpu_and_runnable(pgid: i32) -> Option<(f64, u32)> {
    // SAFETY: sysconf(_SC_CLK_TCK) queries a scalar kernel constant and touches no memory.
    let hz = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
    let Ok(hz_u64) = u64::try_from(hz) else {
        return None;
    };
    if hz_u64 == 0 {
        return None;
    }
    let Ok(entries) = std::fs::read_dir("/proc") else {
        return None;
    };
    let mut jiffies: u64 = 0;
    let mut runnable: u32 = 0;
    for entry in entries.flatten() {
        let name = entry.file_name();
        let Some(name_str) = name.to_str() else {
            continue;
        };
        if !name_str.bytes().all(|b| b.is_ascii_digit()) {
            continue;
        }
        let Ok(stat) = std::fs::read_to_string(entry.path().join("stat")) else {
            continue;
        };
        let Some((proc_pgid, state, proc_jiffies)) = parse_proc_stat(&stat) else {
            continue;
        };
        if proc_pgid != pgid {
            continue;
        }
        jiffies = jiffies.saturating_add(proc_jiffies);
        if state == 'R' {
            runnable = runnable.saturating_add(1);
        }
    }
    let secs = u32::try_from(jiffies / hz_u64).unwrap_or(u32::MAX);
    let rem = u32::try_from(jiffies % hz_u64).unwrap_or(0);
    let hz_f = u32::try_from(hz_u64).unwrap_or(100);
    Some((f64::from(secs) + f64::from(rem) / f64::from(hz_f), runnable))
}

/// Fallback for kernels without libproc or `/proc`.
#[cfg(not(any(target_os = "macos", target_os = "linux")))]
#[must_use]
pub const fn pgid_cpu_and_runnable(_pgid: i32) -> Option<(f64, u32)> {
    None
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

    #[test]
    fn parse_proc_stat_handles_parens_in_comm() {
        let sample = "42 (my (odd) proc) R 1 42 42 0 -1 0 0 0 0 0 12 3 25 5 20 0 1 0 99";
        assert_eq!(super::parse_proc_stat(sample), Some((42, 'R', 45)));
        assert_eq!(super::parse_proc_stat("bad"), None);
    }

    #[test]
    fn pgid_cpu_tracks_reaped_descendants_and_idle_sleep() {
        use std::os::unix::process::CommandExt as _;
        let mut child = std::process::Command::new("/bin/sh")
            .args([
                "-c",
                "(i=0; while [ $i -lt 80000 ]; do i=$((i+1)); done); sleep 5",
            ])
            .process_group(0)
            .spawn()
            .unwrap_or_else(|e| panic!("spawn: {e}"));
        let Ok(pgid) = i32::try_from(child.id()) else {
            let _ = child.kill();
            let _ = child.wait();
            panic!("pid out of range");
        };
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        let mut sampled = None;
        while std::time::Instant::now() < deadline {
            sampled = super::pgid_cpu_and_runnable(pgid);
            if matches!(sampled, Some((cpu_s, 0)) if cpu_s > 0.01) {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        let _ = super::killpg(pgid, libc::SIGKILL);
        let _ = child.wait();
        let (cpu_s, runnable) = sampled.unwrap_or_else(|| panic!("expected sample"));
        assert!(cpu_s > 0.01, "expected reaped CPU > 0.01s, got {cpu_s}");
        assert_eq!(runnable, 0, "sleeping process should have 0 runnable");
    }
}
