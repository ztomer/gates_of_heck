//! Stamps the binary with the git trees it was built from (BACKLOG C3, SUPERSOTA R4a).
//!
//! 2026-10-05: the hook's `bin/goh` predated a step added to its source, with the same version
//! number, so the version check passed it and the step ran nowhere. A version says which release;
//! only the source says which steps. The stamp is the git tree hash of every build input at HEAD
//! (`goh source-tree` prints it) and `gates/_goh_bin.sh` compares it to HEAD's before trusting the
//! binary. Tree hashes are CONTENT addresses: a rebase, amend or reword that leaves the inputs
//! alone leaves the stamp alone.
//!
//! `GOH_SOURCE_STAMP` in the environment wins: `scripts/build-goh.sh` builds from an export of
//! HEAD, which has no `.git`, and passes the stamp it read there. Otherwise the stamp is read from
//! git here, and an uncommitted change to any input stamps `dirty` -- never trusted by the resolver.

use std::path::{Path, PathBuf};
use std::process::Command;

const INPUTS: [&str; 4] = ["crates", "Cargo.toml", "Cargo.lock", "rust-toolchain.toml"];

fn git(root: &Path, args: &[&str]) -> Option<String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        // A build run from inside a git hook inherits the hook's repository; ask about THIS one.
        .env_remove("GIT_DIR")
        .env_remove("GIT_INDEX_FILE")
        .env_remove("GIT_WORK_TREE")
        .env_remove("GIT_PREFIX")
        .env_remove("GIT_COMMON_DIR")
        .env_remove("GIT_OBJECT_DIRECTORY")
        .output()
        .ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).trim().to_owned())
}

fn stamp(root: &Path) -> String {
    if let Some(given) = std::env::var("GOH_SOURCE_STAMP")
        .ok()
        .filter(|s| !s.is_empty())
    {
        return given;
    }
    let mut status = vec!["status", "--porcelain", "--untracked-files=normal", "--"];
    status.extend(INPUTS);
    match git(root, &status) {
        None => return "unknown".to_owned(),
        Some(dirty) if !dirty.is_empty() => return "dirty".to_owned(),
        Some(_) => {}
    }
    let specs: Vec<String> = INPUTS.iter().map(|i| format!("HEAD:{i}")).collect();
    let mut args = vec!["rev-parse"];
    args.extend(specs.iter().map(String::as_str));
    git(root, &args).map_or_else(|| "unknown".to_owned(), |s| s.replace('\n', " "))
}

fn main() {
    let manifest = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap_or_default());
    let root = manifest.join("..").join("..");
    println!("cargo:rustc-env=GOH_SOURCE_STAMP={}", stamp(&root));
    println!("cargo:rerun-if-env-changed=GOH_SOURCE_STAMP");
    for input in INPUTS {
        println!("cargo:rerun-if-changed={}", root.join(input).display());
    }
    // A commit moves the stamp from `dirty` to HEAD's trees without touching a source file: the
    // reflog and the index are what change.
    for path in ["logs/HEAD", "index"] {
        if let Some(p) = git(&root, &["rev-parse", "--git-path", path]) {
            let p = PathBuf::from(p);
            let p = if p.is_absolute() { p } else { root.join(p) };
            println!("cargo:rerun-if-changed={}", p.display());
        }
    }
}
