//! The SCOPE of the unreaped-spawn scan: WHICH files it reads, and from WHERE.
//!
//! The scan's subject is the repository's TEST SCOPE -- every test source, not the
//! files the commit brings -- because a guard is a CROSS-FILE contract: one
//! `tests/common/` defines it, and the other test files rely on it. A scope of "the
//! files this commit touches" cannot see that contract, and it failed in BOTH
//! directions:
//!
//! * a file that wraps its child in a SHARED guard was reported as an unreaped
//!   spawn -- the guard was not in scope, so the wrap looked like a bare `Child`.
//!   That is a red on correct code, in the exact files the guard was written for
//!   (`routines`, 2026-10-20: three of its test files gave up the shared guard
//!   and took a local one, which is the workaround, not the fix);
//! * a commit that BROKE the shared guard was green -- the files that rely on it
//!   were not in scope either, so the tree leaked and the gate passed it.
//!
//! So the scope is the TREE at both tiers, and the tiers differ in WHERE the tree
//! is read from, not in how much of it is read:
//!
//! * `--staged`: the INDEX -- the tree the commit produces (`ls-files --cached`,
//!   read through `git show :path`). An unstaged edit is not in it, so it is
//!   neither judged nor able to block a clean commit.
//! * full scope: the WORKTREE -- the tracked and untracked files as they are.
//!
//! The cost is a whole-tree read at commit time, where the step used to read the
//! diff: measured 2026-10-20, 363 test files in 0.07 s in this repo and 420 in
//! 0.16 s in `media_server`, the index blobs fetched in ONE `git cat-file
//! --batch` rather than one `git show` per file. Past the prefetch's memory
//! ceiling (`blobs::PREFETCH_BUDGET_BYTES`) the rest falls back to a `git show`
//! each -- slower, never a different answer.

use std::path::Path;

/// The files the scan reads: the repository's test scope, at both tiers.
///
/// # Errors
///
/// A git call that fails is a message, never an empty scope: a tree that could
/// not be listed is not a tree with nothing in it.
pub fn files(root: &Path, staged: bool) -> Result<Vec<String>, String> {
    if staged {
        crate::gitutil::index_files(root)
    } else {
        crate::gitutil::listed_files(root, false)
    }
}

/// The candidate sources among `paths` -- the extensions the scan can read.
#[must_use]
pub fn candidates(paths: &[String]) -> Vec<String> {
    paths
        .iter()
        .filter(|p| super::is_candidate(p))
        .cloned()
        .collect()
}

/// Fetch the INDEX blobs of every candidate in one `git cat-file --batch`, so the
/// staged scope reads the index of the whole tree without a process per file.
///
/// Nothing to do at full scope: those files are read from the worktree.
pub fn prefetch_index_blobs(root: &Path, paths: &[String], staged: bool) {
    if !staged {
        return;
    }
    let _ = crate::blobs::prefetch_staged(root, &candidates(paths));
}
