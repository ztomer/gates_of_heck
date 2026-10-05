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

fn metadata(dir: &Path, no_deps: bool) -> Result<serde_json::Value, String> {
    let mut args = vec!["metadata", "--format-version", "1", "--locked"];
    if no_deps {
        args.push("--no-deps");
    }
    let text = run(dir, "cargo", &args)?;
    serde_json::from_str(&text).map_err(|e| format!("cargo metadata: {e}"))
}

fn repo_root(dir: &Path) -> Result<PathBuf, String> {
    let top = run(dir, "git", &["rev-parse", "--show-toplevel"])?;
    std::fs::canonicalize(top.trim()).map_err(|e| format!("repo root: {e}"))
}

fn rel(root: &Path, path: &Path) -> Option<String> {
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

/// True when a source line reaches outside its crate at run time.
fn escapes(line: &str) -> bool {
    (line.contains("CARGO_MANIFEST_DIR") && line.contains(".."))
        || line.contains("\"../")
        || line.contains("/../")
}

fn tracked_rs(root: &Path, dir: &str) -> Vec<PathBuf> {
    let spec = if dir == "." {
        "*.rs".to_owned()
    } else {
        format!("{dir}/*.rs")
    };
    run(root, "git", &["ls-files", "-z", "--", &spec])
        .map(|out| {
            out.split('\0')
                .filter(|s| !s.is_empty())
                .map(|s| root.join(s))
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
    for (_, r) in &dirs {
        for file in tracked_rs(&root, r) {
            let text = std::fs::read_to_string(&file).unwrap_or_default();
            if text.lines().any(escapes) {
                return Ok(vec![".".to_owned()]);
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

fn covered(entry_set: &[String], rel_path: &str) -> bool {
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

fn depinfo_paths(file: &Path, text: &str) -> Vec<PathBuf> {
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
        return match depinfo_escapes(cargo_dir, &entries) {
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
mod tests {
    use super::{covered, depinfo_paths, escapes, prune_nested};
    use std::collections::BTreeSet;
    use std::path::{Path, PathBuf};

    #[test]
    fn a_nested_entry_is_covered_by_its_parent() {
        let set: BTreeSet<String> = ["crates/a", "crates/a/Cargo.toml", "crates/b"]
            .into_iter()
            .map(str::to_owned)
            .collect();
        assert_eq!(prune_nested(set), vec!["crates/a", "crates/b"]);
    }

    #[test]
    fn coverage_is_by_path_segment_not_by_prefix() {
        let set = vec!["crates/a".to_owned()];
        assert!(covered(&set, "crates/a/src/lib.rs"));
        assert!(!covered(&set, "crates/ab/src/lib.rs"));
    }

    #[test]
    fn a_runtime_reach_outside_the_crate_is_seen() {
        assert!(escapes(
            r#"let p = concat!(env!("CARGO_MANIFEST_DIR"), "/../x");"#
        ));
        assert!(escapes(r#"std::fs::read("../fixtures/a.json")"#));
        assert!(!escapes("let x = a..b;"));
    }

    #[test]
    fn dep_info_with_escaped_spaces_parses() {
        let text = "/b/deps/a-1.d: /r/src/lib.rs /r/my\\ dir/x.txt\n\n/r/src/lib.rs:\n";
        assert_eq!(
            depinfo_paths(Path::new("/b/deps/a-1.d"), text),
            vec![
                PathBuf::from("/r/src/lib.rs"),
                PathBuf::from("/r/my dir/x.txt")
            ]
        );
    }
}
