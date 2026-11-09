//! The estate seed: a WARN-level ratchet of the consumer sites a check found on the day it landed.
//!
//! A new house check sweeps the estate first (`lib/estate_registry.py` `existing()`), and the sites
//! it finds in consumer repositories are fixed there, one queue item each -- never from
//! `gates_of_heck`. Until then each is seeded in a JSON file at the `gates_of_heck` root (read from
//! `$GOH_DIR`), keyed by the repository's ROOT COMMIT and the file: a seeded file at or under its
//! count WARNS, over it is red, and an entry a full run finds clean or lower is named to delete or
//! lower (a warn: the consumer that fixed it cannot edit this file in the same commit). An unseeded
//! finding is red. "Done" is an empty `entries`.
//!
//! WHY THE ROOT COMMIT. The directory name was `goh bare-hook-index`'s first key, and the release
//! canary judges each consumer in a clone named `tree`: the seeded repo read as unseeded and the
//! land was refused. A root commit is the same in every clone, worktree and move of a repository
//! (`repo` in an entry is a label for the reader, never matched).
//!
//! One implementation for every seeded check (`hookindex`, `relquiet`, `mktempport`, `changelog`):
//! the first copy of this lived in `hookindex/seed.rs`, and a second copy is how one of them would
//! have kept the directory-name key.

use std::collections::BTreeMap;
use std::path::Path;

/// `(root commit, path) -> count` as seeded.
pub type Seed = BTreeMap<(String, String), usize>;

/// One seeded check: its seed file at the `gates_of_heck` root, and the noun its count counts.
#[derive(Debug, Clone, Copy)]
pub struct Kind {
    /// The file name, at the `gates_of_heck` root.
    pub file: &'static str,
    /// What one counted finding is, singular (`read`, `site`, `finding`).
    pub unit: &'static str,
    /// What the file is called in a corrupt-file report (`hook-index seed`).
    pub name: &'static str,
}

/// Read `kind`'s seed at `goh`. A missing file is an empty seed; a corrupt one is an error.
///
/// # Errors
///
/// A message naming the file when it is not the JSON this module reads.
pub fn load(goh: &Path, kind: Kind) -> Result<Seed, String> {
    let path = goh.join(kind.file);
    let Ok(text) = std::fs::read_to_string(&path) else {
        return Ok(Seed::new());
    };
    let bad = |why: &str| format!("{} is not a {}: {why}", path.display(), kind.name);
    let v: serde_json::Value = serde_json::from_str(&text).map_err(|e| bad(&e.to_string()))?;
    let mut seed = Seed::new();
    for e in v
        .get("entries")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| bad("no `entries` array"))?
    {
        let field = |k: &str| e.get(k).and_then(serde_json::Value::as_str);
        let (Some(root), Some(repo), Some(file), Some(count)) = (
            field("root"),
            field("repo"),
            field("path"),
            e.get("count").and_then(serde_json::Value::as_u64),
        ) else {
            return Err(bad("an entry lacks root, repo, path or count"));
        };
        if field("reason").is_none_or(str::is_empty) {
            return Err(bad(&format!("{repo} {file} has no reason")));
        }
        let count = usize::try_from(count).map_err(|e| bad(&e.to_string()))?;
        seed.insert((root.to_string(), file.to_string()), count);
    }
    Ok(seed)
}

/// What the seed makes of one repo's findings (`path -> count`).
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Verdict {
    /// Unseeded, or over the seed: red.
    pub red: Vec<String>,
    /// Seeded files at their count, and entries to lower or delete: `(path, note)`, warnings.
    pub warn: Vec<(String, String)>,
}

/// Judge `found` (`path -> number of findings`) in the repo whose identity is `repo` against
/// `seed`. `full`: every file in scope was read, so an entry with fewer findings than seeded is
/// stale.
#[must_use]
pub fn judge(
    repo: &str,
    found: &BTreeMap<String, usize>,
    seed: &Seed,
    full: bool,
    kind: Kind,
) -> Verdict {
    let mut v = Verdict::default();
    let (file, unit) = (kind.file, kind.unit);
    for (path, n) in found {
        match seed.get(&(repo.to_string(), path.clone())) {
            None => v.red.push(path.clone()),
            Some(s) if n > s => v.red.push(path.clone()),
            Some(s) if n < s => {
                let note =
                    format!("{n} {unit}(s), seeded {s} -- lower its entry in $GOH_DIR/{file}");
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
                let note = format!("no {unit} left -- delete its entry from $GOH_DIR/{file}");
                v.warn.push((path.clone(), note));
            }
        }
    }
    v
}

/// The repo identity `judge` keys by, read only when a seed could matter: findings to judge, or a
/// full run's stale check over a non-empty seed. One git spawn at most.
#[must_use]
pub fn repo_identity(root: &Path, any_found: bool, staged: bool, seed: &Seed) -> String {
    if !any_found && (staged || seed.is_empty()) {
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
    /// The red header's tail after the count: `release step(s) that discard their output`.
    pub what: &'a str,
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
    use goh_tui::{ERR, OK, WARN};
    use std::fmt::Write as _;
    let counts: BTreeMap<String, usize> = sites.iter().map(|(p, s)| (p.clone(), s.len())).collect();
    let repo = repo_identity(root, !counts.is_empty(), staged, seed);
    let v = judge(&repo, &counts, seed, !staged, kind);
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
    if v.red.is_empty() {
        // A pass over seeded sites is not the clean pass: it says the seed is what passed.
        let ok = if v.warn.is_empty() {
            format!("{OK} {} OK — {}\n", r.tag, r.ok)
        } else {
            format!(
                "{OK} {} OK — nothing beyond the {} seed; {} seeded file(s) warned\n",
                r.tag,
                r.item,
                v.warn.len()
            )
        };
        return (0, ok, warns);
    }
    let n: usize = v.red.iter().map(|p| sites[p].len()).sum();
    let scope = if staged { "staged" } else { "tracked" };
    let mut err = warns;
    let _ = writeln!(err, "{ERR} {} {n} {} ({scope}):", r.tag, r.what);
    for p in &v.red {
        for line in &sites[p] {
            let _ = writeln!(err, "{line}");
        }
    }
    err.push_str(r.fix);
    (1, String::new(), err)
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

#[cfg(test)]
mod tests {
    use super::*;

    const K: Kind = Kind {
        file: "x_seed.json",
        unit: "site",
        name: "x seed",
    };

    fn seed(entries: &[(&str, &str, usize)]) -> Seed {
        entries
            .iter()
            .map(|(r, p, n)| ((r.to_string(), p.to_string()), *n))
            .collect()
    }

    fn found(entries: &[(&str, usize)]) -> BTreeMap<String, usize> {
        entries.iter().map(|(p, n)| (p.to_string(), *n)).collect()
    }

    #[test]
    fn unseeded_and_over_are_red_at_and_under_warn() {
        let s = seed(&[("r", "a.sh", 1), ("r", "b.sh", 2), ("r", "c.sh", 2)]);
        let v = judge(
            "r",
            &found(&[("a.sh", 1), ("b.sh", 3), ("c.sh", 1), ("d.sh", 1)]),
            &s,
            false,
            K,
        );
        assert_eq!(v.red, vec!["b.sh".to_string(), "d.sh".to_string()]);
        assert_eq!(v.warn.len(), 2, "{:?}", v.warn);
        assert!(v.warn[1].1.contains("lower its entry"), "{:?}", v.warn);
    }

    #[test]
    fn a_full_run_names_a_stale_entry_and_another_repos_entry_is_not_this_ones() {
        let s = seed(&[("r", "gone.sh", 1), ("q", "a.sh", 1)]);
        let v = judge("r", &found(&[("a.sh", 1)]), &s, true, K);
        assert_eq!(v.red, vec!["a.sh".to_string()]);
        assert_eq!(v.warn.len(), 1);
        assert!(v.warn[0]
            .1
            .contains("delete its entry from $GOH_DIR/x_seed.json"));
        let staged = judge("r", &found(&[]), &s, false, K);
        assert_eq!(
            staged.warn,
            Vec::<(String, String)>::new(),
            "a staged run reads too little to call an entry stale"
        );
    }

    #[test]
    fn an_entry_without_a_reason_is_refused() {
        let dir = std::env::temp_dir().join(format!("goh-estate-seed-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("mkdir");
        std::fs::write(
            dir.join(K.file),
            r#"{"entries":[{"root":"r","repo":"x","path":"a.sh","count":1,"reason":""}]}"#,
        )
        .expect("write");
        let e = load(&dir, K).expect_err("no reason is refused");
        assert!(
            e.contains("is not a x seed") && e.contains("has no reason"),
            "{e}"
        );
        std::fs::remove_dir_all(&dir).expect("rm");
        assert_eq!(load(&dir, K).expect("missing is empty"), Seed::new());
    }
}
