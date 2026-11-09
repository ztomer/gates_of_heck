//! The estate's unbound index reads on the day the check widened: a WARN-level ratchet.
//!
//! The sweep that widened `goh bare-hook-index` to plain index reads (GOH.BAREINDEX) found
//! consumers whose hook-run scripts read an index unbound; they are fixed in their own repos, one
//! queue item each. Until then each is seeded here, in `gates_of_heck` (`hook_index_seed.json`,
//! read from `$GOH_DIR`), keyed by the repository's ROOT COMMIT and the file: a seeded file at
//! or under its count WARNS with its sites, over it is red, and an entry a full run finds clean or
//! lower is named to delete or lower (a warn: the consumer that fixed it cannot edit this file in
//! the same commit). An unseeded read is red. "Done" is an empty `entries`.
//!
//! The ratchet itself, and why it is keyed by the root commit, is `crate::estate_seed`, shared by
//! every seeded check.

use std::collections::BTreeMap;
use std::path::Path;

pub use crate::estate_seed::{Seed, Verdict};

/// The seed's file name, at the `gates_of_heck` root.
pub const SEED_FILE: &str = "hook_index_seed.json";

const KIND: crate::estate_seed::Kind = crate::estate_seed::Kind {
    file: SEED_FILE,
    unit: "unbound read",
    name: "hook-index seed",
};

/// Read the seed at `goh`. A missing file is an empty seed; a corrupt one is an error.
///
/// # Errors
///
/// A message naming the file when it is not the JSON `crate::estate_seed` reads.
pub fn load(goh: &Path) -> Result<Seed, String> {
    crate::estate_seed::load(goh, KIND)
}

/// Judge `found` (`path -> number of reads`) in the repo whose identity is `repo` against `seed`.
/// `full`: every hook-run file was read, so an entry with fewer reads than seeded is stale.
#[must_use]
pub fn judge(repo: &str, found: &BTreeMap<String, usize>, seed: &Seed, full: bool) -> Verdict {
    crate::estate_seed::judge(repo, found, seed, full, KIND)
}
