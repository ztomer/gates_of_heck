//! `goh rust-scope`: the INPUT SCOPE of one cargo workspace, for the proven cache (BACKLOG P3).
//!
//! The proven cache keyed every step on the WHOLE tree, so a README edit re-gated all 29 crates of
//! `media_server`. A crate's gate reads its own packages, the packages they reach by `path =`, and a
//! handful of config files; this names exactly those, as repo-relative paths whose git objects
//! `gates/_proven.sh` hashes into the key. Every way that could be wrong is checked, not assumed:
//!
//! * a source file that reaches OUTSIDE its crate at run time (`CARGO_MANIFEST_DIR` with `..`, or a
//!   `"../` string literal) makes the scope the whole tree (`.`) -- a test opening a fixture in a
//!   sibling directory is then keyed on everything;
//! * a path dependency outside the repository cannot be keyed by git at all: the command fails and
//!   the caller runs uncached;
//! * a config file that does not exist is still NAMED, so creating one (a new `clippy.toml`)
//!   changes the key;
//! * after a green run, `--check-depinfo` reads the compiler's own dep-info (`*.d`) and every build
//!   script's `rerun-if-changed`, and names any file the build read that lies outside the scope:
//!   the caller then records nothing. The cache checks itself.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::process::Command;

/// Config files cargo, clippy, rustfmt and the house gates read from a directory or its ancestors.
const CONFIG_NAMES: &[&str] = &[
    "Cargo.toml",
    "Cargo.lock",
    "rust-toolchain",
    "rust-toolchain.toml",
    ".cargo",
    "clippy.toml",
    ".clippy.toml",
    "rustfmt.toml",
    ".rustfmt.toml",
    "deny.toml",
    ".gatesrc",
];

fn run(dir: &Path, program: &str, args: &[&str]) -> Result<String, String> {
    let out = Command::new(program)
        .args(args)
        .current_dir(dir)
        .output()
        .map_err(|e| format!("{program}: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "{program} {} failed: {}",
            args.join(" "),
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

pub(crate) fn metadata(dir: &Path, no_deps: bool) -> Result<serde_json::Value, String> {
    let mut args = vec!["metadata", "--format-version", "1", "--locked"];
    if no_deps {
        args.push("--no-deps");
    }
    let text = run(dir, "cargo", &args)?;
    serde_json::from_str(&text).map_err(|e| format!("cargo metadata: {e}"))
}

pub(crate) fn repo_root(dir: &Path) -> Result<PathBuf, String> {
    let top = run(dir, "git", &["rev-parse", "--show-toplevel"])?;
    std::fs::canonicalize(top.trim()).map_err(|e| format!("repo root: {e}"))
}

pub(crate) fn rel(root: &Path, path: &Path) -> Option<String> {
    let path = std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    let r = path.strip_prefix(root).ok()?.to_string_lossy().into_owned();
    Some(if r.is_empty() { ".".to_owned() } else { r })
}

/// Package directories with no registry source: the workspace members and their `path =` deps.
fn local_packages(meta: &serde_json::Value) -> Vec<PathBuf> {
    meta["packages"]
        .as_array()
        .map(|pkgs| {
            pkgs.iter()
                .filter(|p| p["source"].is_null())
                .filter_map(|p| p["manifest_path"].as_str())
                .filter_map(|m| Path::new(m).parent().map(Path::to_path_buf))
                .collect()
        })
        .unwrap_or_default()
}

/// The canonical directories of the workspace's own members.
fn member_dirs(meta: &serde_json::Value) -> Vec<PathBuf> {
    let ids: Vec<&str> = meta["workspace_members"]
        .as_array()
        .map(|a| a.iter().filter_map(serde_json::Value::as_str).collect())
        .unwrap_or_default();
    meta["packages"]
        .as_array()
        .map(|pkgs| {
            pkgs.iter()
                .filter(|p| p["id"].as_str().is_some_and(|id| ids.contains(&id)))
                .filter_map(|p| p["manifest_path"].as_str())
                .filter_map(|m| Path::new(m).parent())
                .map(|d| std::fs::canonicalize(d).unwrap_or_else(|_| d.to_path_buf()))
                .collect()
        })
        .unwrap_or_default()
}

/// A file only a package's own test build compiles: under `tests/`, `benches/` or `examples/`, or
/// a `tests.rs` / `*_tests.rs` / `test_*.rs` module.
fn test_only(file: &Path, package: &Path) -> bool {
    let inner = file.strip_prefix(package).unwrap_or(file);
    let in_test_dir = inner.components().any(|c| {
        matches!(
            c.as_os_str().to_str(),
            Some("tests" | "benches" | "examples")
        )
    });
    let name = file
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or_default();
    in_test_dir || name == "tests.rs" || name.ends_with("_tests.rs") || name.starts_with("test_")
}

/// True when a source line reaches outside its crate.
fn escapes(line: &str) -> bool {
    (line.contains("CARGO_MANIFEST_DIR") && line.contains(".."))
        || line.contains("\"../")
        || line.contains("/../")
}

/// What an escaping line reaches, resolved to repo-relative paths -- or `None` when it cannot be
/// named (no literal to resolve, or a path leaving the repository), which keys on the whole tree.
///
/// Inside `include_str!` / `include_bytes!` / `include!` a literal is relative to the SOURCE FILE;
/// at run time (`cargo test` runs in the package directory) it is relative to the PACKAGE, and so is
/// one appended to `CARGO_MANIFEST_DIR` (`"/../x"`). Measured 2026-10-05: every `media_server` crate has
/// `include_str!("../../../VERSION")` in its tests, and keying all 29 on the whole tree for one
/// file made the per-crate cache worthless there.
fn reached(line: &str, source: &Path, package: &Path, root: &Path) -> Option<Vec<String>> {
    let all = string_literals(line);
    let literals: Vec<&str> = all
        .iter()
        .map(String::as_str)
        .filter(|s| s.contains("../") || *s == "..")
        .collect();
    if literals.is_empty() || literals.contains(&"..") {
        return None;
    }
    let compile_time = ["include_str!", "include_bytes!", "include!"]
        .iter()
        .any(|m| line.contains(m));
    let mut out = Vec::new();
    for lit in literals {
        let base = if compile_time && !lit.starts_with('/') {
            source.parent()?.to_path_buf()
        } else {
            package.to_path_buf()
        };
        let resolved = normalize(&base.join(lit.trim_start_matches('/')))?;
        out.push(rel(root, &resolved)?);
    }
    Some(out)
}

/// The contents of the `"..."` literals on a line, `\` escapes honoured (a shell script written
/// from a test has `\"` inside its literal; splitting on `"` read that as no path at all).
fn string_literals(line: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur: Option<String> = None;
    let mut chars = line.chars();
    while let Some(c) = chars.next() {
        match (&mut cur, c) {
            (None, '"') => cur = Some(String::new()),
            (Some(_), '"') => out.extend(cur.take()),
            (Some(s), '\\') => {
                if let Some(next) = chars.next() {
                    s.push(if next == 'n' { '\n' } else { next });
                }
            }
            (Some(s), other) => s.push(other),
            (None, _) => {}
        }
    }
    out
}

/// `a/b/../c` -> `a/c`, lexically; `None` if `..` climbs above the root of the path.
fn normalize(path: &Path) -> Option<PathBuf> {
    let mut out = PathBuf::new();
    for part in path.components() {
        match part {
            std::path::Component::ParentDir => {
                if !out.pop() {
                    return None;
                }
            }
            std::path::Component::CurDir => {}
            other => out.push(other),
        }
    }
    Some(out)
}

/// Every tracked `.rs` file under any of `dirs`, from ONE `git ls-files` (each file once): it was
/// a call per package -- 15 for `media_server`'s mediaops-rs and its path dependencies -- and what a
/// file contributes depends on its nearest package, never on which listing found it.
fn tracked_rs(root: &Path, dirs: &[&str]) -> Vec<PathBuf> {
    if dirs.is_empty() {
        return Vec::new();
    }
    let specs: Vec<String> = dirs
        .iter()
        .map(|d| {
            if *d == "." {
                "*.rs".to_owned()
            } else {
                format!("{d}/*.rs")
            }
        })
        .collect();
    let mut args = vec!["ls-files", "-z", "--"];
    args.extend(specs.iter().map(String::as_str));
    run(root, "git", &args)
        .map(|out| {
            out.split('\0')
                .filter(|s| !s.is_empty())
                .map(|s| root.join(s))
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect()
        })
        .unwrap_or_default()
}

/// The scope of the workspace at `cargo_dir`: repo-relative entries, or `["."]` for the tree.
///
/// # Errors
///
/// When the scope cannot be named: cargo or git failed, or a path dependency lies outside the repo.
pub fn scope(cargo_dir: &Path) -> Result<Vec<String>, String> {
    let root = repo_root(cargo_dir)?;
    let meta = metadata(cargo_dir, false)?;
    let mut entries = BTreeSet::new();
    let mut dirs = Vec::new();
    for pkg in local_packages(&meta) {
        let r = rel(&root, &pkg).ok_or_else(|| {
            format!(
                "{} is outside the repository: git cannot key it",
                pkg.display()
            )
        })?;
        dirs.push((pkg, r.clone()));
        entries.insert(r);
    }
    if let Some(ws) = meta["workspace_root"].as_str() {
        dirs.push((
            PathBuf::from(ws),
            rel(&root, Path::new(ws)).unwrap_or_default(),
        ));
    }
    // Config named at every directory from each package up to the repo root, present or not.
    for (dir, _) in &dirs {
        let mut at = std::fs::canonicalize(dir).unwrap_or_else(|_| dir.clone());
        loop {
            for name in CONFIG_NAMES {
                if let Some(r) = rel(&root, &at.join(name)) {
                    entries.insert(r);
                }
            }
            if at == root || !at.pop() || !at.starts_with(&root) {
                break;
            }
        }
    }
    let members = member_dirs(&meta);
    let package_count = dirs.len() - usize::from(meta["workspace_root"].is_string());
    let package_dirs: Vec<&str> = dirs
        .iter()
        .take(package_count)
        .map(|(_, r)| r.as_str())
        .collect();
    {
        for file in tracked_rs(&root, &package_dirs) {
            let text = std::fs::read_to_string(&file).unwrap_or_default();
            // The NEAREST enclosing package is the one `cargo test` runs a file's tests in.
            let package = dirs
                .iter()
                .map(|(d, _)| std::fs::canonicalize(d).unwrap_or_else(|_| d.clone()))
                .filter(|d| file.starts_with(d))
                .max_by_key(|d| d.components().count())
                .unwrap_or_else(|| {
                    // The listed package dir that holds it (the per-package loop's own dir).
                    package_dirs
                        .iter()
                        .map(|r| root.join(r))
                        .filter(|d| file.starts_with(d))
                        .max_by_key(|d| d.components().count())
                        .unwrap_or_else(|| root.clone())
                });
            // A path DEPENDENCY's own tests never run in this gate (it lints and covers only its
            // workspace), so their reads are not this crate's inputs; its compiled code's are.
            if !members.contains(&package) && test_only(&file, &package) {
                continue;
            }
            // Comments are prose about paths, not reads of them.
            let code = text.lines().filter(|l| !l.trim_start().starts_with("//"));
            for line in code.filter(|l| escapes(l)) {
                match reached(line, &file, &package, &root) {
                    Some(paths) => entries.extend(paths),
                    None => return Ok(vec![".".to_owned()]),
                }
            }
        }
    }
    Ok(prune_nested(entries))
}

/// Drop entries already covered by a directory entry above them.
fn prune_nested(entries: BTreeSet<String>) -> Vec<String> {
    let mut kept: Vec<String> = Vec::new();
    for e in entries {
        if e == "." {
            return vec![".".to_owned()];
        }
        if !kept.iter().any(|k| e.starts_with(&format!("{k}/"))) {
            kept.push(e);
        }
    }
    kept
}

/// `goh rust-scope`: print the scope, or check the last build's dep-info against a scope file.
/// Exit 0 printed / clean, 1 when dep-info names files outside the scope, 2 when it cannot answer.
pub fn run_command(cargo_dir: &std::path::Path, check: Option<&std::path::Path>) -> i32 {
    if let Some(file) = check {
        let entries: Vec<String> = std::fs::read_to_string(file)
            .unwrap_or_default()
            .lines()
            .map(str::to_owned)
            .filter(|l| !l.is_empty())
            .collect();
        return match crate::rust_depinfo::depinfo_escapes(cargo_dir, &entries) {
            Err(message) => {
                eprintln!("✗ [rust_scope] {message}");
                2
            }
            Ok(bad) if !bad.is_empty() => {
                for path in bad {
                    println!("{path}");
                }
                1
            }
            Ok(_) => 0,
        };
    }
    match scope(cargo_dir) {
        Err(message) => {
            eprintln!("✗ [rust_scope] {message}");
            2
        }
        Ok(entries) => {
            for e in entries {
                println!("{e}");
            }
            0
        }
    }
}

#[cfg(test)]
#[path = "rust_scope_tests.rs"]
mod tests;
