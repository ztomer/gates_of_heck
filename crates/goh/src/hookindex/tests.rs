//! The check's specification as a table: every read RED on the line named, every mention GREEN.
//!
//! The first row is the incident as it was written (routines' and `media_server`'s tools/gate.sh,
//! 2026-10-08): a gate a test ran on a FIXTURE inherited the outer hook's carried index and
//! judged the outer repository's index ("unable to read 404acec").

use super::scan::{defines_binder, reads, Read, CARRIED};

const INDEX: &str = CARRIED[0];
const GIT_DIR: &str = CARRIED[1];

/// `(script, the expanding lines and their variables)`.
const RED: &[(&str, &[(usize, &str)])] = &[
    // The incident.
    (
        "staged=\"$(GIT_INDEX_FILE=\"${GOH_HOOK_INDEX_FILE:-$(git rev-parse --git-path index)}\" git diff --cached --name-only --diff-filter=d)\"\n",
        &[(1, INDEX)],
    ),
    // Bare, braced, length, and the git dir beside it.
    ("GIT_INDEX_FILE=$GOH_HOOK_INDEX_FILE git diff --cached\n", &[(1, INDEX)]),
    ("[ -n \"$GOH_HOOK_GIT_DIR\" ] && x\n", &[(1, GIT_DIR)]),
    ("echo ${#GOH_HOOK_INDEX_FILE}\n", &[(1, INDEX)]),
    ("echo ${!GOH_HOOK_GIT_DIR}\n", &[(1, GIT_DIR)]),
    // An unquoted heredoc expands its body; `<<-` strips the terminator's tabs.
    ("cat <<EOF\n$GOH_HOOK_INDEX_FILE\nEOF\n", &[(2, INDEX)]),
    ("cat <<-EOF\n\t$GOH_HOOK_GIT_DIR\n\tEOF\n", &[(2, GIT_DIR)]),
    // What came before does not hide a read: a comment, a single-quoted string, a quoted
    // heredoc, a line continuation.
    ("# x\nf=$GOH_HOOK_INDEX_FILE\n", &[(2, INDEX)]),
    ("echo 'it''s'\nf=\"$GOH_HOOK_GIT_DIR\"\n", &[(2, GIT_DIR)]),
    (
        "cat <<'EOF'\n$GOH_HOOK_INDEX_FILE\nEOF\nf=$GOH_HOOK_INDEX_FILE\n",
        &[(4, INDEX)],
    ),
    ("x=1 \\\n  y=\"$GOH_HOOK_INDEX_FILE\"\n", &[(2, INDEX)]),
    // A `#` inside a word is not a comment.
    ("echo a#$GOH_HOOK_GIT_DIR\n", &[(1, GIT_DIR)]),
    // Two on one line, two lines.
    (
        "a=$GOH_HOOK_INDEX_FILE b=$GOH_HOOK_GIT_DIR\nc=$GOH_HOOK_INDEX_FILE\n",
        &[(1, INDEX), (1, GIT_DIR), (2, INDEX)],
    ),
];

/// Mentions that are not expansions.
const GREEN: &[&str] = &[
    "# GIT_INDEX_FILE=\"${GOH_HOOK_INDEX_FILE}\"\n",
    "echo 'literal $GOH_HOOK_INDEX_FILE'\n",
    "unset GOH_HOOK_INDEX_FILE GOH_HOOK_GIT_DIR\n",
    "GOH_HOOK_INDEX_FILE=x some_cmd\n",
    "echo \"\\$GOH_HOOK_INDEX_FILE\" \\$GOH_HOOK_GIT_DIR\n",
    "cat <<'EOF'\n$GOH_HOOK_INDEX_FILE\nEOF\n",
    "cat <<\"EOF\"\n$GOH_HOOK_GIT_DIR\nEOF\n",
    "cat <<\\EOF\n$GOH_HOOK_INDEX_FILE\nEOF\n",
    "echo $GOH_HOOK_INDEX_FILES \"${GOH_HOOK_GIT_DIRS}\"\n",
    "cat <<<\"$x\" # $GOH_HOOK_INDEX_FILE\n",
    "x=1 # $GOH_HOOK_GIT_DIR\n",
];

#[test]
fn every_read_is_found_on_its_line() {
    for (src, want) in RED {
        let got: Vec<(usize, &str)> = reads(src).iter().map(|r| (r.line, r.var)).collect();
        assert_eq!(&got, want, "{src:?}");
    }
}

#[test]
fn a_mention_is_not_a_read() {
    for src in GREEN {
        assert_eq!(reads(src), Vec::<Read>::new(), "{src:?}");
    }
}

#[test]
fn the_binder_is_recognised_by_its_definition_not_a_call() {
    for src in [
        "goh_bind_hook_index() {\n",
        "  goh_bind_hook_index ()\n{\n",
        "function goh_bind_hook_index {\n",
        "function goh_bind_hook_index() {\n",
    ] {
        assert!(defines_binder(src), "{src:?}");
    }
    for src in [
        "goh_bind_hook_index\n",
        "( goh_bind_hook_index; git diff --cached )\n",
        "# goh_bind_hook_index() binds it\n",
    ] {
        assert!(!defines_binder(src), "{src:?}");
    }
}
