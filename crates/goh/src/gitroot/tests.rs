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

/// git's own `rev-parse ARGS` line in `dir`, steering variables cleared.
fn git_line(dir: &Path, args: &[&str]) -> Option<String> {
    let out = goh_testkit::git_command()
        .arg("rev-parse")
        .args(args)
        .current_dir(dir)
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).trim_end().to_owned())
}

/// The layout's dirs EQUAL git's four answers at `dir`, and are the layout's, not the fallback's.
fn dirs_certain_and_equal(dir: &Path) {
    let at = dir.display();
    let l = layout_with_env(dir, &no_env).unwrap_or_else(|| panic!("uncertain at {at}"));
    let said = |a: &[&str]| git_line(dir, a).map(PathBuf::from);
    assert_eq!(
        Some(l.gitdir.clone()),
        said(&["--absolute-git-dir"]),
        "git dir at {at}"
    );
    assert_eq!(
        Some(l.common),
        said(&["--path-format=absolute", "--git-common-dir"]),
        "common dir at {at}"
    );
    assert_eq!(
        Some(l.gitdir.join("index")),
        said(&["--path-format=absolute", "--git-path", "index"]),
        "index at {at}"
    );
}

/// The public answers (layout or fallback) equal git's at `dir`, whatever the shape.
fn publics_equal_git(dir: &Path) {
    let at = dir.display();
    let said = |a: &[&str]| git_line(dir, a).map(PathBuf::from);
    assert_eq!(
        absolute_git_dir(dir),
        said(&["--absolute-git-dir"]),
        "git dir at {at}"
    );
    assert_eq!(
        common_git_dir(dir),
        said(&["--path-format=absolute", "--git-common-dir"]),
        "common dir at {at}"
    );
    assert_eq!(
        index_path(dir),
        said(&["--path-format=absolute", "--git-path", "index"]),
        "index at {at}"
    );
    assert_eq!(
        is_shallow(dir),
        git_line(dir, &["--is-shallow-repository"]).as_deref() == Some("true"),
        "shallow at {at}"
    );
}

#[test]
fn the_git_dirs_of_a_plain_repo_and_its_subdirs_match_git() {
    let td = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
    let r = repo(td.path(), "r");
    for sub in ["", "a", "a/b"] {
        dirs_certain_and_equal(&r.join(sub));
        publics_equal_git(&r.join(sub));
    }
    let link = td.path().join("link");
    assert!(std::os::unix::fs::symlink(&r, &link).is_ok());
    dirs_certain_and_equal(&link.join("a"));
}

#[test]
fn the_git_dirs_of_a_linked_worktree_are_its_own_and_the_shared_one() {
    let td = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
    let r = repo(td.path(), "r");
    let wt = td.path().join("wt");
    git(
        &r,
        &["worktree", "add", "-q", "-b", "o", &wt.to_string_lossy()],
    );
    for p in [wt.clone(), wt.join("a/b")] {
        dirs_certain_and_equal(&p);
        publics_equal_git(&p);
    }
    let l = layout_with_env(&wt, &no_env).unwrap_or_else(|| panic!("uncertain"));
    assert_ne!(l.gitdir, l.common, "a linked worktree has its own git dir");
    assert_eq!(
        common_git_dir(&wt),
        common_git_dir(&r),
        "one common dir per repository"
    );
}

#[test]
fn the_git_dirs_of_a_submodule_match_git_or_ask_git() {
    let td = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
    let inner = repo(td.path(), "inner");
    let outer = repo(td.path(), "outer");
    git(
        &outer,
        &["submodule", "add", "-q", &inner.to_string_lossy(), "sub"],
    );
    for p in [outer.join("sub"), outer.join("sub/a/b"), outer.join("a")] {
        publics_equal_git(&p);
    }
    // its config names `core.worktree`, so the layout is git's to answer
    assert!(layout_with_env(&outer.join("sub"), &no_env).is_none());
}

#[test]
fn a_bare_repo_and_the_inside_of_a_git_dir_ask_git_for_the_dirs() {
    let td = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
    let r = repo(td.path(), "r");
    git(
        td.path(),
        &["clone", "-q", "--bare", &r.to_string_lossy(), "bare.git"],
    );
    let bare = td.path().join("bare.git");
    for p in [
        bare.clone(),
        bare.join("refs"),
        r.join(".git"),
        r.join(".git/refs"),
    ] {
        assert!(layout_with_env(&p, &no_env).is_none(), "at {}", p.display());
        publics_equal_git(&p);
    }
    let outside = td.path().join("none");
    assert!(std::fs::create_dir_all(&outside).is_ok());
    publics_equal_git(&outside);
    assert_eq!(absolute_git_dir(&outside), None);
}

#[test]
fn a_shallow_clone_is_shallow_and_so_is_its_worktree() {
    let td = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
    let r = repo(td.path(), "r");
    assert!(!is_shallow(&r), "a full clone is not shallow");
    git(&r, &["commit", "-q", "--allow-empty", "-m", "two"]);
    let url = format!("file://{}", r.display());
    git(td.path(), &["clone", "-q", "--depth", "1", &url, "s"]);
    let s = td.path().join("s");
    assert!(is_shallow(&s), "a depth-1 clone is shallow");
    publics_equal_git(&s);
    git(
        &s,
        &[
            "worktree",
            "add",
            "-q",
            "-b",
            "o",
            &td.path().join("swt").to_string_lossy(),
        ],
    );
    assert!(
        is_shallow(&td.path().join("swt")),
        "so is its linked worktree"
    );
    publics_equal_git(&td.path().join("swt"));
}

#[test]
fn a_config_that_can_move_the_work_tree_asks_git_for_the_dirs() {
    let td = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
    let r = repo(td.path(), "r");
    let moved = td.path().join("moved");
    assert!(std::fs::create_dir_all(&moved).is_ok());
    git(&r, &["config", "core.worktree", &moved.to_string_lossy()]);
    assert!(layout_with_env(&r.join("a"), &no_env).is_none());
    publics_equal_git(&r.join("a"));
}

#[test]
fn worktree_config_is_certain_until_a_config_worktree_exists() {
    let td = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
    let r = repo(td.path(), "r");
    git(&r, &["config", "core.repositoryformatversion", "1"]);
    git(&r, &["config", "extensions.worktreeConfig", "true"]);
    let wt = td.path().join("wt");
    git(
        &r,
        &["worktree", "add", "-q", "-b", "o", &wt.to_string_lossy()],
    );
    for p in [r.join("a"), wt.join("a")] {
        certain_and_equal(&p);
        dirs_certain_and_equal(&p);
    }
    // a per-worktree config can move the work tree: then git answers
    let moved = td.path().join("moved");
    assert!(std::fs::create_dir_all(&moved).is_ok());
    git(
        &wt,
        &[
            "config",
            "--worktree",
            "core.worktree",
            &moved.to_string_lossy(),
        ],
    );
    assert!(layout_with_env(&wt.join("a"), &no_env).is_none());
    publics_equal_git(&wt.join("a"));
    certain_and_equal(&r.join("a"));
}
