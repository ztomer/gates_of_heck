//! The estate seed: a WARN-level ratchet of the consumer sites a check found on the day it landed.
//!
//! A new house check sweeps the estate first (`lib/estate_registry.py` `existing()`), and the sites
//! it finds in consumer repositories are fixed there, one queue item each -- never from
//! `gates_of_heck`. Until then each is SEEDED, keyed by the repository's ROOT COMMIT and the file: a
//! seeded file at or under its count WARNS, over it is red, unseeded is red, and an entry a full
//! run finds clean is STALE and red (its repository deletes it with the fix). "Done" is no entry.
//!
//! WHERE AN ENTRY LIVES (GOH.SEEDPRIVATE, 2026-11-09). `gates_of_heck` is public, and its seeds
//! once held every consumer's entries: `crates_behind_seed.json` published a private consumer's nested Cargo workspace. Now a
//! repository's entries have two homes, read together, one key:
//!
//! * its OWN tracked seed file, `<repo>/<kind file>` -- versioned, reviewed, and in every clone, so
//!   a GitHub runner judges what the machine judges. Every entry there must carry THAT
//!   repository's root commit: an entry naming another root is refused, so no tree (goh's least of
//!   all) can carry another repository's facts. A shallow clone cannot know its root, nor a first
//!   commit in progress, so there the file's entries are taken as its own (the full clone that
//!   committed them, or the next commit, is judged);
//! * the owner's PRIVATE store, `$GOH_SEED_DIR/<kind file>`, else `$XDG_STATE_HOME/goh/seeds/`,
//!   else `~/.local/state/goh/seeds/` -- in no tree at all, for a repository gated only on this
//!   machine, or a clone that is not ours to commit to (a vendored or upstream tree).
//!
//! An entry in both homes is refused: one fact in one place.
//!
//! A STORE ENTRY FOR A REPOSITORY WHOSE CI RUNS GOH IS REFUSED (GOH.STORECI, 2026-11-09). A GitHub
//! runner cannot read the private store, so such an entry passes on this machine and goes red on
//! the repository's next push -- the trap GOH.SEEDPRIVATE and GOH.PLURALHEDGE both fell into in one
//! day. A repository runs goh in CI when a workflow under `.github/workflows` checks out, clones or
//! uses `<owner>/gates_of_heck` (a comment naming it does not count). Its entries live in its own
//! tracked file. Until `STORE_CI_REFUSED_FROM` (2026-11-16) the refusal is a WARNING naming the
//! repository and the store: the entries still standing on the day it landed (EST.HEDGESEEDS was
//! moving them into each repository's tracked file) keep their repositories green while they move.
//!
//! WHY THE ROOT COMMIT. The directory name was `goh bare-hook-index`'s first key, and the release
//! canary judges each consumer in a clone named `tree`: the seeded repo read as unseeded and the
//! land was refused. A root commit is the same in every clone, worktree and move of a repository
//! (`repo` in an entry is a label for the reader, never matched).
//!
//! One implementation for every seeded check (`hookindex`, `relquiet`, `mktempport`, `changelog`;
//! `crates_behind`'s seed is retired, D-0001): the first copy of this lived in `hookindex/seed.rs`, and a second copy is how
//! one of them would have kept the directory-name key.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// One seeded count, and which home holds it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Held {
    /// How many findings the entry allows.
    pub count: usize,
    /// In the private store (`store_dir`), not the repository's own tracked file.
    pub stored: bool,
}

impl Held {
    /// An entry from the repository's own tracked seed file.
    #[must_use]
    pub const fn tracked(count: usize) -> Self {
        Self {
            count,
            stored: false,
        }
    }
}

/// `(root commit, path) -> what is seeded`.
pub type Seed = BTreeMap<(String, String), Held>;

/// One seeded check: its seed file's name, and the noun its count counts.
#[derive(Debug, Clone, Copy)]
pub struct Kind {
    /// The file name, at a repository's root and in the private store.
    pub file: &'static str,
    /// What one counted finding is, singular (`read`, `site`, `finding`).
    pub unit: &'static str,
    /// What the file is called in a corrupt-file report (`hook-index seed`).
    pub name: &'static str,
}

/// The private store: `GOH_SEED_DIR`, else `$XDG_STATE_HOME/goh/seeds`, else
/// `$HOME/.local/state/goh/seeds` (the estate state's own directory, `lib/estate_registry.py`).
#[must_use]
pub fn store_dir() -> Option<PathBuf> {
    let set = |k: &str| {
        std::env::var_os(k)
            .filter(|v| !v.is_empty())
            .map(PathBuf::from)
    };
    if let Some(d) = set("GOH_SEED_DIR") {
        return Some(d);
    }
    let base = set("XDG_STATE_HOME").or_else(|| set("HOME").map(|h| h.join(".local/state")))?;
    Some(base.join("goh").join("seeds"))
}

/// Where `held` is to be edited, for a note.
fn home(kind: Kind, held: Held) -> String {
    match store_dir() {
        Some(d) if held.stored => d.join(kind.file).display().to_string(),
        _ => kind.file.to_string(),
    }
}

/// `kind`'s seed for the repository at `repo`: its own tracked file and the private store.
///
/// # Errors
///
/// A corrupt file; a tracked entry naming another repository's root; one entry in both homes; a
/// store entry for a repository whose CI runs goh (from `STORE_CI_REFUSED_FROM`; a warning before).
pub fn load(repo: &Path, kind: Kind, staged: bool) -> Result<Seed, String> {
    load_scoped(repo, store_dir().as_deref(), kind, staged)
}

/// `load` with the store named (`None`: no store), so a test never reads the machine's.
///
/// # Errors
///
/// As `load`.
pub fn load_from(repo: &Path, store: Option<&Path>, kind: Kind) -> Result<Seed, String> {
    load_scoped(repo, store, kind, false)
}

/// `load_from` at a scope: `staged` reads the tracked seed from the index (`tracked_text`).
///
/// # Errors
///
/// As `load`.
pub fn load_scoped(
    repo: &Path,
    store: Option<&Path>,
    kind: Kind,
    staged: bool,
) -> Result<Seed, String> {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs());
    load_in(repo, store, kind, now, staged)
}

/// `load_from` at the time `now` (seconds since the epoch), which decides whether a store entry for
/// a repository whose CI runs goh warns or is refused.
///
/// # Errors
///
/// As `load`.
pub fn load_at(repo: &Path, store: Option<&Path>, kind: Kind, now: u64) -> Result<Seed, String> {
    load_in(repo, store, kind, now, false)
}

fn load_in(
    repo: &Path,
    store: Option<&Path>,
    kind: Kind,
    now: u64,
    staged: bool,
) -> Result<Seed, String> {
    let mut seed = Seed::new();
    let on_disk = repo.join(kind.file);
    let tracked = parse_text(&on_disk, tracked_text(repo, kind, staged), kind)?;
    if !tracked.is_empty() {
        let own = crate::gitutil::root_commit(repo).unwrap_or_default();
        // Unverifiable, so the file's entries are taken as its own: a shallow clone (its root is the
        // shallow boundary), or a repository's first commit in progress (it has no root yet).
        let unverifiable = own.is_empty() || crate::gitutil::is_shallow(repo);
        for e in tracked {
            if !unverifiable && e.root != own {
                let to = store.map_or_else(
                    || "the private store".to_string(),
                    |d| d.join(kind.file).display().to_string(),
                );
                return Err(format!(
                    "{} holds an entry for another repository ({} {}, root {}; this one's is {}): \
                     an entry about a repository lives in THAT repository's own {}, or in {to}, \
                     never in this tree (GOH.SEEDPRIVATE)",
                    kind.file, e.repo, e.path, e.root, own, kind.file
                ));
            }
            let root = if unverifiable { own.clone() } else { e.root };
            seed.insert((root, e.path), Held::tracked(e.count));
        }
    }
    if let Some(dir) = store {
        let file = dir.join(kind.file);
        let stored = parse(&file, kind)?;
        if !stored.is_empty() {
            store_ci::refuse(repo, &file, &stored, now)?;
        }
        for e in stored {
            let key = (e.root, e.path);
            if let Some(held) = seed.get(&key) {
                // Two entries in the store itself are two clones of one repository (the same root):
                // naming the tracked file there sent the reader to a file that held neither.
                let both = if held.stored {
                    format!("twice in {}", file.display())
                } else {
                    format!("in both {} and {}", kind.file, file.display())
                };
                return Err(format!(
                    "{} {} is seeded {both}: one fact in one place -- delete one",
                    e.repo, key.1
                ));
            }
            seed.insert(
                key,
                Held {
                    count: e.count,
                    stored: true,
                },
            );
        }
    }
    Ok(seed)
}

/// What the seed makes of one repo's findings (`path -> count`).
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Verdict {
    /// Unseeded, or over the seed: red.
    pub red: Vec<String>,
    /// Seeded files at or under their count: `(path, note)`, warnings.
    pub warn: Vec<(String, String)>,
    /// Entries a full run found nothing for: `(path, note)`, red -- a ratchet holds only what is
    /// still there, and the repository that fixed the site deletes its own entry with the fix.
    pub stale: Vec<(String, String)>,
}

/// Judge `found` (`path -> number of findings`) in the repo whose identity is `repo` against
/// `seed`. `full`: every file in scope was read, so an entry with no finding left is stale.
#[must_use]
pub fn judge(
    repo: &str,
    found: &BTreeMap<String, usize>,
    seed: &Seed,
    full: bool,
    kind: Kind,
) -> Verdict {
    let mut v = Verdict::default();
    let unit = kind.unit;
    for (path, n) in found {
        match seed.get(&(repo.to_string(), path.clone())) {
            None => v.red.push(path.clone()),
            Some(s) if *n > s.count => v.red.push(path.clone()),
            Some(s) if *n < s.count => {
                let note = format!(
                    "{}, seeded {} -- lower its entry in {}",
                    goh_tui::counted(*n, unit),
                    s.count,
                    home(kind, *s)
                );
                v.warn.push((path.clone(), note));
            }
            Some(_) => v
                .warn
                .push((path.clone(), "seeded, not yet fixed".to_string())),
        }
    }
    if full {
        for ((r, path), s) in seed.range((repo.to_string(), String::new())..) {
            if r != repo {
                break;
            }
            if !found.contains_key(path) {
                let note = format!("no {unit} left -- delete its entry from {}", home(kind, *s));
                v.stale.push((path.clone(), note));
            }
        }
    }
    v
}

/// The repo identity `judge` keys by, read only when a seed could matter.
///
/// That is findings to judge, or a non-empty seed (a full run's stale check, a commit's settling
/// of the entries it changes). One git spawn at most.
#[must_use]
pub fn repo_identity(root: &Path, any_found: bool, seed: &Seed) -> String {
    if !any_found && seed.is_empty() {
        return String::new();
    }
    crate::gitutil::root_commit(root).unwrap_or_default()
}

/// A seeded file's sites listed in its warning; the rest are counted. A seeded changelog carries
/// up to 70 findings, and every commit in that repo would print them all.
const WARN_SITES: usize = 5;

/// A seeded check's findings: `path -> one listing line per site` (`    path:N: text  -- why`).
pub type Sites = BTreeMap<String, Vec<String>>;

/// What one seeded check says about one run, before the verdict.
pub struct Report<'a> {
    /// The check's tag, `[name]`.
    pub tag: &'a str,
    /// The queue item that seeded it, named on every warning (`GOH.RELHIDE`).
    pub item: &'a str,
    /// The red header's tail after the count, singular and plural: (`release step that discards
    /// its output`, `release steps that discard their output`).
    pub what: (&'a str, &'a str),
    /// The fix, indented, ending in a newline.
    pub fix: &'a str,
    /// The pass line, without the glyph and tag.
    pub ok: String,
}

/// The verdict over `sites` in the repo at `root`: `(exit code, stdout, stderr)`. Seeded files
/// warn with their sites; anything else found is red with the fix.
#[must_use]
pub fn verdict(
    root: &Path,
    staged: bool,
    kind: Kind,
    seed: &Seed,
    sites: &Sites,
    r: &Report<'_>,
) -> (i32, String, String) {
    verdict_in(root, Scope::of(staged), kind, seed, sites, r)
}

/// `verdict` with its scope spelled out (`Scope`).
#[must_use]
pub fn verdict_in(
    root: &Path,
    scope: Scope,
    kind: Kind,
    seed: &Seed,
    sites: &Sites,
    r: &Report<'_>,
) -> (i32, String, String) {
    use goh_tui::{ERR, OK, WARN};
    use std::fmt::Write as _;
    let counts: BTreeMap<String, usize> = sites.iter().map(|(p, s)| (p.clone(), s.len())).collect();
    let commit = scope.commit;
    let repo = repo_identity(root, !counts.is_empty(), seed);
    let (v, settled) = match judge_settled(root, &repo, scope, &counts, seed, kind) {
        Ok(j) => j,
        Err(m) => return (2, String::new(), format!("{ERR} {} {m}\n", r.tag)),
    };
    let mut warns = String::new();
    for (path, note) in &v.warn {
        let _ = writeln!(
            warns,
            "{WARN} {} seeded ({} ratchet): {path}: {note}",
            r.tag, r.item
        );
        let listed = sites.get(path).map_or(&[][..], Vec::as_slice);
        for line in listed.iter().take(WARN_SITES) {
            let _ = writeln!(warns, "{line}");
        }
        if listed.len() > WARN_SITES {
            let _ = writeln!(
                warns,
                "    ... and {} more, counted in the seed",
                listed.len() - WARN_SITES
            );
        }
    }
    if v.red.is_empty() && v.stale.is_empty() {
        warns.push_str(&settle_store(&repo, kind, &settled, r.tag, r.item));
        // A pass over seeded sites is not the clean pass: it says the seed is what passed.
        let ok = if v.warn.is_empty() {
            format!("{OK} {} OK — {}\n", r.tag, r.ok)
        } else {
            format!(
                "{OK} {} OK — nothing beyond the {} seed; {} warned\n",
                r.tag,
                r.item,
                goh_tui::counted(v.warn.len(), "seeded file")
            )
        };
        return (0, ok, warns);
    }
    let mut err = warns;
    if !v.red.is_empty() {
        let n: usize = v.red.iter().map(|p| sites[p].len()).sum();
        let scope = if commit { "staged" } else { "tracked" };
        let what = goh_tui::noun_as(n, r.what.0, r.what.1);
        let _ = writeln!(err, "{ERR} {} {n} {what} ({scope}):", r.tag);
        for p in &v.red {
            for line in &sites[p] {
                let _ = writeln!(err, "{line}");
            }
        }
        err.push_str(r.fix);
    }
    err.push_str(&stale_report(r.tag, r.item, &v.stale));
    (1, String::new(), err)
}

/// The red lines for `stale` entries (empty when there are none): a seed that outlives its sites is
/// headroom for the defect to come back.
#[must_use]
pub fn stale_report(tag: &str, item: &str, stale: &[(String, String)]) -> String {
    use std::fmt::Write as _;
    let mut out = String::new();
    if stale.is_empty() {
        return out;
    }
    let _ = writeln!(
        out,
        "{} {tag} {} stale {item} seed {}: a ratchet holds only what is still there:",
        goh_tui::ERR,
        stale.len(),
        goh_tui::noun_as(stale.len(), "entry", "entries")
    );
    for (path, note) in stale {
        let _ = writeln!(out, "    {path}: {note}");
    }
    out
}

/// A seeded check as a structural step: its warnings on a pass, its report on a red.
#[must_use]
pub fn step(label: &str, source: &str, verdict: (i32, String, String)) -> Option<i32> {
    let start = crate::step_report::begin(label);
    match verdict {
        (0, _, warns) => {
            goh_tui::eout(&warns);
            crate::step_report::ok(label, start);
            None
        }
        (code, _, err) => {
            let _ =
                crate::step_report::fail(label, &crate::step_report::ported(source), &err, start);
            Some(code)
        }
    }
}

/// A seeded check as a command: prints the verdict, returns its code.
#[must_use]
pub fn print(verdict: (i32, String, String)) -> i32 {
    let (code, out, err) = verdict;
    goh_tui::out(&out);
    goh_tui::eout(&err);
    code
}

/// Clip a source line for a listing.
#[must_use]
pub fn clip(s: &str) -> String {
    let s = s.trim();
    let mut out: String = s.chars().take(90).collect();
    if out.len() < s.len() {
        out.push_str("...");
    }
    out
}

mod parse;
mod pending;
mod settle;
mod store_ci;
use parse::{parse, parse_text, tracked_text};
pub use pending::{apply as apply_pending, PENDING_ENV};
#[cfg(test)]
use settle::settled_seed;
pub use settle::{
    judge_settled, settle, settle_store, write_settled, write_store_file, Scope, Settle,
};
pub use store_ci::{goh_workflow, STORE_CI_REFUSED_FROM};

#[cfg(test)]
mod tests;
