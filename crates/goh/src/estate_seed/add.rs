//! `goh seed-add`: the one writer that ADDS entries to the private store (GOH.GRACEEND).
//!
//! The store's only writer was the commit's settle (`settle::write_store_file`), which may only
//! lower or delete an entry. A new scanner's estate sweep found the sites no seed named, and the
//! entries it saved for the store had no tool to land them: a coordinator hand-edited
//! `~/.local/state/goh/seeds`, and a hand edit is the one that drops a reason, spells a key
//! another way, or races a commit's settle. So the store is written by goh in both directions,
//! under the one lock on the store directory the settle takes.
//!
//! The input is a seed file's own shape, `{"why": ..., "entries": [{root, repo, path, count,
//! reason}, ...]}` (or the bare `entries` array, when the store file exists): what a sweep saves
//! as its pending file. Every entry is checked before anything is written -- a root that is not a
//! full commit id, an empty path, a count of 0, an entry with no reason -- and one bad entry
//! refuses the whole file. An entry whose root and path the store already holds is REPLACED,
//! and the line says so with both counts: the pending file is the reviewed list. The store file is
//! created when absent, carrying the input's `why` (refused without one). The judge still decides
//! what an entry means: a store entry for a repository whose CI runs goh is refused at load
//! (GOH.STORECI), so this tool never makes a CI repository green.
//!
//! WHERE IT WRITES (GOH.GRACEFWD): where the entry's repository keeps its other seeds
//! (`route.rs`). An entry for a repository whose seeds are tracked goes to that repository's own
//! tracked file (left for it to commit) and leaves the store; every other entry goes to the store.

mod route;

use super::store_dir;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write as _;
use std::path::{Path, PathBuf};

/// One entry as the input spells it, checked.
#[derive(Debug, Clone, PartialEq, Eq)]
struct Entry {
    root: String,
    repo: String,
    path: String,
    count: u64,
    reason: String,
}

impl Entry {
    fn json(&self) -> serde_json::Value {
        serde_json::json!({
            "root": self.root,
            "repo": self.repo,
            "path": self.path,
            "count": self.count,
            "reason": self.reason,
        })
    }
}

/// Whether `name` is a seed file's name: `<kind>_seed.json`, no directory.
pub(super) fn seed_name(name: &str) -> bool {
    name.strip_suffix("_seed.json")
        .is_some_and(|k| !k.is_empty() && k.bytes().all(|b| b.is_ascii_lowercase() || b == b'_'))
}

/// The input's `why` and its entries, every one checked.
fn read_input(text: &str) -> Result<(Option<String>, Vec<Entry>), String> {
    let v: serde_json::Value = serde_json::from_str(text).map_err(|e| e.to_string())?;
    let (why, list) = match &v {
        serde_json::Value::Array(a) => (None, a),
        serde_json::Value::Object(o) => (
            o.get("why")
                .and_then(serde_json::Value::as_str)
                .map(str::to_string),
            o.get("entries")
                .and_then(serde_json::Value::as_array)
                .ok_or("no `entries` array")?,
        ),
        _ => return Err("neither a seed file nor an `entries` array".to_string()),
    };
    let mut out = Vec::new();
    for (i, e) in list.iter().enumerate() {
        let s = |k: &str| {
            e.get(k)
                .and_then(serde_json::Value::as_str)
                .map(str::trim)
                .filter(|v| !v.is_empty())
                .map(str::to_string)
        };
        let n = i + 1;
        let root = s("root").ok_or_else(|| format!("entry {n} has no root"))?;
        if root.len() != 40 || !root.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err(format!("entry {n}: root {root} is not a full commit id"));
        }
        let repo = s("repo").ok_or_else(|| format!("entry {n} has no repo"))?;
        let path = s("path").ok_or_else(|| format!("entry {n} ({repo}) has no path"))?;
        let count = e
            .get("count")
            .and_then(serde_json::Value::as_u64)
            .filter(|c| *c > 0)
            .ok_or_else(|| format!("entry {n} ({repo} {path}) has no count above 0"))?;
        let reason =
            s("reason").ok_or_else(|| format!("entry {n} ({repo} {path}) has no reason"))?;
        out.push(Entry {
            root,
            repo,
            path,
            count,
            reason,
        });
    }
    let mut seen = std::collections::BTreeSet::new();
    for e in &out {
        if !seen.insert((&e.root, &e.path)) {
            return Err(format!("{} {} is listed twice", e.repo, e.path));
        }
    }
    Ok((why, out))
}

/// Add `input`'s entries to the store file `name` in `dir`: `(exit code, what it did)`.
#[cfg(test)]
fn add_in(dir: &Path, name: &str, input: &Path) -> (i32, String) {
    add_routed(Some(dir), &[], name, input)
}

/// Add `input`'s entries to the seed file `name`, each where its repository keeps its seeds
/// (`route.rs`): the tracked file of the repository among `repos` with its root when that
/// repository keeps its seeds tracked, else the store `store`.
#[must_use]
pub fn add_routed(
    store: Option<&Path>,
    repos: &[PathBuf],
    name: &str,
    input: &Path,
) -> (i32, String) {
    let fail = |m: String| (2, format!("{} goh seed-add: {m}\n", goh_tui::ERR));
    if !seed_name(name) {
        return fail(format!(
            "{name} is not a seed file's name (<kind>_seed.json)"
        ));
    }
    let (why, entries) = match std::fs::read_to_string(input)
        .map_err(|e| e.to_string())
        .and_then(|t| read_input(&t))
    {
        Ok(got) => got,
        Err(m) => return fail(format!("{}: {m}; nothing written", input.display())),
    };
    let roots: BTreeSet<String> = entries.iter().map(|e| e.root.clone()).collect();
    let homes = route::tracked_homes(&roots, repos);
    let mut groups: BTreeMap<Option<&PathBuf>, Vec<Entry>> = BTreeMap::new();
    for e in entries {
        groups.entry(homes.get(&e.root)).or_default().push(e);
    }
    let mut said = String::new();
    for (home, group) in &groups {
        let why = why.as_deref();
        let got = home.map_or_else(
            || into_store(store, name, why, group),
            |repo| into_tracked(repo, store, name, why, group),
        );
        match got {
            Ok(w) => said.push_str(&w),
            Err(m) => {
                let (code, err) = fail(m);
                return (code, format!("{said}{err}"));
            }
        }
    }
    (0, said)
}

/// `entries` into the store file `name` in `store`.
fn into_store(
    store: Option<&Path>,
    name: &str,
    why: Option<&str>,
    entries: &[Entry],
) -> Result<String, String> {
    let dir = store.ok_or("no private store (GOH_SEED_DIR, XDG_STATE_HOME and HOME unset)")?;
    std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    write(dir, name, why, entries)
}

/// `entries` into the repository `repo`'s own tracked `name`, and out of the store.
fn into_tracked(
    repo: &Path,
    store: Option<&Path>,
    name: &str,
    why: Option<&str>,
    entries: &[Entry],
) -> Result<String, String> {
    let wrote = write(repo, name, why, entries)?;
    let moved = store.map_or(Ok(0), |d| drop_from(d, name, entries))?;
    Ok(tracked_note(repo, name, moved, &wrote))
}

/// What a write into a repository's own tracked file adds to its line: where it went and why.
fn tracked_note(repo: &Path, name: &str, moved: usize, wrote: &str) -> String {
    let mut out = wrote.to_string();
    let _ = writeln!(
        out,
        "{} {} keeps its seeds tracked, so a runner reads them: commit {name} there{}",
        goh_tui::START,
        repo.display(),
        if moved > 0 {
            format!(
                " ({} moved out of the private store)",
                goh_tui::counted_as(moved, "entry", "entries")
            )
        } else {
            String::new()
        }
    );
    out
}

/// Delete `entries`' keys from the store file `name` in `dir`, if it holds them: how many went.
fn drop_from(dir: &Path, name: &str, entries: &[Entry]) -> Result<usize, String> {
    let file = dir.join(name);
    if !file.is_file() {
        return Ok(0);
    }
    let lock = std::fs::File::open(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    lock.lock().map_err(|e| format!("{}: {e}", dir.display()))?; // lock-order: estate_settle
    let text = std::fs::read_to_string(&file).map_err(|e| format!("{}: {e}", file.display()))?;
    let mut v: serde_json::Value =
        serde_json::from_str(&text).map_err(|e| format!("{}: {e}", file.display()))?;
    let held = v
        .get_mut("entries")
        .and_then(serde_json::Value::as_array_mut)
        .ok_or_else(|| format!("{}: no `entries` array", file.display()))?;
    let before = held.len();
    held.retain(|h| {
        let field = |k: &str| h.get(k).and_then(serde_json::Value::as_str);
        !entries.iter().any(|e| {
            field("root") == Some(e.root.as_str()) && field("path") == Some(e.path.as_str())
        })
    });
    let gone = before - held.len();
    if gone > 0 {
        replace(dir, name, &v)?;
    }
    Ok(gone)
}

/// `add_routed` over the private store (`store_dir`) and the repositories an entry may belong to
/// (`route::candidates`).
#[must_use]
pub fn add(name: &str, input: &Path) -> (i32, String) {
    let store = store_dir();
    add_routed(store.as_deref(), &route::candidates(), name, input)
}

/// Write `v` to `dir/name` whole, by a rename (the caller holds the directory's lock).
fn replace(dir: &Path, name: &str, v: &serde_json::Value) -> Result<(), String> {
    let file = dir.join(name);
    let mut body = serde_json::to_string_pretty(v).map_err(|e| e.to_string())?;
    body.push('\n');
    let tmp = dir.join(format!(".{name}.{}.tmp", std::process::id()));
    std::fs::write(&tmp, &body).map_err(|e| format!("{}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, &file).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("{}: {e}", file.display())
    })
}

fn write(dir: &Path, name: &str, why: Option<&str>, entries: &[Entry]) -> Result<String, String> {
    let file = dir.join(name);
    // The settle's lock (`settle::write_store_file`): one flock on the directory, never the file
    // the rename replaces, and no lock file a crashed holder would leave.
    let lock = std::fs::File::open(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    lock.lock().map_err(|e| format!("{}: {e}", dir.display()))?; // lock-order: estate_settle
    let mut v: serde_json::Value = match std::fs::read_to_string(&file) {
        Ok(text) => serde_json::from_str(&text).map_err(|e| format!("{}: {e}", file.display()))?,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            let why = why.ok_or_else(|| {
                format!(
                    "{} does not exist, and the input has no `why` to create it with",
                    file.display()
                )
            })?;
            serde_json::json!({"why": why, "entries": []})
        }
        Err(e) => return Err(format!("{}: {e}", file.display())),
    };
    let held = v
        .get_mut("entries")
        .and_then(serde_json::Value::as_array_mut)
        .ok_or_else(|| format!("{}: no `entries` array", file.display()))?;
    let mut out = String::new();
    let (mut added, mut replaced) = (0usize, 0usize);
    for e in entries {
        let at = held.iter().position(|h| {
            h.get("root").and_then(serde_json::Value::as_str) == Some(e.root.as_str())
                && h.get("path").and_then(serde_json::Value::as_str) == Some(e.path.as_str())
        });
        let Some(i) = at else {
            held.push(e.json());
            added += 1;
            continue;
        };
        let was = held[i].get("count").and_then(serde_json::Value::as_u64);
        if was == Some(e.count)
            && held[i].get("reason").and_then(serde_json::Value::as_str) == Some(e.reason.as_str())
        {
            continue;
        }
        held[i] = e.json();
        replaced += 1;
        let was = was.map_or_else(|| "none".to_string(), |c| c.to_string());
        let _ = writeln!(
            out,
            "{} {name}: {} {} replaced, count {was} -> {}",
            goh_tui::WARN,
            e.repo,
            e.path,
            e.count
        );
    }
    replace(dir, name, &v)?;
    let _ = writeln!(
        out,
        "{} {}: {} added, {} replaced, {} unchanged",
        goh_tui::OK,
        file.display(),
        goh_tui::counted_as(added, "entry", "entries"),
        replaced,
        entries.len() - added - replaced
    );
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    const ROOT: &str = "c5d904a9accb412cf585d8d0a3d8d25c96e243f3";

    fn entry(path: &str, count: u64) -> serde_json::Value {
        serde_json::json!({"root": ROOT, "repo": "r", "path": path, "count": count,
                           "reason": "unreviewed: a test"})
    }

    fn input(dir: &Path, v: &serde_json::Value) -> std::path::PathBuf {
        let p = dir.join("in.json");
        std::fs::write(&p, v.to_string()).expect("write input");
        p
    }

    fn held(dir: &Path) -> serde_json::Value {
        let t = std::fs::read_to_string(dir.join("x_seed.json")).expect("store file");
        serde_json::from_str(&t).expect("json")
    }

    #[test]
    fn a_new_store_file_is_created_with_the_inputs_why_and_entries() {
        let t = tempfile::tempdir().expect("tmp");
        let store = t.path().join("seeds");
        let src = input(
            t.path(),
            &serde_json::json!({"why": "w", "entries": [entry("a.sh", 2)]}),
        );
        let (code, said) = add_in(&store, "x_seed.json", &src);
        assert_eq!(code, 0, "{said}");
        let v = held(&store);
        assert_eq!(v["why"], "w");
        assert_eq!(v["entries"][0]["count"], 2);
        assert!(said.contains("1 entry added"), "{said}");
    }

    #[test]
    fn an_existing_entry_is_replaced_and_said_and_the_rest_kept() {
        let t = tempfile::tempdir().expect("tmp");
        let store = t.path();
        let first =
            serde_json::json!({"why": "w", "entries": [entry("a.sh", 1), entry("b.sh", 1)]});
        std::fs::write(store.join("x_seed.json"), first.to_string()).expect("seed");
        let src = input(
            store,
            &serde_json::json!([entry("a.sh", 3), entry("c.sh", 1)]),
        );
        let (code, said) = add_in(store, "x_seed.json", &src);
        assert_eq!(code, 0, "{said}");
        assert!(said.contains("r a.sh replaced, count 1 -> 3"), "{said}");
        let v = held(store);
        let paths: Vec<_> = v["entries"]
            .as_array()
            .expect("arr")
            .iter()
            .map(|e| {
                (
                    e["path"].as_str().unwrap_or("").to_string(),
                    e["count"].as_u64(),
                )
            })
            .collect();
        assert_eq!(
            paths,
            vec![
                ("a.sh".into(), Some(3)),
                ("b.sh".into(), Some(1)),
                ("c.sh".into(), Some(1))
            ]
        );
        // Applied twice, nothing changes.
        let (code, said) = add_in(store, "x_seed.json", &src);
        assert_eq!(code, 0, "{said}");
        assert!(
            said.contains("0 entries added, 0 replaced, 2 unchanged"),
            "{said}"
        );
    }

    #[test]
    fn one_bad_entry_refuses_the_whole_file_and_writes_nothing() {
        let t = tempfile::tempdir().expect("tmp");
        let store = t.path().join("seeds");
        for (bad, why) in [
            (
                serde_json::json!({"root": "abc", "repo": "r", "path": "p", "count": 1, "reason": "x"}),
                "not a full commit id",
            ),
            (
                serde_json::json!({"root": ROOT, "repo": "r", "path": "p", "count": 0, "reason": "x"}),
                "no count above 0",
            ),
            (
                serde_json::json!({"root": ROOT, "repo": "r", "path": "p", "count": 1, "reason": " "}),
                "no reason",
            ),
            (entry("a.sh", 1), "listed twice"),
        ] {
            let src = input(
                t.path(),
                &serde_json::json!({"why": "w", "entries": [entry("a.sh", 1), bad]}),
            );
            let (code, said) = add_in(&store, "x_seed.json", &src);
            assert_eq!(code, 2, "{said}");
            assert!(said.contains(why), "{why}: {said}");
            assert!(!store.join("x_seed.json").exists(), "{why}: written");
        }
    }

    #[test]
    fn a_missing_store_file_needs_a_why_and_a_name_must_be_a_seed_files() {
        let t = tempfile::tempdir().expect("tmp");
        let src = input(t.path(), &serde_json::json!([entry("a.sh", 1)]));
        let (code, said) = add_in(t.path(), "x_seed.json", &src);
        assert_eq!(code, 2);
        assert!(said.contains("no `why`"), "{said}");
        for name in ["../x_seed.json", "x.json", "_seed.json", "X_seed.json"] {
            let (code, said) = add_in(t.path(), name, &src);
            assert_eq!(code, 2, "{name}");
            assert!(said.contains("not a seed file's name"), "{name}: {said}");
        }
    }
}
