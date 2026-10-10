//! `goh fixed-sleep`: a fixed sleep never stands in for a readiness signal (BACKLOG 4.10).
//!
//! A `time.sleep(1)` followed by `proc.poll()` asks "is it done?" after a guess at how long it
//! takes, and on a loaded machine the guess is sometimes short: the read says "not yet" about a
//! thing that was merely slow, and the caller acts on it. `ZoneWM` shipped three of these, each
//! fixed with a real signal, and nothing stopped the fourth: a capture racing the work it
//! captured, a harness reading a later marker on an earlier signal, and a lock read as held after
//! "not exited after 1 s" (fail-open under load). The finder is `scan.rs`.
//!
//! The debt is a per-repo SEED, `fixed_sleep_seed.json` at the repo root: how many such sleeps
//! each file had when the repo adopted the check. It may only fall. A file over its count is red
//! with its sites; a file under it is red too, until the seed is lowered (`--seed` rewrites it), so
//! a fixed site cannot leave headroom for a new one; and an entry for a file the scan no longer
//! reads is stale. A repo opts in by calling the command from its own gate.

pub mod scan;

use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::path::Path;

const TAG: &str = "[fixed_sleep]";
/// The seed's file name, at the repo root.
pub const SEED_FILE: &str = "fixed_sleep_seed.json";

/// The findings per file the scan read, and the files it could not parse.
#[derive(Debug, Default)]
pub struct Scanned {
    pub read: usize,
    pub found: BTreeMap<String, Vec<scan::Finding>>,
    pub unreadable: Vec<(String, String)>,
}

/// The verdict over a scan and a seed: (exit code, report). Pure, so every direction is a unit
/// test.
#[must_use]
pub fn judge(scanned: &Scanned, seed: &BTreeMap<String, usize>) -> (i32, String) {
    let mut s = String::new();
    let mut red = false;
    for (path, why) in &scanned.unreadable {
        let _ = writeln!(s, "✗ {TAG} {path} is not Python this check can read: {why}");
        red = true;
    }
    for (path, sites) in &scanned.found {
        let allowed = seed.get(path).copied().unwrap_or(0);
        if sites.len() > allowed {
            red = true;
            for f in sites {
                let _ = writeln!(
                    s,
                    "✗ {TAG} {path}:{}: a fixed sleep, then `{}`: wait on the signal itself, \
                     in a loop bounded by a deadline that refuses on expiry",
                    f.line, f.read
                );
            }
            if allowed > 0 {
                let _ = writeln!(
                    s,
                    "  ({path} is seeded with {allowed}; it has {})",
                    sites.len()
                );
            }
        }
    }
    for (path, &allowed) in seed {
        let has = scanned.found.get(path).map_or(0, Vec::len);
        if has < allowed {
            red = true;
            let _ = writeln!(
                s,
                "✗ {TAG} {path} is seeded with {allowed} and has {has}: lower the seed \
                 (goh fixed-sleep --seed), so a fixed site leaves no room for a new one"
            );
        }
    }
    let total: usize = scanned.found.values().map(Vec::len).sum();
    if red {
        let _ = writeln!(s, "→ {TAG} read {} Python file(s)", scanned.read);
        return (1, s);
    }
    let _ = writeln!(
        s,
        "✓ {TAG} no new fixed sleep stands in for a readiness signal: {} Python file(s) read, \
         {total} seeded",
        scanned.read
    );
    (0, s)
}

/// Read the seed: absent is empty; malformed is an ERROR, never an empty seed -- a gate must
/// not be able to switch itself off by corrupting its own exemptions.
///
/// # Errors
///
/// What is wrong with the file.
pub fn load_seed(root: &Path) -> Result<BTreeMap<String, usize>, String> {
    let path = root.join(SEED_FILE);
    let text = match std::fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(BTreeMap::new()),
        Err(e) => return Err(format!("cannot read {SEED_FILE}: {e}")),
    };
    let value: serde_json::Value =
        serde_json::from_str(&text).map_err(|e| format!("{SEED_FILE} is not JSON: {e}"))?;
    let files = value
        .get("files")
        .and_then(serde_json::Value::as_object)
        .ok_or_else(|| format!("{SEED_FILE} has no \"files\" object"))?;
    files
        .iter()
        .map(|(k, v)| {
            v.as_u64()
                .and_then(|n| usize::try_from(n).ok())
                .map(|n| (k.clone(), n))
                .ok_or_else(|| format!("{SEED_FILE}: {k}'s count is not a whole number"))
        })
        .collect()
}

/// The seed a scan implies: every file with a finding, and its count.
#[must_use]
pub fn seed_text(scanned: &Scanned) -> String {
    let files: serde_json::Map<String, serde_json::Value> = scanned
        .found
        .iter()
        .map(|(k, v)| (k.clone(), serde_json::Value::from(v.len())))
        .collect();
    let doc = serde_json::json!({
        "why": "fixed sleeps standing in for a readiness signal when this repo adopted goh \
                fixed-sleep; a count may only fall (gates_of_heck BACKLOG 4.10)",
        "files": files,
    });
    serde_json::to_string_pretty(&doc).unwrap_or_default() + "\n"
}

/// Scan `files` (repo-relative) under `root`.
#[must_use]
pub fn scan_files(root: &Path, files: &[String]) -> Scanned {
    let mut out = Scanned::default();
    let python = |f: &&String| {
        Path::new(f)
            .extension()
            .is_some_and(|e| e.eq_ignore_ascii_case("py"))
    };
    for rel in files.iter().filter(python) {
        let Ok(text) = std::fs::read_to_string(root.join(rel)) else {
            continue;
        };
        out.read += 1;
        match scan::findings(&text) {
            Ok(found) if !found.is_empty() => {
                out.found.insert(rel.clone(), found);
            }
            Ok(_) => {}
            Err(why) => out.unreadable.push((rel.clone(), why)),
        }
    }
    out
}

/// `goh fixed-sleep [--seed] [--exclude RE]` over the working tree's listed files.
#[must_use]
pub fn run_command(seed: bool, exclude: &str) -> i32 {
    let root = crate::gitutil::repo_root().map_or_else(
        || std::env::current_dir().unwrap_or_default(),
        std::path::PathBuf::from,
    );
    let listed = match crate::gitutil::listed_files(&root, false) {
        Ok(l) => l,
        Err(m) => {
            eprintln!("✗ {TAG} {m}");
            return 2;
        }
    };
    let filter = match crate::steps::compile_exclude(exclude) {
        Ok(f) => f,
        Err(m) => {
            eprintln!("✗ {TAG} {m}");
            return 2;
        }
    };
    let files: Vec<String> = listed
        .into_iter()
        .filter(|f| !filter.as_ref().is_some_and(|x| x.is_match(f)))
        .collect();
    let scanned = scan_files(&root, &files);
    if seed {
        let text = seed_text(&scanned);
        if let Err(e) = std::fs::write(root.join(SEED_FILE), text) {
            eprintln!("✗ {TAG} cannot write {SEED_FILE}: {e}");
            return 2;
        }
        let total: usize = scanned.found.values().map(Vec::len).sum();
        println!(
            "✓ {TAG} seeded {SEED_FILE}: {total} site(s) in {} file(s), of {} read",
            scanned.found.len(),
            scanned.read
        );
        return 0;
    }
    let seeded = match load_seed(&root) {
        Ok(s) => s,
        Err(m) => {
            eprintln!("✗ {TAG} {m}");
            return 2;
        }
    };
    let (code, text) = judge(&scanned, &seeded);
    print!("{text}");
    code
}

#[cfg(test)]
mod tests;
