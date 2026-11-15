//! The ONE place goh's temp, lock and staging paths come from, from Rust.
//!
//! GOH.CLASS.CHECKOUT part 2. The shell face is `lib/run_path.sh`, whose header tells the class;
//! the Python face is `lib/run_path.py`. One root and one key in all three:
//!
//! * `root()` -- `$GOH_RUN_DIR`, else `$XDG_STATE_HOME/goh/run`, else `~/.local/state/goh/run`;
//!   a relative `GOH_RUN_DIR` is refused (`None`), never joined to the cwd (`crate::cache_root`).
//! * `dir_for(tree)` -- `<root>/<basename>-<sha256(physical tree)[:16]>`, created.
//! * `index_tree(repo)` -- the tree of the index git would commit, written from a private copy
//!   under the root, so the shared `index.lock` is never taken (GOH.REFRESHINDEXLOCK).

use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use sha2::{Digest, Sha256};

/// The root every run path lives under, or `None` when `GOH_RUN_DIR` is relative.
#[must_use]
pub fn root() -> Option<PathBuf> {
    root_from(
        std::env::var_os("GOH_RUN_DIR").as_deref(),
        std::env::var_os("XDG_STATE_HOME").as_deref(),
        std::env::var_os("HOME").as_deref(),
    )
}

fn root_from(
    named: Option<&std::ffi::OsStr>,
    state: Option<&std::ffi::OsStr>,
    home: Option<&std::ffi::OsStr>,
) -> Option<PathBuf> {
    if let Some(n) = named.filter(|n| !n.is_empty()) {
        let p = PathBuf::from(n);
        return p.is_absolute().then_some(p);
    }
    let base = match state.map(PathBuf::from).filter(|s| s.is_absolute()) {
        Some(s) => s,
        None => PathBuf::from(home?).join(".local").join("state"),
    };
    Some(base.join("goh").join("run"))
}

/// The directory name of `phys` under the root: its basename and 16 hex of its path's SHA-256.
#[must_use]
pub fn key(phys: &Path) -> String {
    let mut hexd = String::with_capacity(16);
    for b in Sha256::digest(phys.as_os_str().as_encoded_bytes())
        .iter()
        .take(8)
    {
        let _ = write!(hexd, "{b:02x}");
    }
    let base = phys
        .file_name()
        .map_or_else(String::new, |n| n.to_string_lossy().into_owned());
    format!("{base}-{hexd}")
}

/// `tree`'s own directory under the root, created; `None` when the tree or the root is unusable.
#[must_use]
pub fn dir_for(tree: &Path) -> Option<PathBuf> {
    let phys = tree.canonicalize().ok()?;
    let d = root()?.join(key(&phys));
    std::fs::create_dir_all(&d).ok()?;
    Some(d)
}

/// The tree of the index git would commit in `repo` (the cwd when `None`): `GIT_INDEX_FILE` when
/// set, else the repository's own index, copied under the root and written from the copy.
#[must_use]
pub fn index_tree(repo: Option<&Path>) -> Option<String> {
    let git = |args: &[&str], index: Option<&Path>| {
        let mut c = Command::new("git");
        if let Some(r) = repo {
            c.current_dir(r);
        }
        if let Some(i) = index {
            c.env("GIT_INDEX_FILE", i);
        }
        let out = c
            .args(args)
            .stdin(Stdio::null())
            .stderr(Stdio::null())
            .output()
            .ok()?;
        out.status
            .success()
            .then(|| String::from_utf8_lossy(&out.stdout).trim().to_owned())
    };
    let own = std::env::var_os("GIT_INDEX_FILE").filter(|i| !i.is_empty());
    let carried = || {
        let here = git(&["rev-parse", "--absolute-git-dir"], None)?;
        carried_index(
            std::env::var_os("GOH_HOOK_INDEX_FILE").as_deref(),
            std::env::var_os("GOH_HOOK_GIT_DIR").as_deref(),
            Path::new(&here),
        )
    };
    let index = match own.map(PathBuf::from).or_else(carried) {
        Some(i) => repo.map_or_else(|| i.clone(), |r| r.join(&i)),
        None => PathBuf::from(git(
            &["rev-parse", "--path-format=absolute", "--git-path", "index"],
            None,
        )?),
    };
    let where_ = root()?.join("index");
    std::fs::create_dir_all(&where_).ok()?;
    let copy = where_.join(format!("tree.{}.{}", std::process::id(), nanos()));
    if index.is_file() && std::fs::copy(&index, &copy).is_err() {
        return None;
    }
    let tree = git(&["write-tree"], Some(&copy));
    let _ = std::fs::remove_file(&copy);
    tree.filter(|t| !t.is_empty())
}

/// The index an outer commit hook carried (`gates/_git_env.sh` `goh_hook_unbind`), when the
/// repository at `here` is the one it belongs to: `commit -a` and `commit -- path` commit a
/// TEMPORARY index that only `GOH_HOOK_INDEX_FILE` names once the hook has dropped its git variables.
fn carried_index(
    index: Option<&std::ffi::OsStr>,
    git_dir: Option<&std::ffi::OsStr>,
    here: &Path,
) -> Option<PathBuf> {
    let (index, git_dir) = (index.filter(|i| !i.is_empty())?, git_dir?);
    let same = |a: &Path, b: &Path| {
        a.canonicalize()
            .ok()
            .zip(b.canonicalize().ok())
            .is_some_and(|(a, b)| a == b)
    };
    same(Path::new(git_dir), here).then(|| PathBuf::from(index))
}

fn nanos() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_nanos())
}

#[cfg(test)]
mod tests {
    use super::{carried_index, key, root_from};
    use std::ffi::OsStr;
    use std::path::{Path, PathBuf};

    #[test]
    fn the_root_follows_its_knobs_and_refuses_a_relative_one() {
        let home = Some(OsStr::new("/home/u"));
        assert_eq!(
            root_from(None, None, home),
            Some(PathBuf::from("/home/u/.local/state/goh/run"))
        );
        assert_eq!(
            root_from(None, Some(OsStr::new("/s")), home),
            Some(PathBuf::from("/s/goh/run"))
        );
        assert_eq!(
            root_from(None, Some(OsStr::new("rel")), home),
            Some(PathBuf::from("/home/u/.local/state/goh/run"))
        );
        assert_eq!(
            root_from(Some(OsStr::new("/r")), None, home),
            Some(PathBuf::from("/r"))
        );
        assert_eq!(root_from(Some(OsStr::new("r")), None, home), None);
    }

    #[test]
    fn the_key_is_the_basename_and_sixteen_hex_of_the_path() {
        // `printf %s /a/repo | sha256sum`: the vector lib/run_path.sh and lib/run_path.py are
        // pinned to as well (tests/test_run_path.py), so the three faces name one directory.
        assert_eq!(key(Path::new("/a/repo")), "repo-668405c985a3f02f");
        assert_ne!(key(Path::new("/a/repo")), key(Path::new("/b/repo")));
    }

    #[test]
    fn a_carried_index_is_taken_only_for_the_repository_it_belongs_to() {
        let dir = std::env::temp_dir();
        let (idx, gd) = (Some(OsStr::new("/t/index.lock")), Some(dir.as_os_str()));
        assert_eq!(
            carried_index(idx, gd, &dir),
            Some(PathBuf::from("/t/index.lock"))
        );
        assert_eq!(carried_index(idx, gd, Path::new("/nonexistent")), None);
        assert_eq!(carried_index(None, gd, &dir), None);
        assert_eq!(carried_index(Some(OsStr::new("")), gd, &dir), None);
        assert_eq!(carried_index(idx, None, &dir), None);
    }
}
