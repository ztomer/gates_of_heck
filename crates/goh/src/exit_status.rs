//! A child's exit as the process code a caller reads (GOH.WRAPSIGNAL).
//!
//! `ExitStatus::code()` is `None` for a signal death. A wrapper that maps that to a fixed small
//! code hands its caller a verdict the child never gave (`goh land --wait` killed read as its own
//! "no request", 2). The shell's convention is 128 plus the signal, which no house script returns
//! as a verdict of its own.

use std::os::unix::process::ExitStatusExt;
use std::process::ExitStatus;

/// The code `status` exits with: its own, or 128 plus the signal that killed it.
#[must_use]
pub fn code_of(status: ExitStatus) -> i32 {
    status
        .code()
        .unwrap_or_else(|| 128 + status.signal().unwrap_or(0))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_code_is_kept_and_a_signal_is_128_plus_it() {
        assert_eq!(code_of(ExitStatus::from_raw(7 << 8)), 7);
        assert_eq!(code_of(ExitStatus::from_raw(15)), 143);
        assert_eq!(code_of(ExitStatus::from_raw(9)), 137);
    }
}
