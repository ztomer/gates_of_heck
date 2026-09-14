//! Git plumbing — Rust port of `checks/_gitutil.py`.
//!
//! Two contracts:
//! 1. File listing: tracked files (full runs) vs staged index entries.
//! 2. Content truth: a staged check polices the index blob (`git show :path`),
//!    never the worktree. Full runs police the worktree.
//!
//! NUL-delimited git output is load-bearing: without `-z`, git quotes paths
//! holding non-ASCII or control characters and those files go unpoliced.

use std::path::Path;
use std::process::Command;

/// The repo top level, or `None` outside a git checkout.
#[must_use]
pub fn repo_root() -> Option<String> {
    let out = Command::new("git")
        .args(["rev-parse", "--show-toplevel"])
        .output()
        .ok()?;
    if out.status.success() {
        Some(String::from_utf8_lossy(&out.stdout).trim().to_owned())
    } else {
        None
    }
}

/// Repo-root-relative file list.
///
/// Full mode lists everything tracked (`git ls-files`); staged mode lists
/// added/copied/modified index entries (`--diff-filter=ACM`, mirroring
/// `listed_files` — other checkers may pass `R` for renames).
///
/// # Errors
///
/// Returns a message when git answers and fails (a corrupt index reads like
/// a spotless repo if this were silent — it must be loud instead).
pub fn listed_files(root: &Path, staged: bool) -> Result<Vec<String>, String> {
    let mut cmd = Command::new("git");
    cmd.arg("-C").arg(root);
    if staged {
        cmd.args(["diff", "--cached", "--name-only", "-z", "--diff-filter=ACM"]);
    } else {
        cmd.args(["ls-files", "-z"]);
    }
    let out = cmd.output().map_err(|e| format!("git list failed: {e}"))?;
    if !out.status.success() {
        let detail = String::from_utf8_lossy(&out.stderr);
        return Err(format!(
            "git {} failed (exit {}): {}",
            if staged { "diff" } else { "ls-files" },
            out.status.code().unwrap_or(-1),
            detail.trim().chars().take(200).collect::<String>()
        ));
    }
    Ok(out
        .stdout
        .split(|b| *b == 0)
        .filter(|chunk| !chunk.is_empty())
        .map(|chunk| String::from_utf8_lossy(chunk).into_owned())
        .collect())
}

/// The bytes a check should police: the index blob when staged (falling back
/// to the worktree on an index race), the worktree otherwise. `None` means
/// nothing to police.
#[must_use]
pub fn content_bytes(root: &Path, rel: &str, staged: bool) -> Option<Vec<u8>> {
    if staged {
        let spec = format!(":{rel}");
        if let Ok(out) = Command::new("git")
            .arg("-C")
            .arg(root)
            .args(["show", &spec])
            .output()
        {
            if out.status.success() {
                return Some(out.stdout);
            }
        }
    }
    std::fs::read(root.join(rel)).ok()
}

/// Lines in a blob, by the one definition the gates share: a trailing newline
/// terminates the last line, it does not begin another.
#[must_use]
pub fn line_count(blob: &[u8]) -> usize {
    bytecount::count(blob, b'\n') + usize::from(!blob.is_empty() && !blob.ends_with(b"\n"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn line_count_terminator_is_not_a_line() {
        assert_eq!(line_count(b""), 0);
        assert_eq!(line_count(b"a\nb\n"), 2);
        assert_eq!(line_count(b"a\nb"), 2);
        assert_eq!(line_count(b"a"), 1);
    }
}
