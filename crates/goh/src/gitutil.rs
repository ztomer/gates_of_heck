//! Git plumbing — Rust port of the retired `checks/_gitutil.py`.
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
/// Full mode lists the WORKTREE — everything tracked plus every untracked
/// file git does not ignore (`ls-files --cached --others --exclude-standard`);
/// staged mode lists added/copied/modified index entries (`--diff-filter=ACM`,
/// mirroring `listed_files`). Full used to be tracked-only, so a brand-new
/// oversized file was invisible to `--full` until it was staged; "full"
/// answers "is my tree green", and an untracked file is the tree.
///
/// # Errors
///
/// Returns a message when git answers and fails (a corrupt index reads like
/// a spotless repo if this were silent — it must be loud instead).
pub fn listed_files(root: &Path, staged: bool) -> Result<Vec<String>, String> {
    if staged {
        nul_list(
            root,
            &["diff", "--cached", "--name-only", "-z", "--diff-filter=ACM"],
        )
    } else {
        nul_list(
            root,
            &[
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
        )
    }
}

/// The files this commit ADDS to the index (`--diff-filter=A`): a new path, not an edit.
///
/// # Errors
///
/// As `listed_files`: a failing git is loud, never an empty list.
pub fn added_files(root: &Path) -> Result<Vec<String>, String> {
    nul_list(
        root,
        &["diff", "--cached", "--name-only", "-z", "--diff-filter=A"],
    )
}

/// `git -C root <args>`, its NUL-separated stdout as paths; a git that answers and fails is an
/// error (a corrupt index must not read like a spotless repo).
fn nul_list(root: &Path, args: &[&str]) -> Result<Vec<String>, String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .output()
        .map_err(|e| format!("git list failed: {e}"))?;
    if !out.status.success() {
        let detail = String::from_utf8_lossy(&out.stderr);
        return Err(format!(
            "git {} failed (exit {}): {}",
            args.first().copied().unwrap_or("git"),
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

/// Directories a FALLBACK walk never descends (mirrors `checks/_gitutil.py` `PRUNE`).
const PRUNE: &[&str] = &[
    ".git",
    "target",
    ".build",
    "build",
    "node_modules",
    "__pycache__",
    ".venv",
];

/// Root-relative paths of every file the tree under `root` holds, without reading build output.
///
/// Mirrors `checks/_gitutil.py::tree_files`: git's worktree listing when `root` is in a work tree
/// (so an ignored `target/` is never opened -- one crate's walk descended 50,950 directories of it,
/// measured 2026-10-05), else a walk that PRUNES `PRUNE` rather than filtering afterwards.
#[must_use]
pub fn tree_files(root: &Path) -> Vec<String> {
    if let Ok(files) = listed_files(root, false) {
        return files;
    }
    let mut found = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.filter_map(Result::ok) {
            let path = entry.path();
            if path.is_dir() {
                if !path
                    .file_name()
                    .is_some_and(|n| PRUNE.iter().any(|p| n == *p))
                {
                    stack.push(path);
                }
            } else if let Ok(rel) = path.strip_prefix(root) {
                found.push(rel.to_string_lossy().replace('\\', "/"));
            }
        }
    }
    found.sort();
    found
}

/// Every `Cargo.toml` file in the tree (`tree_files`), as paths under `root`.
#[must_use]
pub fn cargo_manifests(root: &Path) -> Vec<std::path::PathBuf> {
    tree_files(root)
        .into_iter()
        .filter(|rel| rel.rsplit('/').next() == Some("Cargo.toml"))
        .map(|rel| root.join(rel))
        .filter(|p| p.is_file())
        .collect()
}

/// The bytes a check should police: the index blob when staged (falling back
/// to the worktree on an index race), the worktree otherwise. `None` means
/// nothing to police.
#[must_use]
pub fn content_bytes(root: &Path, rel: &str, staged: bool) -> Option<Vec<u8>> {
    if staged {
        // Read once per run for every scanner when the pipeline prefetched
        // it (crate::blobs); the same index blob `git show` would return.
        if let Some(blob) = crate::blobs::cached(root, rel) {
            return Some(blob);
        }
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

/// Binary-scan window: only the first this many bytes are inspected for a
/// NUL byte. Mirrors the reference checkers' `blob[:8000]` — one shared
/// definition so the window cannot drift per scanner.
pub const BINARY_SCAN_WINDOW: usize = 8000;

/// Report cap: violation blocks show the first this many hits, then an
/// "… and N more" line. Mirrors the references' `[:200]` — one shared
/// definition so every report truncates identically.
pub const MAX_REPORT_HITS: usize = 200;

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

    /// Fixture git, via the testkit's one scrubbed helper (never the hook's repo).
    fn git(root: &Path, args: &[&str]) {
        let res = goh_testkit::git_in(root, args);
        assert!(res.is_ok(), "{res:?}");
    }

    fn write(path: &Path, body: &str) {
        assert!(
            std::fs::write(path, body).is_ok(),
            "write {}",
            path.display()
        );
    }

    #[test]
    fn full_scope_lists_the_worktree_but_not_ignored_files() {
        let td = std::env::temp_dir().join(format!("goh-gitutil-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&td);
        assert!(std::fs::create_dir_all(td.join("build")).is_ok());
        git(&td, &["init", "-q"]);
        write(&td.join(".gitignore"), "build/\n");
        write(&td.join("tracked.md"), "t\n");
        git(&td, &["add", "-A"]);
        git(
            &td,
            &[
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-q",
                "-m",
                "x",
            ],
        );
        // A brand-new file nobody staged, and an ignored one.
        write(&td.join("fresh.md"), "f\n");
        write(&td.join("build/out.md"), "o\n");

        let mut full = listed_files(&td, false).unwrap_or_default();
        full.sort();
        assert_eq!(full, [".gitignore", "fresh.md", "tracked.md"]);
        // Staged scope is still the index: nothing is staged.
        assert_eq!(listed_files(&td, true).unwrap_or_default().len(), 0);
        let _ = std::fs::remove_dir_all(&td);
    }
}
