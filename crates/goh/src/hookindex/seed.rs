//! The estate's unbound index reads on the day the check widened: a WARN-level ratchet.
//!
//! The sweep that widened `goh bare-hook-index` to plain index reads (GOH.BAREINDEX) found
//! consumers whose hook-run scripts read an index unbound; they are fixed in their own repos, one
//! queue item each. Until then each is seeded in `hook_index_seed.json` -- the repository's own
//! tracked file, or the owner's private store, never `gates_of_heck`'s tree (GOH.SEEDPRIVATE) --
//! keyed by the repository's ROOT COMMIT and the file: a seeded file at or under its count WARNS
//! with its sites, over it is red, an entry a full run finds clean is stale and red, and an
//! unseeded read is red. "Done" is no entry.
//!
//! The ratchet itself, its two homes, and why it is keyed by the root commit, is
//! `crate::estate_seed`, shared by every seeded check.

use std::collections::BTreeMap;
use std::path::Path;

pub use crate::estate_seed::{Held, Seed, Verdict};

/// The seed's file name, at a repository's root and in the private store.
pub const SEED_FILE: &str = "hook_index_seed.json";

pub const KIND: crate::estate_seed::Kind = crate::estate_seed::Kind {
    file: SEED_FILE,
    unit: "unbound read",
    name: "hook-index seed",
};

/// The seed for the repository at `repo`: its own tracked file and the private store.
///
/// # Errors
///
/// As `crate::estate_seed::load`: a corrupt file, a foreign entry, an entry in both homes.
pub fn load(repo: &Path) -> Result<Seed, String> {
    crate::estate_seed::load(repo, KIND)
}

/// Judge `found` (`path -> number of reads`) in the repo whose identity is `repo` against `seed`.
/// `full`: every hook-run file was read, so an entry with no read left is stale.
#[must_use]
pub fn judge(repo: &str, found: &BTreeMap<String, usize>, seed: &Seed, full: bool) -> Verdict {
    crate::estate_seed::judge(repo, found, seed, full, KIND)
}
