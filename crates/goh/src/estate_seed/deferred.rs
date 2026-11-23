//! A settle waits for the branch the store is keyed to (GOH.SEEDBRANCH).
//!
//! The private store is one file per check for the whole machine, keyed by repository root and
//! never by branch, and every checkout of a repository reads the same entries. A commit's settle
//! (GOH.SEEDSTALE, D-0005) wrote it at once, so a commit on a LANE branch that fixed seeded sites
//! deleted their entries while main still carried the sites, and every full run on main went red
//! on them (three times on 2026-11-22, the last holding a release's push). So a settle made off
//! the store's branch writes a LICENCE under `<store>/deferred/<seed file's stem>/` instead: the store is
//! untouched; any full run reads a licensed entry at what it finds, so the lane's own runs are not
//! called stale; and the first full run ON the store's branch that finds the change applies it and
//! deletes the licence. An unlicensed stale entry stays red there: the push's backstop is kept.

use super::{settle::Settle, write_store_file, Kind, Seed};
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// The branch the store is keyed to: the one `origin/HEAD` names, else `main`.
#[must_use]
pub fn store_branch(checkout: &Path) -> String {
    git(
        checkout,
        &["symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"],
    )
    .and_then(|r| r.split_once('/').map(|(_, b)| b.to_string()))
    .unwrap_or_else(|| "main".to_string())
}

/// The branch `checkout` is on, when it is on the store's: `Err(the store's branch)` otherwise.
///
/// An export of HEAD (`.goh-head`) answers from the live checkout it was cut from; a detached HEAD
/// or an unknown checkout is never the store's branch.
///
/// # Errors
///
/// `checkout` is not on the store's branch; the error names that branch.
pub fn on_store_branch(checkout: &Path) -> Result<(), String> {
    let live = if checkout.join(".goh-head").is_file() {
        crate::goh_dir::live_root_for(checkout)
    } else {
        Some(checkout.to_path_buf())
    };
    let Some(live) = live else {
        return Err("main".to_string());
    };
    let want = store_branch(&live);
    match git(&live, &["symbolic-ref", "-q", "--short", "HEAD"]) {
        Some(b) if b == want => Ok(()),
        _ => Err(want),
    }
}

fn git(dir: &Path, args: &[&str]) -> Option<String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(args)
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
    (out.status.success() && !s.is_empty()).then_some(s)
}

fn fnv(s: &str) -> u64 {
    s.bytes().fold(0xcbf2_9ce4_8422_2325, |h, b| {
        (h ^ u64::from(b)).wrapping_mul(0x0100_0000_01b3)
    })
}

fn licence_dir(store: &Path, file: &str) -> PathBuf {
    store
        .join("deferred")
        .join(file.strip_suffix(".json").unwrap_or(file))
}

/// License `store_entries` of `repo` in `file` to settle on `branch`, and say so.
#[must_use]
pub fn license(
    store: &Path,
    file: &str,
    repo: &str,
    store_entries: &BTreeMap<String, usize>,
    (branch, tag): (&str, &str),
) -> String {
    let mut out = String::new();
    let dir = licence_dir(store, file);
    for path in store_entries.keys() {
        let body = serde_json::json!({"root": repo, "path": path, "branch": branch});
        let name = format!("{repo}-{:016x}.json", fnv(path));
        let tmp = dir.join(format!(".{name}.tmp"));
        let done = std::fs::create_dir_all(&dir)
            .and_then(|()| std::fs::write(&tmp, format!("{body}\n")))
            .and_then(|()| std::fs::rename(&tmp, dir.join(&name)));
        let _ = match done {
            Ok(()) => writeln!(
                out,
                "{} {tag} {path}: its entry in {} settles when this change reaches {branch} \
                 (GOH.SEEDBRANCH)",
                goh_tui::START,
                store.join(file).display()
            ),
            Err(e) => writeln!(
                out,
                "{} {tag} {path}: could not license its settle in {}: {e}",
                goh_tui::WARN,
                dir.display()
            ),
        };
    }
    out
}

/// The licensed paths of `repo` in `file`: `path -> its licence file`.
fn licensed(store: &Path, file: &str, repo: &str) -> BTreeMap<String, PathBuf> {
    let Ok(rd) = std::fs::read_dir(licence_dir(store, file)) else {
        return BTreeMap::new();
    };
    let prefix = format!("{repo}-");
    rd.filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .is_some_and(|n| n.to_string_lossy().starts_with(&prefix))
        })
        .filter_map(|p| {
            let v: serde_json::Value =
                serde_json::from_str(&std::fs::read_to_string(&p).ok()?).ok()?;
            Some((v["path"].as_str()?.to_string(), p))
        })
        .collect()
}

/// A full run's licensed settles: read at what is found in `now`, applied on the store's branch.
///
/// Returns what it did. Only a STORE entry above what is found, with a licence, is touched; a
/// licence whose entry is gone is deleted; one whose change has not arrived waits.
pub fn consume(
    root: &Path,
    repo: &str,
    found: &BTreeMap<String, usize>,
    now: &mut Seed,
    (store, kind): (&Path, Kind),
) -> String {
    let lic = licensed(store, kind.file, repo);
    if lic.is_empty() {
        return String::new();
    }
    let mut due = Settle::default();
    for (path, file) in &lic {
        let n = found.get(path).copied().unwrap_or(0);
        match now.get(&(repo.to_string(), path.clone())) {
            Some(h) if h.stored && n < h.count => {
                due.store.insert(path.clone(), n);
            }
            // The change has not reached this checkout: the licence waits for it.
            Some(h) if h.stored => {}
            _ => {
                let _ = std::fs::remove_file(file);
            }
        }
    }
    if due.store.is_empty() {
        return String::new();
    }
    *now = super::settle::settled_seed(now, repo, &due);
    let file = store.join(kind.file);
    if on_store_branch(root).is_err() {
        return String::new();
    }
    match write_store_file(store, kind.file, repo, &due.store) {
        Ok(()) => {
            for path in due.store.keys() {
                let _ = std::fs::remove_file(&lic[path]);
            }
            due.store.iter().fold(String::new(), |mut out, (path, n)| {
                let _ = writeln!(
                    out,
                    "{} {path}: its licensed entry in {} settled to {} now the change is on this \
                     branch (GOH.SEEDBRANCH)",
                    goh_tui::START,
                    file.display(),
                    goh_tui::counted(*n, kind.unit)
                );
                out
            })
        }
        Err(m) => format!(
            "{} could not settle {}: {m}; its licence is kept\n",
            goh_tui::WARN,
            file.display()
        ),
    }
}
