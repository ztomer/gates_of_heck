//! `goh rust-scope --check-depinfo`: what the last build of a workspace actually READ (P3).
//!
//! Split from `rust_scope.rs` for the line cap. After a green group, the compiler's dep-info
//! (`*.d`) and every build script's `rerun-if-changed` are read, and any file the build read that
//! lies outside the scope is named -- the caller then records nothing. The cache checks itself.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use crate::rust_scope::{metadata, rel, repo_root};

pub(crate) fn covered(entry_set: &[String], rel_path: &str) -> bool {
    entry_set
        .iter()
        .any(|e| e == "." || rel_path == e || rel_path.starts_with(&format!("{e}/")))
}

/// The package a per-unit build directory belongs to (cargo 1.100+): `build/<pkg>/<hash>/{out,run}`.
fn unit_package(dir: &Path) -> Option<String> {
    let unit = dir.parent()?;
    let pkg = unit.parent()?;
    let under_build = pkg.parent()?.file_name().is_some_and(|b| b == "build");
    under_build.then(|| pkg.file_name().map(|n| n.to_string_lossy().into_owned()))?
}

fn dep_files(build_dir: &Path, prefixes: &[String]) -> Vec<PathBuf> {
    let mut found = Vec::new();
    let mut stack = vec![(build_dir.to_path_buf(), 0)];
    while let Some((dir, depth)) = stack.pop() {
        let Ok(rd) = std::fs::read_dir(&dir) else {
            continue;
        };
        let leaf = dir.file_name().map(|d| d.to_string_lossy().into_owned());
        // Cargo 1.100's per-unit layout names the package by directory, not by file prefix.
        let unit_pkg = match leaf.as_deref() {
            Some("out" | "run") => unit_package(&dir).filter(|pkg| {
                prefixes
                    .iter()
                    .any(|p| p.strip_suffix('-') == Some(pkg.as_str()))
            }),
            _ => None,
        };
        for entry in rd.filter_map(Result::ok) {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            if path.is_dir() {
                if depth < 6 {
                    stack.push((path, depth + 1));
                }
                continue;
            }
            let is_d = path
                .extension()
                .is_some_and(|e| e.eq_ignore_ascii_case("d"));
            let dep_info = (leaf.as_deref() == Some("deps")
                && is_d
                && prefixes.iter().any(|p| name.starts_with(p)))
                || (leaf.as_deref() == Some("out") && is_d && unit_pkg.is_some());
            let script_output = (name == "output"
                && leaf
                    .as_deref()
                    .is_some_and(|d| prefixes.iter().any(|p| d.starts_with(p))))
                || (name == "stdout" && leaf.as_deref() == Some("run") && unit_pkg.is_some());
            if dep_info || script_output {
                found.push(path);
            }
        }
    }
    found
}

/// A build script's captured output: `<pkg>-<hash>/output`, or cargo 1.100's `<hash>/run/stdout`.
fn is_script_output(file: &Path) -> bool {
    file.file_name().is_some_and(|n| n == "output")
        || (file.file_name().is_some_and(|n| n == "stdout")
            && file
                .parent()
                .and_then(Path::file_name)
                .is_some_and(|d| d == "run"))
}

/// The package whose build script wrote `file`: `build/<pkg>/<hash>/run/stdout` (cargo 1.100+) or
/// `build/<pkg>-<hash>/output`.
fn script_package(file: &Path) -> Option<String> {
    let dir = file.parent()?;
    if file.file_name().is_some_and(|n| n == "stdout") {
        return unit_package(dir);
    }
    let leaf = dir.file_name()?.to_string_lossy().into_owned();
    leaf.rsplit_once('-').map(|(pkg, _hash)| pkg.to_owned())
}

/// `path` with `.` and `..` folded away by name: a file a build re-runs on may not exist, and
/// `canonicalize` answers only for one that does.
fn lexical(path: &Path) -> PathBuf {
    use std::path::Component;
    let mut out = PathBuf::new();
    for c in path.components() {
        match c {
            Component::CurDir => {}
            Component::ParentDir => {
                if !out.pop() {
                    out.push(c);
                }
            }
            _ => out.push(c),
        }
    }
    out
}

/// The directory a relative path in `file` is relative to, as cargo reads it (GOH.RERUNPATH,
/// probed on cargo 1.100.0): a build script's `rerun-if-changed` is relative to its PACKAGE ROOT;
/// rustc's dep-info is relative to the WORKSPACE ROOT, where cargo runs rustc for a path package
/// it names relatively (any other source path is absolute, and so is every path rustc derives).
/// `None` when the package is not one `cargo metadata` named.
fn relative_base(
    file: &Path,
    pkg_roots: &std::collections::BTreeMap<String, PathBuf>,
    ws_root: &Path,
) -> Option<PathBuf> {
    if is_script_output(file) {
        return script_package(file).and_then(|p| pkg_roots.get(&p).cloned());
    }
    Some(ws_root.to_path_buf())
}

pub(crate) fn depinfo_paths(file: &Path, text: &str) -> Vec<PathBuf> {
    if is_script_output(file) {
        // A build script's output: `cargo:rerun-if-changed=PATH`, relative to its package.
        return text
            .lines()
            .filter_map(|l| {
                l.strip_prefix("cargo:rerun-if-changed=")
                    .or_else(|| l.strip_prefix("cargo::rerun-if-changed="))
            })
            .map(PathBuf::from)
            .collect();
    }
    let Some(first) = text.lines().next() else {
        return Vec::new();
    };
    let deps = first.split_once(": ").map_or("", |(_, d)| d);
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut chars = deps.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '\\' if chars.peek() == Some(&' ') => {
                cur.push(' ');
                chars.next();
            }
            ' ' => {
                if !cur.is_empty() {
                    out.push(PathBuf::from(std::mem::take(&mut cur)));
                }
            }
            _ => cur.push(c),
        }
    }
    if !cur.is_empty() {
        out.push(PathBuf::from(cur));
    }
    out
}

/// Files the last build of `cargo_dir`'s members read that lie outside `entries`.
///
/// # Errors
///
/// When cargo or git cannot answer.
pub fn depinfo_escapes(cargo_dir: &Path, entries: &[String]) -> Result<Vec<String>, String> {
    depinfo_escapes_keyed(cargo_dir, entries, &crate::rust_scope::keyed_roots())
}

/// [`depinfo_escapes`], with the keyed roots named: a file read inside one is keyed by the proven
/// identity, so it is not an escape.
///
/// # Errors
///
/// As [`depinfo_escapes`].
pub fn depinfo_escapes_keyed(
    cargo_dir: &Path,
    entries: &[String],
    keyed: &[PathBuf],
) -> Result<Vec<String>, String> {
    let root = repo_root(cargo_dir)?;
    let meta = metadata(cargo_dir, true)?;
    let build = meta["build_directory"]
        .as_str()
        .or_else(|| meta["target_directory"].as_str())
        .map(PathBuf::from)
        .ok_or("cargo metadata names no build directory")?;
    let target = meta["target_directory"].as_str().map(PathBuf::from);
    let canon = |p: PathBuf| std::fs::canonicalize(&p).unwrap_or(p);
    let ws_root = meta["workspace_root"]
        .as_str()
        .map(|w| canon(PathBuf::from(w)))
        .ok_or("cargo metadata names no workspace root")?;
    let mut prefixes = Vec::new();
    let mut pkg_roots = std::collections::BTreeMap::new();
    if let Some(pkgs) = meta["packages"].as_array() {
        for p in pkgs {
            if let Some(name) = p["name"].as_str() {
                prefixes.push(format!("{}-", name.replace('-', "_")));
                prefixes.push(format!("{name}-"));
                if let Some(dir) = p["manifest_path"]
                    .as_str()
                    .and_then(|m| Path::new(m).parent())
                {
                    pkg_roots.insert(name.to_owned(), canon(dir.to_path_buf()));
                }
            }
            for t in p["targets"].as_array().into_iter().flatten() {
                if let Some(name) = t["name"].as_str() {
                    prefixes.push(format!("{}-", name.replace('-', "_")));
                    prefixes.push(format!("lib{}-", name.replace('-', "_")));
                }
            }
        }
    }
    let home = std::env::var("CARGO_HOME").map_or_else(
        |_| {
            std::env::var("HOME")
                .map(|h| PathBuf::from(h).join(".cargo"))
                .unwrap_or_default()
        },
        PathBuf::from,
    );
    // The repository's own git dir (a worktree's is under the main checkout's `.git/worktrees/`):
    // a build script that re-runs on `index` or `logs/HEAD` (goh's source stamp) reads no input
    // the proof keys -- the cache requires a clean tree, and that tree is the key (GOH.LANDGAPS).
    let git_dirs: Vec<PathBuf> = ["--absolute-git-dir", "--git-common-dir"]
        .iter()
        .filter_map(|flag| crate::rust_scope::run(&root, "git", &["rev-parse", flag]).ok())
        .map(|out| {
            let p = PathBuf::from(out.trim());
            let p = if p.is_absolute() { p } else { root.join(p) };
            std::fs::canonicalize(&p).unwrap_or(p)
        })
        .collect();
    let mut bad = BTreeSet::new();
    for file in dep_files(&build, &prefixes) {
        let text = std::fs::read_to_string(&file).unwrap_or_default();
        let base = relative_base(&file, &pkg_roots, &ws_root);
        for dep in depinfo_paths(&file, &text) {
            let dep = if dep.is_absolute() {
                lexical(&dep)
            } else if let Some(base) = &base {
                lexical(&base.join(dep))
            } else {
                // No package to resolve it against: name it, so the cache records nothing.
                bad.insert(format!(
                    "{} (relative to an unknown package)",
                    dep.display()
                ));
                continue;
            };
            let s = dep.to_string_lossy();
            let canon = std::fs::canonicalize(&dep).unwrap_or_else(|_| dep.clone());
            if git_dirs.iter().any(|g| canon.starts_with(g)) {
                continue;
            }
            if dep.starts_with(&build)
                || target.as_ref().is_some_and(|t| dep.starts_with(t))
                || dep.starts_with(&home)
                || s.contains("/.rustup/")
                || s.contains("/rustlib/")
            {
                continue;
            }
            match rel(&root, &dep) {
                Some(r) if covered(entries, &r) => {}
                Some(r) => {
                    bad.insert(r);
                }
                None if crate::rust_scope::under_keyed(&dep, keyed) => {}
                None => {
                    bad.insert(s.into_owned());
                }
            }
        }
    }
    Ok(bad.into_iter().collect())
}
