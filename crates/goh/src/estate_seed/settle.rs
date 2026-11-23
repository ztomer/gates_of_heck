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
    write_store_file(dir, kind.file, repo, &settled.store)
}

/// `write_settled` by the seed file's name: what `goh seed-settle` applies from a journal.
///
/// # Errors
///
/// The store file cannot be read, parsed or written.
pub fn write_store_file(
    dir: &Path,
    name: &str,
    repo: &str,
    store: &BTreeMap<String, usize>,
) -> Result<(), String> {
    if store.is_empty() {
        return Ok(());
    }
    let file = dir.join(name);
    // Read-modify-rename under ONE lock on the store directory (GOH.SEEDSCOPE): two commits
    // settling at once each read the old file, and the second rename wrote back the entry the
    // first deleted. The directory, never the file, which the rename replaces; and no lock file,
    // which a crashed holder would leave behind.
    let lock = std::fs::File::open(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    lock.lock().map_err(|e| format!("{}: {e}", dir.display()))?; // lock-order: estate_settle
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
        let Some(n) = field("path").and_then(|p| store.get(&p).copied()) else {
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
    let tmp = dir.join(format!(".{name}.{}.tmp", std::process::id()));
    std::fs::write(&tmp, &body).map_err(|e| format!("{}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, &file).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("{}: {e}", file.display())
    })
}

/// Delete `retired`'s entries (`path -> 0`) of the repository whose root is `repo` from `kind`'s
/// file in the private store, under its lock, and say each with `say(path, file)`.
///
/// The one path by which a run that is not a commit's retires a store entry (GOH.SEEDPRUNE, a
/// declared floor; GOH.STORECISTALE, a fixed site of a repository whose CI runs goh). A failed
/// write is a warning tagged `tag`: the next run retires the entry again, and nothing reads it.
pub fn retire_store(
    repo: &str,
    kind: Kind,
    retired: &BTreeMap<String, usize>,
    tag: &str,
    say: impl Fn(&str, &Path) -> String,
) -> String {
    use std::fmt::Write as _;
    let mut out = String::new();
    let Some(dir) = store_dir().filter(|_| !retired.is_empty()) else {
        return out;
    };
    let file = dir.join(kind.file);
    if let Err(m) = write_store_file(&dir, kind.file, repo, retired) {
        let _ = writeln!(
            out,
            "{} {tag} could not retire {} from {}: {m}; the next run retires {}",
            goh_tui::WARN,
            goh_tui::counted_as(retired.len(), "entry", "entries"),
            file.display(),
            if retired.len() == 1 { "it" } else { "them" }
        );
        return out;
    }
    for path in retired.keys() {
        let _ = writeln!(out, "{}", say(path, &file));
    }
    out
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
    let mut now = settled_seed(seed, repo, &settled);
    // A lane's licensed settle is read at what is found, and applied on the store's branch
    // (GOH.SEEDBRANCH).
    let licensed = match store_dir() {
        Some(dir) if full && mine => {
            super::deferred::consume(root, repo, counts, &mut now, (&dir, kind))
        }
        _ => String::new(),
    };
    // GOH.STORECI judges the store entries left once the findings are known (GOH.STORECISTALE).
    let retired = super::store_ci::judge(root, repo, full, counts, &mut now, kind)?;
    let mut v = judge(repo, counts, &now, full, kind);
    v.retired = licensed + &retired;
    if !full {
        v.stale.extend(settled.stale.iter().cloned());
    }
    if let Some(note) = unjudged(root, seed) {
        v.warn.push((kind.file.to_string(), note));
    }
    Ok((v, settled))
}

/// Why the tracked entries' roots were not judged, or `None` (GOH.SHALLOWROOT).
///
/// A `--depth 1` clone (most consumer CI: only monitor fetches its whole history) answers the
/// shallow BOUNDARY as its root, so `load` takes every tracked entry as the repository's own and
/// the refusal of another repository's entry never runs there. That is a warning naming the fix,
/// never a silent pass. One git spawn, only when the seed holds a tracked entry.
fn unjudged(root: &Path, seed: &Seed) -> Option<String> {
    let tracked = seed.values().filter(|h| !h.stored).count();
    (tracked > 0 && crate::gitutil::is_shallow(root)).then(|| {
        format!(
            "NOT JUDGED in a shallow clone: {} taken as this repository's own, its root commit \
             is not in this clone; fetch the whole history to judge whose they are \
             (actions/checkout `fetch-depth: 0`, or `git fetch --unshallow`)",
            goh_tui::counted_as(tracked, "tracked entry", "tracked entries")
        )
    })
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
pub fn settle_store(
    root: &Path,
    repo: &str,
    kind: Kind,
    settled: &Settle,
    (tag, item): (&str, &str),
) -> String {
    use std::fmt::Write as _;
    let mut out = String::new();
    if settled.store.is_empty() {
        return out;
    }
    let Some(dir) = store_dir() else {
        return out;
    };
    // Inside a commit's hook the settle waits for EVERY step of that commit (GOH.SEEDREFUSED): a
    // later step refusing the commit must leave the store as it was.
    if let Some(pending) = super::pending::dir() {
        return super::pending::journal(&pending, &dir, kind, repo, settled, tag, item);
    }
    // Off the branch the store is keyed to, the settle is licensed for when the change reaches it
    // (GOH.SEEDBRANCH): main still holds the sites, and its runs read this same store.
    if let Err(branch) = super::deferred::on_store_branch(root) {
        return super::deferred::license(&dir, kind.file, repo, &settled.store, (&branch, tag));
    }
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
    out.push_str(&settled_lines(&settled.store, kind.unit, tag, &file));
    out
}

/// What a settle did, one line per entry it lowered or deleted from `file`.
pub(super) fn settled_lines(
    store: &BTreeMap<String, usize>,
    unit: &str,
    tag: &str,
    file: &Path,
) -> String {
    use std::fmt::Write as _;
    let mut out = String::new();
    for (path, n) in store {
        let did = if *n == 0 {
            format!("no {unit} left, its entry deleted")
        } else {
            format!("its entry lowered to {}", goh_tui::counted(*n, unit))
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
