//! A hook's `GIT_DIR` must never reach a fixture's git (zinc, 2026-09-27).
//!
//! Under a LINKED worktree a hook's `GIT_DIR` is `<main>/.git/worktrees/<name>`;
//! a fixture `git init <tmp>` that inherits it re-initialises the REAL repo
//! and, with `extensions.worktreeConfig` on, writes `core.bare = true` into
//! the shared config. The parent test builds that shape, proves the raw
//! inheritance flips it (calibration), then re-runs THIS test binary as a
//! child with the hook's variables in its real environment - inherited, the
//! way a hook passes them - and has the child build a fixture through
//! `repo_with`. The scratch main must come through untouched.

use std::path::Path;
use std::process::Command;

use goh_testkit::{git_command, git_in, local_env_vars, repo_with};

const CHILD: &str = "GOH_HOOK_ENV_CHILD";

/// `core.bare` as the SHARED config holds it; empty when unset or unreadable
/// (the callers compare against "true"/"false", so neither passes by accident).
fn core_bare(main: &Path) -> String {
    git_command()
        .args(["config", "--file"])
        .arg(main.join(".git/config"))
        .arg("core.bare")
        .output()
        .map_or_else(
            |_| String::new(),
            |o| String::from_utf8_lossy(&o.stdout).trim().to_owned(),
        )
}

/// Runs only as the child: builds a fixture with the hook's variables inherited.
#[test]
fn child_builds_a_fixture_under_a_hook_env() {
    if std::env::var_os(CHILD).is_none() {
        return;
    }
    let r = repo_with(&[("a.txt", "x\n")]).expect("fixture");
    assert!(
        r.path().join(".git").is_dir(),
        "the fixture is its own repo"
    );
}

#[test]
fn git_knows_the_repository_variables() {
    let vars = local_env_vars();
    for v in [
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
    ] {
        assert!(vars.iter().any(|x| x == v), "{v} missing from {vars:?}");
    }
}

#[test]
fn fixture_git_never_reaches_the_hooks_repository() {
    let td = tempfile::tempdir().expect("tempdir");
    let main = td.path().join("main");
    std::fs::create_dir_all(&main).expect("mkdir");
    for args in [
        &["init", "-q"][..],
        &[
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ],
        &["worktree", "add", "-q", "../wt"],
        &["config", "extensions.worktreeConfig", "true"],
    ] {
        git_in(&main, args).expect("scratch main + linked worktree");
    }
    let gitdir = main
        .join(".git/worktrees/wt")
        .canonicalize()
        .expect("worktree gitdir");
    assert_ne!(core_bare(&main), "true");

    // Calibration: a git that DOES carry the hook's GIT_DIR flips it.
    let st = git_command()
        .env("GIT_DIR", &gitdir)
        .args(["init", "-q"])
        .arg(td.path().join("other"))
        .output()
        .expect("git init runs");
    assert!(st.status.success());
    assert_eq!(core_bare(&main), "true", "the hazard did not reproduce");
    git_in(
        &main,
        &["config", "--file", ".git/config", "core.bare", "false"],
    )
    .expect("reset");

    // The child: this binary, the hook's variables in its real environment.
    let out = Command::new(std::env::current_exe().expect("test binary"))
        .args([
            "--exact",
            "child_builds_a_fixture_under_a_hook_env",
            "--test-threads=1",
        ])
        .env(CHILD, "1")
        .env("GIT_DIR", &gitdir)
        .env("GIT_INDEX_FILE", gitdir.join("index"))
        .output()
        .expect("child runs");
    let text = format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(out.status.success(), "{text}");
    assert!(
        text.contains("1 passed"),
        "the child test did not run: {text}"
    );
    assert_eq!(
        core_bare(&main),
        "false",
        "a fixture re-initialised the hook's repo"
    );
}
