//! The INDEX as a directory — what a whole-tree checker must read at
//! pre-commit scope. Port of `goh_index_view` in `gates/_common.sh`.
//!
//! A checker handed the working tree at `--staged` judges files the commit
//! does not contain, and fails both ways: an unstaged edit elsewhere blocks
//! a clean commit, and a staged violation passes because its fix is merely
//! unstaged (`~/.claude/skills`, 2026-09-23: concurrent sessions' unstaged
//! edits refused a commit of three clean skills). The export is scoped to
//! the subtree asked for and removed on drop. A directory OUTSIDE the repo
//! is no part of the commit at all, so `--staged` skips it by name.
//!
//! The export's root carries a `.git` FILE pointing at the repo's git dir,
//! so git run INSIDE the view answers from the same index: `rev-parse
//! --show-toplevel` is the view, `ls-files` lists index entries and nothing
//! untracked, `show :path` agrees with the bytes on disk. Code that finds its
//! repo from a path (the ceiling and ratchet checks) is correct in the view
//! unchanged. Two hook-environment facts make that hold: a plain `git
//! commit` hands its hook `GIT_INDEX_FILE=.git/index`, RELATIVE, which inside
//! the view names a path under a file (it and `GIT_DIR` are made absolute
//! for the whole process, a no-op everywhere else); and `GIT_WORK_TREE`
//! outranks the `.git` file, so it is refused with the reason, never obeyed.

use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// A directory to hand a checker: an index export, or the original path.
#[derive(Debug)]
pub struct IndexView {
    root: PathBuf,
    snapshot: Option<PathBuf>,
}

impl IndexView {
    /// The directory the checker should read.
    #[must_use]
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// [`Self::root`] as a checker argument.
    #[must_use]
    pub fn root_arg(&self) -> String {
        self.root.to_string_lossy().into_owned()
    }

    /// `dir` as a checker must read it at this scope: the index export at
    /// `--staged`, the path itself otherwise. `Err` carries the step's
    /// outcome, already reported: `None` when `dir` lies outside `repo` at
    /// `--staged` (no part of this commit, so a named skip), `Some(2)` when
    /// the export failed (refused, never the working tree in its place).
    ///
    /// # Errors
    /// As above; the caller returns the carried outcome.
    pub fn at_scope(repo: &Path, dir: &str, staged: bool) -> Result<Self, Option<i32>> {
        if !staged {
            return Ok(Self {
                root: PathBuf::from(dir),
                snapshot: None,
            });
        }
        match Self::of(repo, Path::new(dir)) {
            Ok(Some(view)) => Ok(view),
            Ok(None) => {
                eprintln!("⚠ {dir} is outside this repo, so no part of this commit — NOT checked at --staged (--full reads it)");
                Err(None)
            }
            Err(message) => {
                eprintln!("✗ structural: {message} — refusing to check the working tree in the index's place");
                Err(Some(2))
            }
        }
    }

    /// The whole repo as a checker must read it at this scope (see
    /// [`Self::at_scope`]). `None` means the export was refused and
    /// reported; the caller fails the step.
    #[must_use]
    pub fn repo(repo: &Path, staged: bool) -> Option<Self> {
        Self::at_scope(repo, &repo.to_string_lossy(), staged).ok()
    }

    /// Export the index's copy of `dir`. `None` when `dir` lies outside
    /// `repo`: a commit there records nothing of it, so there is no index
    /// copy to read.
    ///
    /// # Errors
    /// A path cannot be resolved, or git failed to list or export the
    /// index. The caller must refuse rather than fall back to the working
    /// tree, which is the defect this type exists to remove.
    pub fn of(repo: &Path, dir: &Path) -> Result<Option<Self>, String> {
        pin_git_env()?;
        let top = repo
            .canonicalize()
            .map_err(|e| format!("cannot resolve {}: {e}", repo.display()))?;
        let phys = dir
            .canonicalize()
            .map_err(|e| format!("cannot resolve {}: {e}", dir.display()))?;
        let Ok(rel) = phys.strip_prefix(&top) else {
            return Ok(None);
        };
        let snapshot = make_temp_dir()?;
        let view = Self {
            root: snapshot.join(rel),
            snapshot: Some(snapshot.clone()),
        };
        export(&top, rel, &snapshot)?;
        let gitdir = git_dir(&top)?;
        std::fs::write(snapshot.join(".git"), format!("gitdir: {gitdir}\n"))
            .map_err(|e| format!("cannot write {}/.git: {e}", snapshot.display()))?;
        // A subtree with nothing staged is an EMPTY corpus, not a missing
        // one: the checker's own floor then says so instead of a path error.
        std::fs::create_dir_all(&view.root)
            .map_err(|e| format!("cannot create {}: {e}", view.root.display()))?;
        Ok(Some(view))
    }
}

impl Drop for IndexView {
    fn drop(&mut self) {
        if let Some(dir) = &self.snapshot {
            // Best effort: a leftover temp dir is litter, not a wrong verdict.
            let _ = std::fs::remove_dir_all(dir);
        }
    }
}

/// Refuse `GIT_WORK_TREE`, and make a relative `GIT_INDEX_FILE` or
/// `GIT_DIR` absolute against the cwd git set it from (the hook's).
fn pin_git_env() -> Result<(), String> {
    if let Some(tree) = std::env::var_os("GIT_WORK_TREE").filter(|v| !v.is_empty()) {
        return Err(format!(
            "GIT_WORK_TREE is set ({}) — it would override the index view, so --staged cannot judge the index. Unset it for the commit",
            Path::new(&tree).display()
        ));
    }
    let cwd = std::env::current_dir().map_err(|e| format!("cannot read the cwd: {e}"))?;
    for key in ["GIT_INDEX_FILE", "GIT_DIR"] {
        if let Some(value) = std::env::var_os(key).filter(|v| !v.is_empty()) {
            if Path::new(&value).is_relative() {
                std::env::set_var(key, cwd.join(value));
            }
        }
    }
    Ok(())
}

/// The repo's absolute git dir, for the view's `.git` file.
fn git_dir(top: &Path) -> Result<String, String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(top)
        .args(["rev-parse", "--absolute-git-dir"])
        .output()
        .map_err(|e| format!("git rev-parse failed to start: {e}"))?;
    let dir = String::from_utf8_lossy(&out.stdout).trim().to_owned();
    if out.status.success() && !dir.is_empty() {
        Ok(dir)
    } else {
        Err(format!(
            "cannot resolve the git dir of {}: {}",
            top.display(),
            String::from_utf8_lossy(&out.stderr).trim()
        ))
    }
}

/// `git ls-files -z -- <rel>` piped into `git checkout-index -z --stdin`.
fn export(top: &Path, rel: &Path, snapshot: &Path) -> Result<(), String> {
    let spec = if rel.as_os_str().is_empty() {
        Path::new(".")
    } else {
        rel
    };
    let listed = Command::new("git")
        .arg("-C")
        .arg(top)
        .args(["ls-files", "-z", "--"])
        .arg(spec)
        .output()
        .map_err(|e| format!("git ls-files failed to start: {e}"))?;
    if !listed.status.success() {
        return Err(format!(
            "git ls-files failed: {}",
            String::from_utf8_lossy(&listed.stderr).trim()
        ));
    }
    let mut prefix = snapshot.as_os_str().to_owned();
    prefix.push("/");
    let mut child = Command::new("git")
        .arg("-C")
        .arg(top)
        .args(["checkout-index", "-z", "--stdin"])
        .arg(format!("--prefix={}", Path::new(&prefix).display()))
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("git checkout-index failed to start: {e}"))?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin
            .write_all(&listed.stdout)
            .map_err(|e| format!("git checkout-index stdin: {e}"))?;
    }
    let done = child
        .wait_with_output()
        .map_err(|e| format!("git checkout-index: {e}"))?;
    if done.status.success() {
        Ok(())
    } else {
        Err(format!(
            "git checkout-index failed: {}",
            String::from_utf8_lossy(&done.stderr).trim()
        ))
    }
}

/// A fresh directory under `$TMPDIR`, named like the shell side's
/// `goh-index.XXXXXX`. `create_dir` fails on an existing path, so a name
/// collision retries instead of sharing a directory.
fn make_temp_dir() -> Result<PathBuf, String> {
    let base = std::env::temp_dir();
    let pid = std::process::id();
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.subsec_nanos());
    for attempt in 0..100_u32 {
        let dir = base.join(format!("goh-index.{pid}.{nanos}.{attempt}"));
        match std::fs::create_dir(&dir) {
            Ok(()) => return Ok(dir),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(e) => return Err(format!("cannot create {}: {e}", dir.display())),
        }
    }
    Err(format!("no free goh-index name under {}", base.display()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_dir_outside_the_repo_has_no_index_copy_and_is_skipped_at_staged() {
        let repo = tempfile::tempdir().unwrap();
        let other = tempfile::tempdir().unwrap();
        assert!(IndexView::of(repo.path(), other.path()).unwrap().is_none());
        let dir = other.path().to_string_lossy().into_owned();
        assert_eq!(
            IndexView::at_scope(repo.path(), &dir, true).unwrap_err(),
            None
        );
        let full = IndexView::at_scope(repo.path(), &dir, false).unwrap();
        assert_eq!(full.root(), other.path());
    }

    #[test]
    fn an_index_git_cannot_list_is_refused() {
        // Not a git repo: ls-files fails. The error must surface, and the
        // step's outcome is a refusal, never a read of the working tree.
        let repo = tempfile::tempdir().unwrap();
        let err = IndexView::of(repo.path(), repo.path()).unwrap_err();
        assert!(err.contains("git ls-files failed"), "{err}");
        let dir = repo.path().to_string_lossy().into_owned();
        assert_eq!(
            IndexView::at_scope(repo.path(), &dir, true).unwrap_err(),
            Some(2)
        );
        let full = IndexView::at_scope(repo.path(), &dir, false).unwrap();
        assert_eq!(full.root_arg(), dir);
    }

    #[test]
    fn an_unresolvable_path_is_an_error_not_a_fallback() {
        let repo = tempfile::tempdir().unwrap();
        let gone = repo.path().join("missing");
        assert!(IndexView::of(repo.path(), &gone).is_err());
        assert!(IndexView::of(&gone, repo.path()).is_err());
    }
}
