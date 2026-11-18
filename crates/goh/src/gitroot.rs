//! The repo top level, read from git's own layout instead of a `git rev-parse --show-toplevel`
//! spawn (GOH.RUSTROOT, 2026-11-18).
//!
//! A land gate spawned that one question about 3,100 times (`tools/spawn_profile.py`), each a
//! process start for an answer the filesystem already holds. [`layout_toplevel`] answers it in
//! process for the layouts it models -- a `.git` directory, a `.git` file (a linked worktree, a
//! submodule) -- and returns `None` for everything else; [`toplevel`] then asks git, so an unusual
//! layout is git's answer, never a guess. Parity against git on every fixture shape is pinned in
//! the tests below.
//!
//! What makes it `None` (uncertain, so git answers): any discovery-steering variable set
//! (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`, `GIT_CEILING_DIRECTORIES`,
//! `GIT_DISCOVERY_ACROSS_FILESYSTEM`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_COUNT`); a walk that
//! crosses a filesystem; a bare repo or a directory inside a git dir; a `.git` that is not a valid
//! git dir; a config naming `worktree` (`core.worktree`, `extensions.worktreeConfig`), an include,
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
    let out = Command::new("git")
        .args(["rev-parse", "--show-toplevel"])
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

/// The top level read from the layout, or `None` where this cannot be certain.
#[must_use]
pub fn layout_toplevel(dir: &Path) -> Option<PathBuf> {
    layout_toplevel_with(dir, &|k| std::env::var_os(k))
}

/// [`layout_toplevel`] with the environment as a parameter (tests are hermetic about it).
#[must_use]
pub fn layout_toplevel_with(dir: &Path, env: &dyn Fn(&str) -> Option<OsString>) -> Option<PathBuf> {
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
        return certain(d, &gitdir).map(|()| d.to_path_buf());
    }
    None
}

/// `Some` when `gitdir` is a plain, ordinary git dir of the work tree `top`, owned by us.
fn certain(top: &Path, gitdir: &Path) -> Option<()> {
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
        .then_some(())
}

/// A config that cannot move the work tree: no `worktree` word anywhere, no include, `bare` false.
fn plain_config(path: &Path) -> bool {
    std::fs::read_to_string(path).is_ok_and(|text| plain_text(&text))
}

fn plain_text(text: &str) -> bool {
    let lower = text.to_ascii_lowercase();
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
mod tests {
    use super::*;

    fn no_env(_: &str) -> Option<OsString> {
        None
    }

    fn git(dir: &Path, args: &[&str]) {
        let mut full = vec![
            "-c",
            "protocol.file.allow=always",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "init.defaultBranch=main",
        ];
        full.extend_from_slice(args);
        assert!(goh_testkit::git_in(dir, &full).is_ok(), "git {args:?}");
    }

    /// git's own answer with the steering variables cleared (the gate runs under a hook's).
    fn git_says(dir: &Path) -> Option<PathBuf> {
        let out = goh_testkit::git_command()
            .args(["rev-parse", "--show-toplevel"])
            .current_dir(dir)
            .stdin(Stdio::null())
            .stderr(Stdio::null())
            .output()
            .ok()?;
        out.status
            .success()
            .then(|| PathBuf::from(String::from_utf8_lossy(&out.stdout).trim_end_matches('\n')))
    }

    /// The layout answer EQUALS git's, and is not just the fallback's.
    fn certain_and_equal(dir: &Path) {
        let ours = layout_toplevel_with(dir, &no_env);
        assert!(ours.is_some(), "layout was uncertain at {}", dir.display());
        assert_eq!(ours, git_says(dir), "at {}", dir.display());
    }

    /// Whatever the layout says it is certain of is git's answer; `None` is allowed (git asks).
    fn never_wrong(dir: &Path) {
        if let Some(ours) = layout_toplevel_with(dir, &no_env) {
            assert_eq!(Some(ours), git_says(dir), "at {}", dir.display());
        }
    }

    fn repo(base: &Path, name: &str) -> PathBuf {
        let r = base.join(name);
        assert!(std::fs::create_dir_all(r.join("a/b")).is_ok());
        git(&r, &["init", "-q"]);
        assert!(std::fs::write(r.join("a/b/f.txt"), "x\n").is_ok());
        git(&r, &["add", "-A"]);
        git(&r, &["commit", "-q", "-m", "x"]);
        r.canonicalize().unwrap_or(r)
    }

    #[test]
    fn plain_repo_root_and_subdirs_match_git() {
        let td = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
        let r = repo(td.path(), "r");
        for sub in ["", "a", "a/b"] {
            certain_and_equal(&r.join(sub));
        }
    }

    #[test]
    fn a_symlinked_path_gives_the_physical_top_like_git() {
        let td = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
        let r = repo(td.path(), "r");
        let link = td.path().join("link");
        assert!(std::os::unix::fs::symlink(&r, &link).is_ok());
        certain_and_equal(&link.join("a/b"));
    }

    #[test]
    fn a_linked_worktree_matches_git() {
        let td = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
        let r = repo(td.path(), "r");
        let wt = td.path().join("wt");
        git(
            &r,
            &[
                "worktree",
                "add",
                "-q",
                "-b",
                "other",
                &wt.to_string_lossy(),
            ],
        );
        certain_and_equal(&wt);
        certain_and_equal(&wt.join("a/b"));
    }

    #[test]
    fn a_submodule_matches_git_or_asks_git() {
        let td = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
        let inner = repo(td.path(), "inner");
        let outer = repo(td.path(), "outer");
        git(
            &outer,
            &["submodule", "add", "-q", &inner.to_string_lossy(), "sub"],
        );
        for p in [outer.join("sub"), outer.join("sub/a/b"), outer.join("a")] {
            never_wrong(&p);
            assert_eq!(toplevel(&p), git_says(&p), "toplevel() at {}", p.display());
        }
        assert_eq!(
            toplevel(&outer.join("sub")),
            Some(outer.join("sub")),
            "a submodule is its own top level"
        );
    }

    #[test]
    fn a_bare_repo_and_the_inside_of_a_git_dir_are_git_s_to_answer() {
        let td = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
        let r = repo(td.path(), "r");
        let bare = td.path().join("bare.git");
        git(
            td.path(),
            &["clone", "-q", "--bare", &r.to_string_lossy(), "bare.git"],
        );
        for p in [
            bare.clone(),
            bare.join("refs"),
            r.join(".git"),
            r.join(".git/refs"),
        ] {
            assert_eq!(
                layout_toplevel_with(&p, &no_env),
                None,
                "at {}",
                p.display()
            );
            assert_eq!(toplevel(&p), git_says(&p), "toplevel() at {}", p.display());
        }
    }

    #[test]
    fn outside_any_repo_is_none_like_git() {
        let td = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
        let p = td.path().canonicalize().unwrap_or_else(|e| panic!("{e}"));
        assert_eq!(layout_toplevel_with(&p, &no_env), None);
        assert_eq!(toplevel(&p), git_says(&p));
    }

    #[test]
    fn an_empty_dot_git_is_not_a_repo_and_git_answers() {
        let td = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
        let r = repo(td.path(), "r");
        assert!(std::fs::create_dir_all(r.join("a/.git")).is_ok());
        assert_eq!(layout_toplevel_with(&r.join("a"), &no_env), None);
        assert_eq!(toplevel(&r.join("a")), git_says(&r.join("a")));
    }

    #[test]
    fn core_worktree_and_bare_true_configs_are_git_s_to_answer() {
        let td = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
        let r = repo(td.path(), "r");
        let moved = td.path().join("moved");
        assert!(std::fs::create_dir_all(&moved).is_ok());
        git(&r, &["config", "core.worktree", &moved.to_string_lossy()]);
        assert_eq!(layout_toplevel_with(&r.join("a"), &no_env), None);
        git(&r, &["config", "--unset", "core.worktree"]);
        certain_and_equal(&r.join("a"));
        git(&r, &["config", "core.bare", "true"]);
        assert_eq!(layout_toplevel_with(&r.join("a"), &no_env), None);
    }

    #[test]
    fn a_steering_variable_hands_the_question_to_git() {
        let td = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
        let r = repo(td.path(), "r");
        for var in STEERING {
            let env = |k: &str| (k == var).then(|| OsString::from("x"));
            assert_eq!(layout_toplevel_with(&r, &env), None, "{var}");
        }
    }

    #[test]
    fn a_filesystem_crossing_and_an_unreadable_dir_are_not_guessed() {
        assert_eq!(
            layout_toplevel_with(Path::new("/definitely/not/here"), &no_env),
            None
        );
    }
}
