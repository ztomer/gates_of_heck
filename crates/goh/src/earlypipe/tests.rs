//! The check's specification as a table: every consumer form RED, every fixed form GREEN.
//!
//! Each row is a whole script body judged with pipefail ON (the header below is prepended), so a
//! row that goes green for the wrong reason -- the pipefail detector, not the consumer rule --
//! cannot hide: the pipefail rows at the bottom pin that half on its own.

use super::{is_shell, scan_text, Finding};

const PIPEFAIL: &str = "#!/usr/bin/env bash\nset -euo pipefail\n";

fn judge(body: &str) -> Vec<Finding> {
    scan_text(&format!("{PIPEFAIL}{body}"))
}

/// `(body, consumer kind)`: each must yield exactly one finding, of that kind, on the body's
/// LAST line (where every row puts its consumer), counted after the two-line header.
const RED: &[(&str, &str)] = &[
    // The measured incidents, as they were written.
    (
        "docker compose ps --services | grep -qxF \"$svc\"\n",
        "grep -q",
    ),
    (
        "if git diff --cached --name-only | grep -qE '\\.rs$'; then :; fi\n",
        "grep -q",
    ),
    ("ps -Ao pcpu,comm -r | head -6 >&2\n", "head"),
    // grep's early-exit flags, every spelling.
    ("printf '%s\\n' \"$t\" | grep -q x\n", "grep -q"),
    ("echo \"$x\" | grep -Eq '^[0-9]+$'\n", "grep -q"),
    ("echo \"$x\" | grep -qi ghostty\n", "grep -q"),
    ("echo \"$x\" | grep --quiet x\n", "grep -q"),
    ("echo \"$x\" | grep --silent x\n", "grep -q"),
    ("echo \"$x\" | egrep -q 'a|b'\n", "grep -q"),
    ("echo \"$x\" | /usr/bin/grep -q x\n", "grep -q"),
    ("echo \"$x\" | LC_ALL=C grep -q x\n", "grep -q"),
    ("echo \"$x\" | grep -l x\n", "grep -q"),
    (
        "v=\"$(git show HEAD:a | grep -m1 '^version')\"\n",
        "grep -m",
    ),
    ("v=$(cat a | grep -m 1 x)\n", "grep -m"),
    ("v=$(cat a | grep --max-count=1 x)\n", "grep -m"),
    ("v=$(cat a | grep --max-count 1 x)\n", "grep -m"),
    // head, in a command substitution and bare.
    (
        "v=\"$(\"$1\" source-tree 2>/dev/null | head -n1)\"\n",
        "head",
    ),
    ("v=$(ls | head -1)\n", "head"),
    ("v=`ls | head -1`\n", "head"),
    ("ls | head -c 120\n", "head"),
    ("ls | head\n", "head"),
    // awk that exits before EOF.
    ("v=$(ls | awk 'NR==1{print; exit}')\n", "awk exit"),
    ("ls | gawk '/x/ { found=1; exit 0 }'\n", "awk exit"),
    ("ls | awk -F: 'BEGIN { exit }'\n", "awk exit"),
    // sed that quits.
    ("v=$(ls | sed 1q)\n", "sed q"),
    ("v=$(ls | sed -n '/x/{p;q}')\n", "sed q"),
    ("v=$(ls | sed -e 's/a/b/' -e 3Q)\n", "sed q"),
    ("v=$(ls | sed -ne '$!d' -e 'q')\n", "sed q"),
    // A pipeline continued across lines, both ways.
    ("ls \\\n  | grep -q x\n", "grep -q"),
    ("ls |\n  head -1\n", "head"),
    // `|&` is a pipe too.
    ("ls |& grep -q x\n", "grep -q"),
    // A consumer mid-pipeline still closes on its producer.
    ("v=$(ls | head -5 | tr '\\n' ' ')\n", "head"),
    // An assignment's status IS read: by `set -e`, by `if`, after `!`, and after other assignments.
    ("if v=$(ls | head -1); then :; fi\n", "head"),
    ("a=1 b=\"$(ls | grep -m1 x)\"\n", "grep -m"),
    ("x=1; v[0]=$(ls | head -1)\n", "head"),
    // `|| echo` reads the verdict: still racy.
    ("ls | grep -q x || echo missing\n", "grep -q"),
];

/// Bodies that must yield NO finding: the fixed shapes, and the look-alikes.
const GREEN: &[&str] = &[
    // The fix shapes the report prints.
    "out=\"$(docker compose ps --services)\"\ngrep -qxF \"$svc\" <<< \"$out\"\n",
    "out=\"$(ls)\"\nhead -n1 <<< \"$out\"\n",
    "out=\"$(ls)\"\n[ -n \"$out\" ]\n",
    "grep -q x file.txt\n",
    "grep -q x < file.txt\n",
    // The status is discarded: nothing for the race to corrupt.
    "ps -Ao pcpu,comm -r | head -6 >&2 || true\n",
    "v=$(cargo metadata | grep -o x | head -n1 | cut -d'\"' -f4 || true)\n",
    "ls | head -1 || :\n",
    // A substitution whose status nothing reads: an argument, or a masking builtin's value.
    // The race corrupts only the status; the output is whole.
    "echo \"first: $(ls | head -1)\"\n",
    "printf '%s\\n' \"$(ls | grep -m1 x)\"\n",
    "[ -z \"$(git ls-files -- \"$c\" | head -1)\" ] || continue\n",
    "refuse \"tags: $(git tag | head -3 | tr '\\n' ' ')\"\n",
    "local v=$(ls | head -1)\n",
    "export V=\"$(ls | head -1)\"\n",
    // Consumers that read to EOF.
    "ls | grep x\n",
    "ls | grep -c x\n",
    "ls | grep -s x\n",
    "ls | grep -e -q\n",
    "ls | tail -1\n",
    "ls | head -n -2\n",
    "ls | awk '{print $1}'\n",
    "ls | awk '{n++} END { exit n == 0 }'\n",
    "ls | sed -n '1p'\n",
    "ls | sed 's/q/x/'\n",
    "ls | sed 'y/abq/xyz/'\n",
    "ls | sed -e 's/a/b/;s/c/q/g'\n",
    // `||` and `>|` are not pipes.
    "test -f a || head -1 b\n",
    "echo x >| out.txt\n",
    // Comments, strings and heredoc bodies are not code.
    "# ls | grep -q x\n",
    "echo hi  # ls | head -1\n",
    "echo 'ls | grep -q x'\n",
    "echo \"ls | head -1\"\n",
    "cat <<EOF\nls | grep -q x\nEOF\n",
    "cat <<-'EOF'\n\tls | head -1\n\tEOF\n",
    "echo \\| head\n",
    // A case pattern alternative named like a consumer.
    "case \"$x\" in\n  tail|head) echo y ;;\nesac\n",
];

#[test]
fn every_consumer_form_is_red() {
    for (body, kind) in RED {
        let got = judge(body);
        assert_eq!(got.len(), 1, "want one finding for {body:?}, got {got:?}");
        assert_eq!(got[0].kind, *kind, "{body:?}");
        assert_eq!(
            got[0].line,
            2 + body.matches('\n').count(),
            "{body:?}: line"
        );
    }
}

#[test]
fn every_fixed_form_and_look_alike_is_green() {
    for body in GREEN {
        let got = judge(body);
        assert!(got.is_empty(), "want no finding for {body:?}, got {got:?}");
    }
}

#[test]
fn a_continued_pipeline_is_reported_on_the_consumers_line() {
    let got = judge("ls |\n  grep -q x\n");
    assert_eq!(got.len(), 1);
    assert_eq!(got[0].line, 4);
    assert_eq!(got[0].text, "grep -q x");
}

#[test]
fn a_command_substitution_inside_double_quotes_is_code() {
    let got = judge("v=\"first: $(ls | head -1), done\"\n");
    assert_eq!(got.len(), 1, "{got:?}");
}

#[test]
fn several_on_one_line_are_each_reported() {
    let got = judge("a=$(ls | head -1); b=$(ls | grep -m1 x)\n");
    let kinds: Vec<&str> = got.iter().map(|f| f.kind).collect();
    assert_eq!(kinds, ["head", "grep -m"]);
}

// ── scope: every shell source, never gated on its own pipefail line ─────

/// A file with no pipefail line still runs under one: a library its caller sources
/// (`media_server`'s install-lib.sh, sourced by install.sh, let a release through a scan that
/// only read files saying "pipefail"), or a script whose caller exports SHELLOPTS. The scan
/// does not ask the file.
#[test]
fn a_file_with_no_pipefail_line_is_still_read() {
    for src in [
        "ls | grep -q x\n",
        "#!/bin/bash\nset -eu\nls | grep -q x\n",
        "#!/bin/bash\nset +o pipefail\nls | grep -q x\n",
    ] {
        assert_eq!(scan_text(src).len(), 1, "{src:?}");
    }
}

/// Every shell SOURCE is in scope, the template an installer is regenerated from included: a
/// `.sh.tmpl` left out let a release put a fixed site straight back (`media_server`, 2026-10-08).
#[test]
fn every_shell_source_is_in_scope() {
    for (rel, head) in [
        ("a.sh", ""),
        ("release/install.sh.tmpl", ""),
        ("a.bash", ""),
        ("a.zsh", ""),
        ("bin/tool", "#!/usr/bin/env bash\n"),
        ("hooks/pre-commit", "#!/bin/sh\n"),
        ("x", "#!/usr/bin/env -S bash -e\n"),
    ] {
        assert!(is_shell(rel, head.as_bytes()), "{rel}");
    }
    for (rel, head) in [
        ("a.py", "#!/usr/bin/env python3\n"),
        ("notes.txt", "ls | head -1\n"),
        ("a.sh.bak", ""),
        ("README.md", ""),
    ] {
        assert!(!is_shell(rel, head.as_bytes()), "{rel}");
    }
}
