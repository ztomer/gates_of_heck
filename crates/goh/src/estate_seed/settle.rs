//! A commit's own run settles the seed entries its change settles (GOH.SEEDSTALE, D-0005).
//!
//! An entry lives in the repository's tracked seed file or in the private store. A commit that
//! removed the last finding a STORE entry held could not delete it -- the store is in no tree --
//! and the push then went red on it as stale. So the commit's run lowers or deletes it, and a
//! tracked entry left with nothing is red at the commit, which can delete it. The push's stale
//! check stays as the backstop.

use super::{judge, store_dir, Kind, Seed, Verdict};
use std::collections::BTreeMap;
use std::path::Path;

/// What a commit's own run does to the seed entries of the files it changes (GOH.SEEDSTALE).
///
/// The entry for a file lives in the repository's tracked seed file or in the private store. A
/// commit that removes the last finding a STORE entry holds could not delete it -- the store is in
/// no tree -- and the push then went red on it as stale (five pushes and releases on 2026-11-11/12,
/// each a coordinator's hand edit of the store). So the commit's run settles it: shrinking a
/// ratchet is always safe, only growth needs a reviewed entry. A TRACKED entry the commit leaves
/// with nothing to hold is the commit's to delete, in the same commit: red now, not at the push.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Settle {
    /// Store entries this commit lowers: `path -> its count now` (0: the entry is deleted).
    pub store: BTreeMap<String, usize>,
    /// Tracked entries this commit leaves with no finding: `(path, note)`, red at the commit.
    pub stale: Vec<(String, String)>,
}

/// Settle `seed`'s entries for `repo` over `read`, the files this commit's run read whole.
///
/// `read` is the commit's changed paths, deletions among them, which hold no finding. A store
/// entry above what is found is lowered, a tracked one with nothing left is stale. An entry for an
/// unread file is untouched.
#[must_use]
pub fn settle(
    repo: &str,
    found: &BTreeMap<String, usize>,
    seed: &Seed,
    read: &std::collections::BTreeSet<String>,
    kind: Kind,
) -> Settle {
    let mut out = Settle::default();
    for ((r, path), held) in seed.range((repo.to_string(), String::new())..) {
        if r != repo {
            break;
        }
        if !read.contains(path) {
            continue;
        }
        let n = found.get(path).copied().unwrap_or(0);
        if n >= held.count {
            continue;
        }
        if held.stored {
            out.store.insert(path.clone(), n);
        } else if n == 0 {
            let note = format!(
                "this commit leaves no {} -- delete its entry from {} in this commit",
                kind.unit, kind.file
            );
            out.stale.push((path.clone(), note));
        }
    }
    out
}

/// `seed` with the store entries `settled` lowers lowered (and those at 0 gone).
pub fn settled_seed(seed: &Seed, repo: &str, settled: &Settle) -> Seed {
    let mut out = seed.clone();
    for (path, n) in &settled.store {
        let key = (repo.to_string(), path.clone());
        if *n == 0 {
            out.remove(&key);
        } else if let Some(h) = out.get_mut(&key) {
            h.count = *n;
        }
    }
    out
}

/// Write `settled`'s store changes into `kind`'s file in the store `dir`.
///
/// Only the entries of the repository whose root is `repo` change: an entry at 0 is deleted, a
/// lowered one says so in its reason. The rest of the file is kept as it was; the write is a
/// rename, so a reader never sees half a file.
///
/// # Errors
///
/// The store file cannot be read, parsed or written.
pub fn write_settled(dir: &Path, kind: Kind, repo: &str, settled: &Settle) -> Result<(), String> {
    if settled.store.is_empty() {
        return Ok(());
    }
    let file = dir.join(kind.file);
    // Read-modify-rename under ONE lock on the store directory (GOH.SEEDSCOPE): two commits
    // settling at once each read the old file, and the second rename wrote back the entry the
    // first deleted. The directory, never the file, which the rename replaces; and no lock file,
    // which a crashed holder would leave behind.
    let lock = std::fs::File::open(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    lock.lock().map_err(|e| format!("{}: {e}", dir.display()))?;
    let text = std::fs::read_to_string(&file).map_err(|e| format!("{}: {e}", file.display()))?;
    hold_for_test();
    let mut v: serde_json::Value =
        serde_json::from_str(&text).map_err(|e| format!("{}: {e}", file.display()))?;
    let Some(entries) = v
        .get_mut("entries")
        .and_then(serde_json::Value::as_array_mut)
    else {
        return Err(format!("{}: no `entries` array", file.display()));
    };
    entries.retain_mut(|e| {
        let field = |k: &str| {
            e.get(k)
                .and_then(serde_json::Value::as_str)
                .map(str::to_string)
        };
        if field("root").as_deref() != Some(repo) {
            return true;
        }
        let Some(n) = field("path").and_then(|p| settled.store.get(&p).copied()) else {
            return true;
        };
        if n == 0 {
            return false;
        }
        let reason = field("reason").unwrap_or_default();
        e["count"] = serde_json::Value::from(n);
        e["reason"] = serde_json::Value::from(format!(
            "{reason}; lowered to {n} by a commit's own run (GOH.SEEDSTALE)"
        ));
        true
    });
    let mut body = serde_json::to_string_pretty(&v).map_err(|e| e.to_string())?;
    body.push('\n');
    let tmp = dir.join(format!(".{}.{}.tmp", kind.file, std::process::id()));
    std::fs::write(&tmp, &body).map_err(|e| format!("{}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, &file).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("{}: {e}", file.display())
    })
}

/// The test seam that widens the read-to-rename window (`GOH_SEED_SETTLE_HOLD_MS`), so the
/// two-process test in `tests/test_seed_scope.py` races on every run rather than by luck.
fn hold_for_test() {
    if let Some(ms) = std::env::var("GOH_SEED_SETTLE_HOLD_MS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
    {
        std::thread::sleep(std::time::Duration::from_millis(ms));
    }
}

/// `judge` over `scope`, a commit's run first settling the entries of what it read (`settle`).
///
/// Returns the verdict, and what a passing commit is to write (`settle_store`). One git spawn, at
/// a commit whose repository holds an entry and whose `read` is not given.
///
/// # Errors
///
/// The commit's changed paths cannot be listed.
pub fn judge_settled(
    root: &Path,
    repo: &str,
    scope: Scope,
    counts: &BTreeMap<String, usize>,
    seed: &Seed,
    kind: Kind,
) -> Result<(Verdict, Settle), String> {
    let Scope { commit, full, read } = scope;
    let mine = !repo.is_empty()
        && seed
            .range((repo.to_string(), String::new())..)
            .next()
            .is_some_and(|((r, _), _)| r == repo);
    let settled = if commit && mine {
        let read = match read {
            Some(read) => read,
            None => commit_paths(root)?,
        };
        settle(repo, counts, seed, &read, kind)
    } else {
        Settle::default()
    };
    let now = settled_seed(seed, repo, &settled);
    let mut v = judge(repo, counts, &now, full, kind);
    if !full {
        v.stale.extend(settled.stale.iter().cloned());
    }
    Ok((v, settled))
}

/// The paths this commit changes, deletions and both sides of a rename among them.
///
/// # Errors
///
/// A git that answers and fails.
fn commit_paths(root: &Path) -> Result<std::collections::BTreeSet<String>, String> {
    crate::gitutil::staged_paths(root).map(|v| v.into_iter().collect())
}

/// What one run of a seeded check read, which decides what its verdict may conclude.
#[derive(Debug, Default)]
pub struct Scope {
    /// A commit's own run: it settles the entries of what it read (GOH.SEEDSTALE).
    pub commit: bool,
    /// Every file in scope was read, so an entry with nothing left is stale.
    pub full: bool,
    /// The files the run read whole; `None` at a commit is the paths the commit changes.
    pub read: Option<std::collections::BTreeSet<String>>,
}

impl Scope {
    /// A run at `staged` scope: a commit's own run, else a full one.
    #[must_use]
    pub const fn of(staged: bool) -> Self {
        Self {
            commit: staged,
            full: !staged,
            read: None,
        }
    }
}

/// Write a passing commit's settled store entries, and say what it did.
///
/// An entry this commit left stale is deleted, a lowered one lowered. A failed write is a warning
/// -- the push's stale check is the backstop -- never a red commit.
#[must_use]
pub fn settle_store(repo: &str, kind: Kind, settled: &Settle, tag: &str, item: &str) -> String {
    use std::fmt::Write as _;
    let mut out = String::new();
    if settled.store.is_empty() {
        return out;
    }
    let Some(dir) = store_dir() else {
        return out;
    };
    let file = dir.join(kind.file);
    if let Err(m) = write_settled(&dir, kind, repo, settled) {
        let _ = writeln!(
            out,
            "{} {} could not settle the {} seed: {m}; the push will call its entry stale",
            goh_tui::WARN,
            tag,
            item
        );
        return out;
    }
    for (path, n) in &settled.store {
        let did = if *n == 0 {
            format!("no {} left, its entry deleted", kind.unit)
        } else {
            format!("its entry lowered to {}", goh_tui::counted(*n, kind.unit))
        };
        let _ = writeln!(
            out,
            "{} {} {path}: {did} from {} by this commit (GOH.SEEDSTALE)",
            goh_tui::START,
            tag,
            file.display()
        );
    }
    out
}
