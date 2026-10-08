//! The check's specification as a table: every dead shape RED on the line named, every live
//! shape GREEN.
//!
//! The first row is the incident as it was written (`app_updates`' tools/gate.sh, fixed in 5a4b33c):
//! the second `exec` never ran, so the full gate never judged ROADMAP.md. shellcheck 0.11.0 exits
//! 0 on it, and on the top-level `exec echo a; echo b` beside it -- measured, which is why this
//! check exists.

use super::{scan_text, Finding};

/// `(script, 1-based line of the dead statement, the exec's line)`.
const RED: &[(&str, usize, usize)] = &[
    // The incident, in its case arm.
    (
        "case \"$1\" in\n  --full)\n    exec python3 tools/check_roadmap.py --self-test\n    exec python3 tools/check_roadmap.py\n    ;;\nesac\n",
        4,
        3,
    ),
    // Top level, one line and two.
    ("exec echo a; echo b\n", 1, 1),
    ("#!/bin/sh\nexec echo a\necho b\n", 3, 2),
    // Blank lines and comments between do not hide it.
    ("exec foo\n\n# why\n   \nbar\n", 5, 1),
    // Every kind of following statement is code.
    ("exec foo\nif x; then y; fi\n", 2, 1),
    ("exec foo\n( bar )\n", 2, 1),
    ("exec foo\n(\n  bar\n)\n", 2, 1),
    ("exec foo\nf() { :; }\n", 2, 1),
    ("exec foo\nV=1\n", 2, 1),
    ("exec foo\nbar &\n", 2, 1),
    ("exec foo\nbar | baz\n", 2, 1),
    ("exec foo\nexit 1 2\n", 2, 1),
    // Inside a block: the block's own next statement.
    ("if a; then\n  exec foo\n  bar\nfi\n", 3, 2),
    ("main() {\n  exec foo \"$@\"\n  echo never\n}\n", 3, 2),
    ("while :; do\n  exec foo\n  bar\ndone\n", 3, 2),
    ("x) exec foo; bar ;;\n", 1, 1),
    // Inside a subshell, the rest of the SUBSHELL is dead.
    ("( exec foo\n  bar )\n", 2, 1),
    // The exec's own form: a line continuation, options, a quoted program, a redirect beside a
    // command, `"$@"`, a prefix keyword.
    ("exec foo \\\n  --bar \\\n  baz\nqux\n", 4, 1),
    ("exec -a name foo\nbar\n", 2, 1),
    ("exec -- foo\nbar\n", 2, 1),
    ("exec \"$GOH/bin/goh\" check\nbar\n", 2, 1),
    ("exec 2>&1 foo\nbar\n", 2, 1),
    ("exec >\"$log\" foo\nbar\n", 2, 1),
    ("exec \"$@\"\nbar\n", 2, 1),
    // A substitution in the exec's own words is part of them, not a `)` that ends it.
    ("exec $(command -v foo) --bar\nbaz\n", 2, 1),
    ("exec diff <(a) <(b)\nbaz\n", 2, 1),
    ("exec `a; b` x\nbaz\n", 2, 1),
    ("if a; then exec foo\n  bar\nfi\n", 2, 1),
    ("{ exec foo\n  bar; }\n", 2, 1),
    // A multi-line quoted argument is part of the exec line, not a statement after it.
    ("exec foo \"a\nb\" 'c\nd'\nbar\n", 4, 1),
    ("exec foo \"$(a\n b)\"\nbar\n", 3, 1),
    // A heredoc body is not code; the line after its terminator is.
    ("exec cat <<EOF\nbar\nEOF\nbaz\n", 4, 1),
    // After the allowed `exit`, anything more is still dead.
    ("exec foo\nexit\nbar\n", 3, 1),
];

/// Scripts with no dead statement.
const GREEN: &[&str] = &[
    // The house parse-guard idiom (gates/goh.sh): exec, a bare `exit`, the group's close.
    "{ # parse-guard\nset -e\nexec \"$goh_native\" \"$check\" \"$@\"\nexit\n} # parse-guard\n",
    "{\nexec foo\nexit 0\n}\n",
    "exec foo\nexit \"$?\"\n",
    // The incident, fixed (app_updates 5a4b33c).
    "case \"$1\" in\n  --full)\n    python3 tools/check_roadmap.py --self-test\n    exec python3 tools/check_roadmap.py\n    ;;\nesac\n",
    // exec last in its block, by every block end.
    "exec foo\n",
    "exec foo",
    "exec foo\n\n# trailing comment\n",
    "a) exec foo ;;\nb) bar ;;\n",
    "a) exec foo\n   ;;\nb) bar ;;\n",
    "case x in\n a) exec foo;;\n b) exec bar;&\n c) baz;;\nesac\nqux\n",
    "case x in\n a) exec foo\nesac\nqux\n",
    "if a; then\n  exec foo\nfi\nbar\n",
    "if a; then exec foo; else exec bar; fi; baz\n",
    "if a; then\n  exec foo\nelif b; then\n  exec bar\nelse\n  baz\nfi\n",
    "while :; do\n  exec foo\ndone\nbar\n",
    "f() {\n  exec foo\n}\ng\n",
    "f() { exec foo; }\ng\n",
    "f() (\n  exec foo\n)\ng\n",
    // An fd/redirect-only exec does not replace the shell.
    "exec >\"$log\" 2>&1\nbar\n",
    "exec > \"$log\"\nbar\n",
    "exec 3<&-\nbar\n",
    "exec 2>&1\nbar\n",
    "exec {fd}>\"$f\"\nbar\n",
    "exec </dev/null\nbar\n",
    "exec 3>&1 4>&2 1>>\"$log\"\nbar\n",
    "exec\nbar\n",
    "exec -a name\nbar\n",
    // An exec whose failure is handled, or that runs in a pipeline, a background job, a
    // subshell, or a condition.
    "exec foo || fallback\nbar\n",
    "exec foo ||\n  fallback\nbar\n",
    "exec foo || { echo failed; exit 1; }\nbar\n",
    "[ -x foo ] && exec foo\nbar\n",
    // ...and the same with the list broken across lines: the exec is still conditional.
    "[ -x foo ] ||\n  exec foo\nbar\n",
    "cond &&\n\n  exec foo\nbar\n",
    "producer |\n  exec foo\nbar\n",
    "exec foo | tee log\nbar\n",
    "exec foo &\nbar\n",
    "( exec foo )\nbar\n",
    "(exec foo); bar\n",
    "v=$(exec foo)\nbar\n",
    "v=\"$(exec foo)\"\nbar\n",
    "if exec foo; then bar; fi\n",
    // Not an exec at all.
    "echo exec foo\nbar\n",
    "echo 'exec foo'\nbar\n",
    "# exec foo\nbar\n",
    "exec_foo\nbar\n",
    "exec=1\nbar\n",
    "cat <<EOF\nexec foo\nbar\nEOF\nbaz\n",
    "echo \"a\nexec foo\nb\"\nbar\n",
];

fn lines(found: &[Finding]) -> Vec<(usize, usize)> {
    found.iter().map(|f| (f.line, f.exec_line)).collect()
}

#[test]
fn every_dead_shape_is_named_on_its_line() {
    for (src, dead, exec) in RED {
        assert_eq!(lines(&scan_text(src)), [(*dead, *exec)], "{src:?}");
    }
}

#[test]
fn every_live_shape_is_clean() {
    for src in GREEN {
        assert_eq!(scan_text(src), [], "{src:?}");
    }
}

#[test]
fn the_finding_quotes_the_dead_line() {
    let found = scan_text("exec foo\n  bar   --baz  # why\n");
    assert_eq!(found[0].text, "bar --baz");
    assert_eq!(found[0].exec, "exec foo");
}

#[test]
fn only_the_first_dead_statement_is_named() {
    assert_eq!(
        lines(&scan_text("exec a\nb\nc\nexec d\ne\n")),
        [(2, 1), (5, 4)]
    );
}
