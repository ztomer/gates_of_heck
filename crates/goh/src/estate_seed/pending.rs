//! A commit's settle waits for the whole commit (GOH.SEEDREFUSED, D-0005).
//!
//! GOH.SEEDSTALE let a commit's own run of a seeded check lower or delete the store entries its
//! change settles, and the check wrote the store the moment IT passed. A commit refused by a LATER
//! step of the same gate (the repository's tests, the line cap, the python twin) had already
//! pruned the store: on 2026-11-12 a necrohand agent's refused commits deleted 13 plural_hedge
//! entries for files it then reverted. So the hook (`gates/commit_gate.sh`) names a pending
//! directory in `GOH_SEED_PENDING`; a check run under it JOURNALS its settle there instead of
//! writing the store, and the hook runs `goh seed-settle <dir>` only once every step has passed.
//! A refused commit's journal is deleted with its directory, unapplied.

use super::{settle::settled_lines, write_store_file, Kind, Settle};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

/// The variable the commit hook names its pending directory in.
pub const PENDING_ENV: &str = "GOH_SEED_PENDING";

/// The pending directory this run journals into, when a commit's hook named one.
pub(super) fn dir() -> Option<PathBuf> {
    std::env::var_os(PENDING_ENV)
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
}

/// Journal `settled` for the store `store` into `pending`, and say what waits for the commit.
pub(super) fn journal(
    pending: &Path,
    store: &Path,
    kind: Kind,
    repo: &str,
    settled: &Settle,
    tag: &str,
    item: &str,
) -> String {
    static SEQ: AtomicUsize = AtomicUsize::new(0);
    let body = serde_json::json!({
        "store": store.display().to_string(),
        "file": kind.file,
        "unit": kind.unit,
        "root": repo,
        "tag": tag,
        "settle": settled.store,
    });
    let name = format!(
        "{}-{}-{}",
        std::process::id(),
        SEQ.fetch_add(1, Ordering::Relaxed),
        kind.file
    );
    let tmp = pending.join(format!(".{name}.tmp"));
    let done = std::fs::write(&tmp, format!("{body}\n"))
        .and_then(|()| std::fs::rename(&tmp, pending.join(name)));
    if let Err(e) = done {
        let _ = std::fs::remove_file(&tmp);
        return format!(
            "{} {tag} could not journal the {item} seed settle in {}: {e}; the push will call its \
             entry stale\n",
            goh_tui::WARN,
            pending.display()
        );
    }
    let file = store.join(kind.file);
    settled.store.keys().fold(String::new(), |mut out, path| {
        out.push_str(&format!(
            "{} {tag} {path}: its entry in {} settles once every step of this commit passes \
             (GOH.SEEDREFUSED)\n",
            goh_tui::START,
            file.display()
        ));
        out
    })
}

/// One store file's settle, merged over every journal naming it.
#[derive(Default)]
struct Due {
    unit: String,
    tag: String,
    store: BTreeMap<String, usize>,
}

/// Apply every journal in `pending` (the commit passed): `(exit code, what it did)`.
///
/// Journals naming one store file, repository and entry merge to the lowest count (two runs of
/// one check journal the same settle). A failed write is a warning -- the push's stale check is the
/// backstop -- never a red commit, which has already passed. Each journal is deleted once read.
#[must_use]
pub fn apply(pending: &Path) -> (i32, String) {
    let mut out = String::new();
    let mut due: BTreeMap<(PathBuf, String, String), Due> = BTreeMap::new();
    let mut names: Vec<PathBuf> = match std::fs::read_dir(pending) {
        Ok(d) => d
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|p| {
                p.file_name()
                    .is_some_and(|n| !n.to_string_lossy().starts_with('.'))
            })
            .collect(),
        Err(e) => {
            return (
                2,
                format!(
                    "{} goh seed-settle {}: {e}\n",
                    goh_tui::ERR,
                    pending.display()
                ),
            )
        }
    };
    names.sort();
    for path in &names {
        let parsed = std::fs::read_to_string(path)
            .map_err(|e| e.to_string())
            .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).map_err(|e| e.to_string()));
        let _ = std::fs::remove_file(path);
        let v = match parsed {
            Ok(v) => v,
            Err(m) => {
                out.push_str(&format!(
                    "{} goh seed-settle {}: {m}; its settle is dropped\n",
                    goh_tui::WARN,
                    path.display()
                ));
                continue;
            }
        };
        let s = |k: &str| v[k].as_str().unwrap_or_default().to_string();
        let d = due
            .entry((PathBuf::from(s("store")), s("file"), s("root")))
            .or_default();
        d.unit = s("unit");
        d.tag = s("tag");
        for (p, n) in v["settle"].as_object().into_iter().flatten() {
            let Some(n) = n.as_u64().and_then(|n| usize::try_from(n).ok()) else {
                continue;
            };
            let at = d.store.entry(p.clone()).or_insert(n);
            *at = (*at).min(n);
        }
    }
    for ((store, file, root), d) in &due {
        match write_store_file(store, file, root, &d.store) {
            Ok(()) => out.push_str(&settled_lines(&d.store, &d.unit, &d.tag, &store.join(file))),
            Err(m) => out.push_str(&format!(
                "{} {} could not settle {}: {m}; the push will call its entry stale\n",
                goh_tui::WARN,
                d.tag,
                store.join(file).display()
            )),
        }
    }
    (0, out)
}
