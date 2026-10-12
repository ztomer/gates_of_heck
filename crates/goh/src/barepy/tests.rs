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
