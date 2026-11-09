//! The estate's unbound index reads on the day the check widened: a WARN-level ratchet.
//!
//! The sweep that widened `goh bare-hook-index` to plain index reads (GOH.BAREINDEX) found
//! consumers whose hook-run scripts read an index unbound; they are fixed in their own repos, one
//! queue item each. Until then each is seeded here, in `gates_of_heck` (`hook_index_seed.json`,
//! read from `$GOH_DIR`), keyed by the repository's directory name and the file: a seeded file at
//! or under its count WARNS with its sites, over it is red, and an entry a full run finds clean or
//! lower is named to delete or lower (a warn: the consumer that fixed it cannot edit this file in
//! the same commit). An unseeded read is red. "Done" is an empty `entries`.

use std::collections::BTreeMap;
use std::path::Path;

/// The seed's file name, at the `gates_of_heck` root.
pub const SEED_FILE: &str = "hook_index_seed.json";

/// `(repo, path) -> count` as seeded.
pub type Seed = BTreeMap<(String, String), usize>;

/// Read the seed at `goh`. A missing file is an empty seed; a corrupt one is an error.
///
/// # Errors
///
/// A message naming the file when it is not the JSON this module writes.
pub fn load(goh: &Path) -> Result<Seed, String> {
    let path = goh.join(SEED_FILE);
    let Ok(text) = std::fs::read_to_string(&path) else {
        return Ok(Seed::new());
    };
    let bad = |why: &str| format!("{} is not a hook-index seed: {why}", path.display());
    let v: serde_json::Value = serde_json::from_str(&text).map_err(|e| bad(&e.to_string()))?;
    let mut seed = Seed::new();
    for e in v
        .get("entries")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| bad("no `entries` array"))?
    {
        let field = |k: &str| e.get(k).and_then(serde_json::Value::as_str);
        let (Some(repo), Some(file), Some(count)) = (
            field("repo"),
            field("path"),
            e.get("count").and_then(serde_json::Value::as_u64),
        ) else {
            return Err(bad("an entry lacks repo, path or count"));
        };
        if field("reason").is_none_or(str::is_empty) {
            return Err(bad(&format!("{repo} {file} has no reason")));
        }
        let count = usize::try_from(count).map_err(|e| bad(&e.to_string()))?;
        seed.insert((repo.to_string(), file.to_string()), count);
    }
    Ok(seed)
}

/// What the seed makes of one repo's findings (`path -> sites`).
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Verdict {
    /// Unseeded, or over the seed: red.
    pub red: Vec<String>,
    /// Seeded files at their count, and entries to lower or delete: `(path, note)`, warnings.
    pub warn: Vec<(String, String)>,
}

/// Judge `found` (`path -> number of reads`) in `repo` against `seed`. `full`: every hook-run
/// file was read, so an entry with fewer reads than seeded is stale.
#[must_use]
pub fn judge(repo: &str, found: &BTreeMap<String, usize>, seed: &Seed, full: bool) -> Verdict {
    let mut v = Verdict::default();
    for (path, n) in found {
        match seed.get(&(repo.to_string(), path.clone())) {
            None => v.red.push(path.clone()),
            Some(s) if n > s => v.red.push(path.clone()),
            Some(s) if n < s => {
                let note =
                    format!("{n} read(s), seeded {s} -- lower its entry in $GOH_DIR/{SEED_FILE}");
                v.warn.push((path.clone(), note));
            }
            Some(_) => v
                .warn
                .push((path.clone(), "seeded, not yet fixed".to_string())),
        }
    }
    if full {
        for ((r, path), _) in seed.range((repo.to_string(), String::new())..) {
            if r != repo {
                break;
            }
            if !found.contains_key(path) {
                let note =
                    format!("no unbound read left -- delete its entry from $GOH_DIR/{SEED_FILE}");
                v.warn.push((path.clone(), note));
            }
        }
    }
    v
}
