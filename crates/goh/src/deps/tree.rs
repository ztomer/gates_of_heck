//! A Cargo tree -> its declared dependencies and locked versions.
//!
//! Port of the retired `checks/_dep_tree.py`: a manifest that does not parse is skipped, an
//! inherited requirement is read from the WORKSPACE ROOT, `path`/`git`
//! dependencies have no crates.io version and are dropped.

use std::path::{Path, PathBuf};

use toml::{Table, Value};

const DEP_TABLES: [&str; 3] = ["dependencies", "build-dependencies", "dev-dependencies"];
const SKIP: [&str; 6] = [
    "target",
    "vendor",
    ".git",
    "node_modules",
    "build",
    ".build",
];

/// One declared dependency.
#[derive(Clone)]
pub struct Dep {
    pub name: String,
    pub req: String,
    pub section: String,
}

/// Every `Cargo.toml` in the tree, vendored and build trees skipped, sorted.
#[must_use]
pub fn manifests(root: &Path) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = crate::gitutil::tree_files(root)
        .into_iter()
        .filter(|rel| {
            rel.rsplit('/').next() == Some("Cargo.toml")
                && !rel.split('/').any(|p| SKIP.contains(&p))
        })
        .map(|rel| root.join(rel))
        .filter(|p| p.is_file())
        .collect();
    out.sort();
    out
}

/// The manifest as a TOML table, or `None` when it does not parse.
#[must_use]
pub fn read_manifest(path: &Path) -> Option<Table> {
    std::fs::read_to_string(path).ok()?.parse::<Table>().ok()
}

fn workspace_dependencies(manifest: &Path, doc: &Table) -> Table {
    let ws_deps = |d: &Table| -> Option<Table> {
        d.get("workspace")?
            .as_table()?
            .get("dependencies")?
            .as_table()
            .cloned()
    };
    if let Some(deps) = ws_deps(doc) {
        return deps;
    }
    for parent in manifest.ancestors().skip(1) {
        let cand = parent.join("Cargo.toml");
        if !cand.is_file() {
            continue;
        }
        if let Some(other) = read_manifest(&cand) {
            if other.get("workspace").is_some_and(Value::is_table) {
                if let Some(deps) = ws_deps(&other) {
                    return deps;
                }
            }
        }
    }
    Table::new()
}

/// Direct dependencies, workspace inheritance resolved, in document order.
#[must_use]
pub fn declared_deps(doc: &Table, path: &Path) -> Vec<Dep> {
    let ws = workspace_dependencies(path, doc);
    let mut out = Vec::new();
    let mut take = |table: &Table, section: &str| {
        for (name, spec) in table {
            let req = match spec {
                Value::String(s) => s.clone(),
                Value::Table(t) => {
                    if t.contains_key("path") || t.contains_key("git") {
                        continue;
                    }
                    if t.get("workspace").and_then(Value::as_bool) == Some(true) {
                        match ws.get(name) {
                            Some(Value::String(s)) => s.clone(),
                            Some(Value::Table(w)) => w
                                .get("version")
                                .and_then(Value::as_str)
                                .unwrap_or("")
                                .to_owned(),
                            _ => continue,
                        }
                    } else {
                        match t.get("version").and_then(Value::as_str) {
                            Some(v) if !v.is_empty() => v.to_owned(),
                            _ => continue,
                        }
                    }
                }
                _ => continue,
            };
            out.push(Dep {
                name: name.clone(),
                req,
                section: section.to_owned(),
            });
        }
    };
    for table in DEP_TABLES {
        if let Some(t) = doc.get(table).and_then(Value::as_table) {
            take(t, table);
        }
    }
    if let Some(targets) = doc.get("target").and_then(Value::as_table) {
        for (plat, block) in targets {
            if let Some(block) = block.as_table() {
                for table in DEP_TABLES {
                    if let Some(t) = block.get(table).and_then(Value::as_table) {
                        take(t, &format!("target.{plat}.{table}"));
                    }
                }
            }
        }
    }
    out
}

/// Crate name -> every version the lockfile pins, in file order.
#[must_use]
pub fn lock_versions(lock: &Path) -> Vec<(String, Vec<String>)> {
    let Some(doc) = std::fs::read_to_string(lock)
        .ok()
        .and_then(|t| t.parse::<Table>().ok())
    else {
        return Vec::new();
    };
    let mut out: Vec<(String, Vec<String>)> = Vec::new();
    for pkg in doc
        .get("package")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let (Some(name), Some(version)) = (
            pkg.get("name").and_then(Value::as_str),
            pkg.get("version").and_then(Value::as_str),
        ) else {
            continue;
        };
        match out.iter_mut().find(|(n, _)| n == name) {
            Some((_, vs)) => vs.push(version.to_owned()),
            None => out.push((name.to_owned(), vec![version.to_owned()])),
        }
    }
    out
}

/// The lockfile that governs this manifest: its own, else the nearest above.
#[must_use]
pub fn nearest_lock(manifest: &Path) -> Option<PathBuf> {
    manifest
        .ancestors()
        .skip(1)
        .map(|d| d.join("Cargo.lock"))
        .find(|p| p.is_file())
}
