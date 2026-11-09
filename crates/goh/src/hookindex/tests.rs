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

mod index_reads {
    use super::super::index::{hook_run, unbound_reads};
    use super::super::seed::{judge, Seed};
    use std::collections::BTreeMap;

    /// `(script, the lines of its unbound index reads)`.
    const TABLE: &[(&str, &[usize])] = &[
        // .loop's tools/gate.sh before 50ff65b.
        ("staged=\"$(git diff --cached --name-only)\"\n", &[1]),
        ("git -C \"$root\" diff-index --cached HEAD\n", &[1]),
        ("git --no-pager -c a=b diff --staged\n", &[1]),
        ("git show \":queue.json\" | jq .\n", &[1]),
        ("git ls-files --stage\ngit ls-files -zs\n", &[1, 2]),
        ("t=`git write-tree`\n", &[1]),
        // A continuation is one command, named at its first line.
        ("git diff \\\n  --cached\n", &[1]),
        // Bound: earlier in the file, or earlier on the line.
        ("goh_bind_hook_index\ngit diff --cached\n", &[]),
        ("x=\"$( (goh_bind_hook_index; git diff --cached) )\"\n", &[]),
        ("GIT_INDEX_FILE=\"$i\" git write-tree\n", &[]),
        // A bind AFTER the read binds nothing before it.
        (
            "git write-tree\ngoh_bind_hook_index\ngit write-tree\n",
            &[1],
        ),
        // Not index reads.
        (
            "git diff --name-only HEAD\ngit show HEAD:a\ngit ls-files -- '*.py'\n",
            &[],
        ),
        ("# git diff --cached\necho done # git write-tree\n", &[]),
        ("echo git; diff --cached a b\n", &[]),
    ];

    #[test]
    fn every_unbound_read_is_named_on_its_line() {
        for (src, want) in TABLE {
            let got: Vec<usize> = unbound_reads(src).iter().map(|r| r.line).collect();
            assert_eq!(&got, want, "{src:?}");
        }
    }

    fn src(p: &str, t: &str) -> (String, String) {
        (p.to_string(), t.to_string())
    }

    #[test]
    fn hook_run_is_what_the_hook_reaches() {
        let sources = vec![
            src(
                "tools/gate.sh",
                "\"$(dirname \"$0\")/run_gates.sh\"\n. \"$here/lib/x.sh\"\n",
            ),
            src("tools/run_gates.sh", ". \"$root/gates/common.sh\"\n"),
            src("tools/lib/x.sh", "true\n"),
            src("gates/common.sh", "true\n"),
            src("tools/other_gates.sh", "true\n"),
            src("release.sh", "bash tools/run_gates.sh\n"),
            src(
                "tools/unnamed.sh",
                "# tools/gate.sh names me only in a comment\n",
            ),
        ];
        let reached: Vec<String> = hook_run(&sources).into_iter().collect();
        assert_eq!(
            reached,
            [
                "gates/common.sh",
                "tools/gate.sh",
                "tools/lib/x.sh",
                "tools/run_gates.sh"
            ]
        );
        let none = vec![src("release.sh", "git write-tree\n")];
        assert_eq!(hook_run(&none).len(), 0);
    }

    #[test]
    fn the_seed_warns_at_its_count_and_never_hides_more() {
        let mut seed = Seed::new();
        seed.insert(("r".into(), "a.sh".into()), 2);
        seed.insert(("r".into(), "gone.sh".into()), 1);
        seed.insert(("s".into(), "a.sh".into()), 1);
        let found = |n: usize| BTreeMap::from([("a.sh".to_string(), n)]);
        let at = judge("r", &found(2), &seed, true);
        assert_eq!(at.red, Vec::<String>::new());
        assert_eq!(
            at.warn.iter().map(|w| w.0.as_str()).collect::<Vec<_>>(),
            ["a.sh", "gone.sh"]
        );
        assert_eq!(judge("r", &found(3), &seed, false).red, ["a.sh"]);
        assert!(judge("r", &found(1), &seed, false).warn[0]
            .1
            .contains("lower"));
        assert_eq!(judge("q", &found(1), &seed, true).red, ["a.sh"]);
        // A staged run reads only what changed: a seeded file it did not read is not stale.
        assert_eq!(judge("r", &BTreeMap::new(), &seed, false).warn, vec![]);
    }

    #[test]
    fn the_checked_in_seed_loads() {
        let goh = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let seed = super::super::seed::load(&goh).expect("hook_index_seed.json loads");
        assert!(seed.keys().all(|(r, p)| !r.is_empty() && !p.is_empty()));
    }
}
