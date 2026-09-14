//! Strict scope parsing — mirrors `gates/structural.sh`.
//!
//! Only `--staged` is a checker-level scope; `--full` (and no argument) mean
//! unrestricted. Unknown arguments are a usage error naming the accepted
//! forms, never a scope guess.

/// Structural scope.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Scope {
    /// Staged files only (pre-commit).
    Staged,
    /// Every check (pre-push). Default.
    Full,
}

/// Resolve `(staged, full)` flags into a [`Scope`].
///
/// Both flags together is a usage error (exit 2 in the binary).
///
/// # Errors
///
/// Returns a usage message when both flags are set.
pub fn resolve(staged: bool, full: bool) -> Result<Scope, String> {
    if staged && full {
        return Err(String::from(
            "goh structural: --staged and --full are mutually exclusive (accepted: no flag | --full | --staged)",
        ));
    }
    if staged {
        Ok(Scope::Staged)
    } else {
        Ok(Scope::Full)
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]
mod tests {
    use super::*;

    #[test]
    fn no_flag_means_full() {
        assert_eq!(resolve(false, false).unwrap(), Scope::Full);
    }

    #[test]
    fn full_flag_means_full() {
        assert_eq!(resolve(false, true).unwrap(), Scope::Full);
    }

    #[test]
    fn staged_flag_means_staged() {
        assert_eq!(resolve(true, false).unwrap(), Scope::Staged);
    }

    #[test]
    fn both_flags_is_a_usage_error() {
        assert!(resolve(true, true).is_err());
    }
}
