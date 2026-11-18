//! Stamps the binary with the git trees it was built from (BACKLOG C3, SUPERSOTA R4a).
//!
//! 2026-10-05: the hook's `bin/goh` predated a step added to its source, with the same version
//! number, so the version check passed it and the step ran nowhere. A version says which release;
//! only the source says which steps. The stamp is the git object of every build input at HEAD --
//! `lib/goh_version.sh --inputs`: the four roots and each embedded file (GOH.STAMPINPUTS) -- then
//! the release field, as that script's `--stamp` prints it. `goh source-tree` prints it and
//! `gates/_goh_bin.sh` compares it to HEAD's before trusting the binary. Tree hashes are CONTENT addresses: a rebase, amend or reword that leaves the inputs
//! alone leaves the stamp alone.
//!
//! `GOH_SOURCE_STAMP` in the environment wins: `scripts/build-goh.sh` builds from an export of
//! HEAD, which has no `.git`, and passes the stamp it read there. Otherwise the stamp is read from
//! git here, and an uncommitted change to any input stamps `dirty` -- never trusted by the resolver.
//!
//! THE VERSION IS THE TAG (GOH.SHIPRELREUSE). `GOH_VERSION` is what `goh --version` prints and what
//! gohpin compares pins with: `GOH_VERSION` from the environment (build-goh.sh, from an export),
//! else `lib/goh_version.sh` over this checkout -- the one rule: `X.Y.Z` on a commit tagged vX.Y.Z,
//! `X.Y.Z-N-g<sha>` past it, `unknown` with no tag; `-dirty` when an input is uncommitted.
//! Cargo.toml's version is a placeholder nobody reads, so a release commit changes no build input.
//! A tagged commit's stamp ends in ` vX.Y.Z` (the same script), so its build is never the untagged
//! build of the same trees that the store already holds.
//!
//! AN INSTRUMENTED BUILD SAYS NO COMMIT (GOH.COVCARRY2). Under cargo-llvm-cov (`CARGO_LLVM_COV` set)
//! the version is `lib/goh_version.sh --coverage`: the release, or `X.Y.Z-coverage` past it. The
//! coverage step keys each test's profile on that binary's bytes, and `-N-g<sha>` moved them on a
//! commit that touched no build input (b851b948 -> d2454263), so no profile could ever be reused.

use std::path::{Path, PathBuf};
use std::process::Command;

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
        // `git status` refreshes the index under an OPTIONAL lock; a cargo build killed in that
        // window strands `index.lock` (GOH.GITKILL), so this reader takes none.
        .env("GIT_OPTIONAL_LOCKS", "0")
        .output()
        .ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).trim().to_owned())
}

/// The build inputs (`lib/goh_version.sh --inputs`): the four roots and every file a crate embeds
/// from outside `crates/` (GOH.STAMPINPUTS). Asked of the script, never listed here: a hand list of
/// four left out the files this binary `include_str!`s, and an edit to one alone kept the stamp.
fn inputs(root: &Path) -> Option<Vec<String>> {
    let listed = goh_version(root, &["--inputs"])?;
    let inputs: Vec<String> = listed.lines().map(str::to_owned).collect();
    (!inputs.is_empty()).then_some(inputs)
}

/// The stamp is the script's own (`--stamp`, its `goh_stamp_at`): one formula, read by the resolver,
/// build-goh.sh and this build alike. `dirty` while an input has an uncommitted edit.
fn stamp(root: &Path, inputs: Option<&[String]>) -> String {
    if let Some(given) = std::env::var("GOH_SOURCE_STAMP")
        .ok()
        .filter(|s| !s.is_empty())
    {
        return given;
    }
    let Some(inputs) = inputs else {
        return "unknown".to_owned();
    };
    let mut status = vec!["status", "--porcelain", "--untracked-files=normal", "--"];
    status.extend(inputs.iter().map(String::as_str));
    match git(root, &status) {
        None => return "unknown".to_owned(),
        Some(dirty) if !dirty.is_empty() => return "dirty".to_owned(),
        Some(_) => {}
    }
    goh_version(root, &["--stamp"])
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "unknown".to_owned())
}

/// `bash lib/goh_version.sh [ARGS] ROOT HEAD`: the one rule for goh's version (its header).
fn goh_version(root: &Path, args: &[&str]) -> Option<String> {
    let out = Command::new("bash")
        .arg(root.join("lib").join("goh_version.sh"))
        .args(args)
        .arg(root)
        .arg("HEAD")
        .env("GIT_OPTIONAL_LOCKS", "0")
        .output()
        .ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).trim().to_owned())
}

fn version(root: &Path, stamp: &str) -> String {
    if let Some(given) = std::env::var("GOH_VERSION").ok().filter(|s| !s.is_empty()) {
        return given;
    }
    let coverage = std::env::var_os("CARGO_LLVM_COV").is_some_and(|v| !v.is_empty());
    let args: &[&str] = if coverage { &["--coverage"] } else { &[] };
    let v = goh_version(root, args)
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| "unknown".to_owned());
    if stamp == "dirty" {
        format!("{v}-dirty")
    } else {
        v
    }
}

fn main() {
    let manifest = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap_or_default());
    let root = manifest.join("..").join("..");
    let inputs = inputs(&root);
    let stamp = stamp(&root, inputs.as_deref());
    println!("cargo:rustc-env=GOH_VERSION={}", version(&root, &stamp));
    println!("cargo:rustc-env=GOH_SOURCE_STAMP={stamp}");
    println!("cargo:rerun-if-env-changed=GOH_SOURCE_STAMP");
    println!("cargo:rerun-if-env-changed=GOH_VERSION");
    println!("cargo:rerun-if-env-changed=CARGO_LLVM_COV");
    println!(
        "cargo:rerun-if-changed={}",
        root.join("lib").join("goh_version.sh").display()
    );
    for input in inputs.iter().flatten() {
        println!("cargo:rerun-if-changed={}", root.join(input).display());
    }
    // A commit moves the stamp from `dirty` to HEAD's trees without touching a source file: the
    // reflog and the index are what change. A tag moves the version: `packed-refs` and the tag
    // directory (a new loose tag changes the directory).
    for path in ["logs/HEAD", "index", "packed-refs", "refs/tags"] {
        if let Some(p) = git(&root, &["rev-parse", "--git-path", path]) {
            let p = PathBuf::from(p);
            let p = if p.is_absolute() { p } else { root.join(p) };
            println!("cargo:rerun-if-changed={}", p.display());
        }
    }
}
