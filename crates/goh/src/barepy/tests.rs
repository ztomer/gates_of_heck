//! The shape table: every row a hook or gate line and whether it is a bare interpreter.

use super::{in_scope, scan_text};

fn names(src: &str) -> Vec<usize> {
    scan_text(src).into_iter().map(|(line, _)| line).collect()
}

#[test]
fn an_interpreter_at_command_position_is_a_finding() {
    for (src, why) in [
        ("python3 tools/x.py\n", "a line's first word"),
        ("  python tools/x.py\n", "indented, unversioned"),
        ("python3.14 tools/x.py\n", "a versioned name"),
        ("set -e; python3 x.py\n", "after ;"),
        ("a && python3 x.py\n", "after &&"),
        ("a | python3 -c 'print(1)'\n", "a pipeline's second command"),
        ("if python3 x.py; then :; fi\n", "an if condition"),
        ("exec python3 x.py\n", "after exec"),
        ("env python3 x.py\n", "through env"),
        (
            "/usr/bin/env FOO=1 python3 x.py\n",
            "through /usr/bin/env, after an assignment",
        ),
        ("FOO=1 BAR=2 python3 x.py\n", "after assignment prefixes"),
        (
            "v=\"$(python3 tools/push_scope.py)\"\n",
            "inside $( ) inside double quotes",
        ),
        ("x=`python3 y.py`\n", "inside backticks"),
        ("{ python3 a.py; }\n", "a brace group"),
        ("case $1 in a) python3 x.py ;; esac\n", "a case arm"),
    ] {
        assert_eq!(names(src), vec![1], "{why}: {src:?}");
    }
}

#[test]
fn a_path_an_argument_a_comment_or_a_string_is_not() {
    for (src, why) in [
        (".venv/bin/python x.py\n", "the venv, a path"),
        ("/usr/bin/python3 x.py\n", "an absolute path"),
        (
            "command -v python3 >/dev/null\n",
            "an argument to command -v",
        ),
        ("need python3 \"coverage\"\n", "an argument"),
        ("# python3 x.py\n", "a comment"),
        ("echo 'python3 x.py'\n", "a single-quoted string"),
        ("echo \"run python3 x.py\"\n", "a double-quoted string"),
        ("bash \"$GOH/gates/python.sh\" x.py\n", "the seam"),
        ("pythonista x\n", "a longer word"),
        ("cat <<E\npython3 x.py\nE\n", "a heredoc body"),
        ("GOH_PY=python3\n", "an assignment's value"),
    ] {
        assert!(names(src).is_empty(), "{why}: {src:?}");
    }
}

#[test]
fn the_line_is_the_authors() {
    assert_eq!(
        names("#!/usr/bin/env bash\nset -e\n\npython3 a.py\n"),
        vec![4]
    );
}

#[test]
fn the_scope_is_the_hooks_and_the_gate() {
    assert!(in_scope("tools/gate.sh"));
    assert!(in_scope(".githooks/pre-commit"));
    assert!(!in_scope(".githooks/.goh-installed/pre-commit.sha256"));
    assert!(!in_scope("tools/other.sh"));
    assert!(!in_scope("scripts/gate.sh"));
}

#[test]
fn the_stock_hooks_every_consumer_copies_run_no_bare_python() {
    for (name, src) in [
        ("commit-msg", include_str!("../../../../hooks/commit-msg")),
        (
            "post-rewrite",
            include_str!("../../../../hooks/post-rewrite"),
        ),
        (
            "pre-applypatch",
            include_str!("../../../../hooks/pre-applypatch"),
        ),
        ("pre-commit", include_str!("../../../../hooks/pre-commit")),
        ("pre-push", include_str!("../../../../hooks/pre-push")),
    ] {
        assert!(
            scan_text(src).is_empty(),
            "hooks/{name}: {:?}",
            scan_text(src)
        );
    }
}

/// Every shell source of goh's OWN under `dir`, recursively: `*.sh`, and an extensionless file
/// whose first line is a shell shebang (a hook).
fn goh_shell_sources(dir: &std::path::Path, out: &mut Vec<std::path::PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            goh_shell_sources(&path, out);
        } else if path.extension().is_some_and(|e| e == "sh")
            || (path.extension().is_none()
                && std::fs::read_to_string(&path).is_ok_and(|t| {
                    t.starts_with("#!") && t.lines().next().is_some_and(|l| l.contains("sh"))
                }))
        {
            out.push(path);
        }
    }
}

/// THE CLASS (G2d): goh's own scripts run the house interpreter, `"$GOH_PY"` (`lib/house_python.sh`),
/// never a bare `python3` -- PATH's first, which under a reduced PATH is macOS's 3.9.6. The suite's
/// gates ran there for months unseen, and a floor was set from it. One sweep, every goh script.
#[test]
fn goh_own_scripts_run_the_house_interpreter_never_a_bare_python() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let mut files = Vec::new();
    for dir in [
        "gates", "lib", "tools", "hooks", "scripts", "checks", "bin", "tui",
    ] {
        goh_shell_sources(&root.join(dir), &mut files);
    }
    files.push(root.join("install.sh"));
    assert!(
        files.len() > 40,
        "the sweep found only {} shell sources",
        files.len()
    );
    let mut findings = Vec::new();
    for path in &files {
        let Ok(text) = std::fs::read_to_string(path) else {
            continue;
        };
        for (line, name) in scan_text(&text) {
            findings.push(format!("{}:{line}: {name}", path.display()));
        }
    }
    assert!(
        findings.is_empty(),
        "bare python in goh's own scripts:\n{}",
        findings.join("\n")
    );
}

/// A fixture tree (no git: a full-scope read is the worktree) holding `files` as shell scripts.
fn tree(files: &[(&str, &str)]) -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap_or_else(|e| panic!("tempdir: {e}"));
    for (rel, body) in files {
        let path = dir.path().join(rel);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).unwrap_or_else(|e| panic!("{e}"));
        }
        std::fs::write(&path, body).unwrap_or_else(|e| panic!("{e}"));
    }
    dir
}

const SCRIPT: &str = "#!/bin/bash\npython3 tests/e2e/verify.py\n";

/// `GOH_BARE_PYTHON_SCOPE` (`ZeroThunder` OW.42, owner ruling 2026-10-31 "widen + fix"): a repo whose
/// local CI and build scripts run Python names them, and they are judged exactly as a hook is.
/// The default scope is unchanged without the key -- the same file outside it is not read.
#[test]
fn a_declared_scope_widens_what_is_judged() {
    let dir = tree(&[
        ("ci_local.sh", SCRIPT),
        ("tools/run.sh", SCRIPT),
        ("other.sh", SCRIPT),
    ]);
    let files: Vec<String> = ["ci_local.sh", "tools/run.sh", "other.sh"]
        .iter()
        .map(|s| (*s).to_owned())
        .collect();

    let (code, out, _) = super::judge(dir.path(), &files, "", "", false);
    assert_eq!(
        code, 0,
        "no key: none of these is a hook or the gate ({out})"
    );
    assert!(out.contains("0 tracked"), "no key reads nothing: {out}");

    let scope = r"^(ci_local\.sh|tools/[^/]*\.sh)$";
    let (code, _, err) = super::judge(dir.path(), &files, "", scope, false);
    assert_eq!(code, 1, "the declared scope is judged");
    assert!(err.contains("ci_local.sh:2: python3"), "{err}");
    assert!(err.contains("tools/run.sh:2: python3"), "{err}");
    assert!(
        !err.contains("other.sh"),
        "outside the declared scope: {err}"
    );
}

#[test]
fn the_declared_scope_still_yields_to_goh_exclude() {
    let dir = tree(&[("ci_local.sh", SCRIPT)]);
    let files = vec!["ci_local.sh".to_owned()];
    let (code, _, _) = super::judge(dir.path(), &files, "^ci_local", "^ci_local", false);
    assert_eq!(
        code, 0,
        "GOH_EXCLUDE wins over a scope that names the same file"
    );
}

/// A pattern that is not a regex must refuse the step, naming the KEY -- never read as "no
/// widening", which would be the silent narrowing this key exists to undo.
#[test]
fn a_bad_scope_regex_is_refused_naming_the_key() {
    let dir = tree(&[]);
    let (code, _, err) = super::judge(dir.path(), &[], "", "(", false);
    assert_eq!(code, 2, "{err}");
    assert!(err.contains("GOH_BARE_PYTHON_SCOPE"), "{err}");
}
