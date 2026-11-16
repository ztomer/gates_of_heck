//! Where `goh seed-add` writes an entry: where the repository's other seeds live (GOH.GRACEFWD).
//!
//! `seed-add` wrote every entry to the private store. A repository whose seeds are TRACKED (its
//! own `<kind>_seed.json` at its root, versioned and in every clone) got its new entry in the one
//! place its hosted CI cannot read: monitor's `floor_seed.json` entry, 2026-11-15, passed on this
//! machine and would have gone red on the runner (the trap GOH.STORECI refuses). So an entry
//! is routed by its root commit to the repository on disk that has it -- the repository the
//! command runs in first, then the estate registry (`crate::shapes::diff::estate`) -- and goes to
//! that repository's tracked file when it keeps its seeds tracked:
//!
//! * it tracks a `<kind>_seed.json` at its root (any kind: one fact, one home per repository); or
//! * its CI runs goh (`store_ci::goh_workflow`), whose runner can read nothing else.
//!
//! Otherwise -- a repository gated only on this machine, or one no checkout here has -- the entry
//! goes to the store, as before. An entry moved into a tracked file is deleted from the store in
//! the same run, since an entry in both homes is refused at load.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

/// Whether the repository at `repo` keeps its seeds in its own tracked files.
#[must_use]
pub fn keeps_tracked(repo: &Path) -> bool {
    if crate::estate_seed::goh_workflow(repo).is_some() {
        return true;
    }
    crate::gitutil::listed_files(repo, false).is_ok_and(|files| {
        files
            .iter()
            .any(|f| !f.contains('/') && super::seed_name(f))
    })
}

/// `root -> repository` for each of `roots` whose repository, among `repos` (first wins), keeps
/// its seeds tracked. A root no repository has, or whose repository keeps none, is absent: the
/// store's.
#[must_use]
pub fn tracked_homes(roots: &BTreeSet<String>, repos: &[PathBuf]) -> BTreeMap<String, PathBuf> {
    let mut seen = BTreeSet::new();
    let mut out = BTreeMap::new();
    for repo in repos {
        if seen.len() == roots.len() {
            break;
        }
        let Some(root) = crate::gitutil::root_commit(repo).filter(|r| roots.contains(r)) else {
            continue;
        };
        if seen.insert(root.clone()) && keeps_tracked(repo) {
            out.insert(root, repo.clone());
        }
    }
    out
}

/// The repositories an entry may belong to: the one the command runs in, then the estate's.
#[must_use]
pub fn candidates() -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = crate::gitutil::repo_root()
        .map(PathBuf::from)
        .into_iter()
        .collect();
    out.extend(crate::shapes::diff::estate().unwrap_or_default());
    out
}
