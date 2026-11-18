//! The repo top level, and the git dirs, read from git's own layout instead of a `git rev-parse`
//! spawn (GOH.RUSTROOT, GOH.RUSTREVPARSE, 2026-11-18).
//!
//! A land gate spawned that one question about 3,100 times (`tools/spawn_profile.py`), each a
//! process start for an answer the filesystem already holds. [`layout_toplevel`] answers it in
//! process for the layouts it models -- a `.git` directory, a `.git` file (a linked worktree, a
//! submodule) -- and returns `None` for everything else; [`toplevel`] then asks git, so an unusual
//! layout is git's answer, never a guess. Parity against git on every fixture shape is pinned in
//! the tests below.
//!
//! The same read answers `--absolute-git-dir`, `--path-format=absolute --git-common-dir`,
//! `--path-format=absolute --git-path index` and `--is-shallow-repository` (GOH.RUSTREVPARSE): each
//! had its own spawn in `proven`, `run_path`, `rust_depinfo`, `shapes::diff`, `index_view`,
//! `credurls` and `gitutil`, and each is a path the layout already names. [`is_shallow`] is certain
//! only for the common case (no `shallow` file, no `GIT_SHALLOW_FILE`); a shallow clone is git's.
//!
//! What makes it `None` (uncertain, so git answers): any discovery-steering variable set
//! (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`, `GIT_CEILING_DIRECTORIES`,
//! `GIT_DISCOVERY_ACROSS_FILESYSTEM`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_COUNT`); a walk that
//! crosses a filesystem; a bare repo or a directory inside a git dir; a `.git` that is not a valid
//! git dir; a config naming `worktree` (`core.worktree`; `extensions.worktreeConfig` is fine while no `config.worktree` exists), an include,
//! or `bare` other than false; a `config.worktree`; and a repo or work tree not owned by this user
//! (git's `safe.directory` refusal).

use std::ffi::OsString;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// Variables that steer git's discovery or config; any of them set and git decides.
const STEERING: [&str; 7] = [
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
];

/// The top level of the work tree holding `dir`: the layout's answer, else git's. `None` when `dir`
/// is in no work tree (what `git rev-parse --show-toplevel` fails on).
#[must_use]
pub fn toplevel(dir: &Path) -> Option<PathBuf> {
    layout_toplevel(dir).or_else(|| git_toplevel(dir))
}

/// `git -C dir rev-parse --show-toplevel`, the authority [`toplevel`] falls back to.
#[must_use]
pub fn git_toplevel(dir: &Path) -> Option<PathBuf> {
    git_path(dir, &["--show-toplevel"])
}

/// The one line `git rev-parse ARGS` prints in `dir`, as a path; `None` when git fails.
fn git_path(dir: &Path, args: &[&str]) -> Option<PathBuf> {
    let out = Command::new("git")
        .arg("rev-parse")
        .args(args)
        .current_dir(dir)
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    out.status.success().then(|| {
        PathBuf::from(
            String::from_utf8_lossy(&out.stdout)
                .trim_end_matches('\n')
                .to_owned(),
        )
    })
}

/// What the layout names for the repository holding a directory.
struct Layout {
    top: PathBuf,
    /// This work tree's git dir (`.git`, or `.git/worktrees/NAME`), canonical.
    gitdir: PathBuf,
    /// The repository's shared git dir, canonical.
    common: PathBuf,
}

/// The layout answer for `dir` with the real environment, or `None` where git must answer.
fn layout(dir: &Path) -> Option<Layout> {
    layout_with_env(dir, &|k| std::env::var_os(k))
}

/// The absolute git dir (`git rev-parse --absolute-git-dir`): the layout's, else git's.
#[must_use]
pub fn absolute_git_dir(dir: &Path) -> Option<PathBuf> {
    layout(dir)
        .map(|l| l.gitdir)
        .or_else(|| git_path(dir, &["--absolute-git-dir"]))
}

/// The common git dir (`git rev-parse --path-format=absolute --git-common-dir`): the layout's,
/// else git's. One per repository, whichever linked work tree `dir` is in.
#[must_use]
pub fn common_git_dir(dir: &Path) -> Option<PathBuf> {
    layout(dir)
        .map(|l| l.common)
        .or_else(|| git_path(dir, &["--path-format=absolute", "--git-common-dir"]))
}

/// The index file of the work tree holding `dir` (`git rev-parse --path-format=absolute --git-path
/// index`): the layout's, else git's. `GIT_INDEX_FILE` set is git's to answer.
#[must_use]
pub fn index_path(dir: &Path) -> Option<PathBuf> {
    let env = |k: &str| std::env::var_os(k);
    env("GIT_INDEX_FILE")
        .is_none()
        .then(|| layout_with_env(dir, &env))
        .flatten()
        .map(|l| l.gitdir.join("index"))
        .or_else(|| git_path(dir, &["--path-format=absolute", "--git-path", "index"]))
}

/// Is the clone holding `dir` shallow (`git rev-parse --is-shallow-repository`)? Certain `false`
/// when the layout is plain and has no `shallow` file; anything else is git's answer.
#[must_use]
pub fn is_shallow(dir: &Path) -> bool {
    let env = |k: &str| std::env::var_os(k);
    let plain = env("GIT_SHALLOW_FILE").is_none();
    if plain && layout_with_env(dir, &env).is_some_and(|l| !l.common.join("shallow").exists()) {
        return false;
    }
    git_path(dir, &["--is-shallow-repository"]).is_some_and(|p| p.as_os_str() == "true")
}

/// The top level read from the layout, or `None` where this cannot be certain.
#[must_use]
pub fn layout_toplevel(dir: &Path) -> Option<PathBuf> {
    layout_toplevel_with(dir, &|k| std::env::var_os(k))
}

/// [`layout_toplevel`] with the environment as a parameter (tests are hermetic about it).
#[must_use]
pub fn layout_toplevel_with(dir: &Path, env: &dyn Fn(&str) -> Option<OsString>) -> Option<PathBuf> {
    layout_with_env(dir, env).map(|l| l.top)
}

fn layout_with_env(dir: &Path, env: &dyn Fn(&str) -> Option<OsString>) -> Option<Layout> {
    if STEERING.iter().any(|k| env(k).is_some()) {
        return None;
    }
    let cwd = dir.canonicalize().ok()?;
    let dev = std::fs::metadata(&cwd).ok()?.dev();
    // A walk that crosses a filesystem ends there: git stops too, so the question is git's.
    let same_fs = |d: &&Path| std::fs::metadata(d).is_ok_and(|m| m.dev() == dev);
    for d in cwd.ancestors().take_while(same_fs) {
        let dotgit = d.join(".git");
        let gitdir = match std::fs::metadata(&dotgit) {
            Ok(m) if m.is_dir() => dotgit,
            Ok(m) if m.is_file() => {
                let text = std::fs::read_to_string(&dotgit).ok()?;
                let line = text.lines().next()?;
                d.join(line.strip_prefix("gitdir: ")?)
            }
            Err(_) if !(d.join("HEAD").is_file() && d.join("objects").is_dir()) => continue,
            // a bare repo, the inside of a git dir, or a `.git` that is neither file nor directory
            _ => return None,
        };
        return certain(d, &gitdir);
    }
    None
}

/// The layout when `gitdir` is a plain, ordinary git dir of the work tree `top`, owned by us.
fn certain(top: &Path, gitdir: &Path) -> Option<Layout> {
    gitdir.join("HEAD").is_file().then_some(())?;
    let link = gitdir.join("commondir");
    let common = if link.exists() {
        gitdir.join(std::fs::read_to_string(link).ok()?.trim_end())
    } else {
        gitdir.to_path_buf()
    };
    let me = goh_sys::euid();
    let ours = [top, gitdir, &common]
        .iter()
        .all(|p| std::fs::metadata(p).is_ok_and(|m| m.uid() == me));
    (common.join("objects").is_dir()
        && !gitdir.join("config.worktree").exists()
        && plain_config(&common.join("config"))
        && ours)
        .then_some(())?;
    Some(Layout {
        top: top.to_path_buf(),
        gitdir: gitdir.canonicalize().ok()?,
        common: common.canonicalize().ok()?,
    })
}

/// A config that cannot move the work tree: no `worktree` word anywhere, no include, `bare` false.
fn plain_config(path: &Path) -> bool {
    std::fs::read_to_string(path).is_ok_and(|text| plain_text(&text))
}

fn plain_text(text: &str) -> bool {
    // `extensions.worktreeConfig` only makes git read a `config.worktree`, which `certain` refuses
    // to find; this repository's own shared config sets it, and every lane runs in a worktree of it.
    let lower = text.to_ascii_lowercase().replace("worktreeconfig", "");
    if lower.contains("worktree") || lower.contains("include") {
        return false;
    }
    lower.lines().all(|line| {
        let line = line.trim();
        match line.strip_prefix("bare") {
            Some(rest) if rest.trim_start().starts_with('=') || rest.trim().is_empty() => rest
                .trim_start()
                .strip_prefix('=')
                .is_some_and(|v| v.trim() == "false"),
            _ => true,
        }
    })
}

#[cfg(test)]
mod tests;
