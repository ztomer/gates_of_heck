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

fn dep_files(build_dir: &Path, prefixes: &[String]) -> Vec<PathBuf> {
    let mut found = Vec::new();
    let mut stack = vec![(build_dir.to_path_buf(), 0)];
    while let Some((dir, depth)) = stack.pop() {
        let Ok(rd) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in rd.filter_map(Result::ok) {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            if path.is_dir() {
                if depth < 4 {
                    stack.push((path, depth + 1));
                }
                continue;
            }
            let dep_info = dir.file_name().is_some_and(|d| d == "deps")
                && path
                    .extension()
                    .is_some_and(|e| e.eq_ignore_ascii_case("d"))
                && prefixes.iter().any(|p| name.starts_with(p));
            let script_output = name == "output"
                && dir
                    .file_name()
                    .is_some_and(|d| prefixes.iter().any(|p| d.to_string_lossy().starts_with(p)));
            if dep_info || script_output {
                found.push(path);
            }
        }
    }
    found
}

pub(crate) fn depinfo_paths(file: &Path, text: &str) -> Vec<PathBuf> {
    if file.file_name().is_some_and(|n| n == "output") {
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
    let root = repo_root(cargo_dir)?;
    let meta = metadata(cargo_dir, true)?;
    let build = meta["build_directory"]
        .as_str()
        .or_else(|| meta["target_directory"].as_str())
        .map(PathBuf::from)
        .ok_or("cargo metadata names no build directory")?;
    let target = meta["target_directory"].as_str().map(PathBuf::from);
    let mut prefixes = Vec::new();
    if let Some(pkgs) = meta["packages"].as_array() {
        for p in pkgs {
            if let Some(name) = p["name"].as_str() {
                prefixes.push(format!("{}-", name.replace('-', "_")));
                prefixes.push(format!("{name}-"));
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
    let mut bad = BTreeSet::new();
    for file in dep_files(&build, &prefixes) {
        let text = std::fs::read_to_string(&file).unwrap_or_default();
        let base = file.parent().map(Path::to_path_buf).unwrap_or_default();
        for dep in depinfo_paths(&file, &text) {
            let dep = if dep.is_absolute() {
                dep
            } else {
                base.join(dep)
            };
            let s = dep.to_string_lossy();
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
                None => {
                    bad.insert(s.into_owned());
                }
            }
        }
    }
    Ok(bad.into_iter().collect())
}
