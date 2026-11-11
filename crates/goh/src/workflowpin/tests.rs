//! The check's specification as tables: every moving runner label and action ref RED on the line
//! named, every named image, commit, release tag, local path and reasoned escape GREEN.
//!
//! The rows are the estate's own: M.FLOODREAP's `ubuntu-latest` (CI warned it moves to Ubuntu 26),
//! antiknob's `actions/checkout@v7` and `dtolnay/rust-toolchain@stable` before GOH.RUNNERPIN, and
//! monitor's hand pins (rust-cache v2.9.3, a toolchain by commit) after M.CIPARITY.

use super::scan::{in_scope, is_moving_runner, is_release, judge_uses, scan, Problem, Use};

#[derive(Debug, Clone, PartialEq, Eq)]
enum K {
    NoRef,
    Action(&'static str),
    Runner(&'static str),
    Empty,
    Stale,
    Unparsable,
}

fn kinds(src: &str) -> Vec<(usize, K)> {
    scan(src)
        .findings
        .into_iter()
        .map(|f| {
            let k = match f.problem {
                Problem::NoRef(_) => K::NoRef,
                Problem::MovingAction(_, r) => K::Action(match r.as_str() {
                    "v4" => "v4",
                    "v7" => "v7",
                    "stable" => "stable",
                    "main" => "main",
                    _ => "other",
                }),
                Problem::MovingRunner(l) => K::Runner(match l.as_str() {
                    "ubuntu-latest" => "ubuntu-latest",
                    "macos-latest" => "macos-latest",
                    "windows-latest" => "windows-latest",
                    _ => "other",
                }),
                Problem::EmptyReason => K::Empty,
                Problem::StaleMarker => K::Stale,
                Problem::Unparsable(_) => K::Unparsable,
            };
            (f.line, k)
        })
        .collect()
}

const SHA: &str = "3d3c42e5aac5ba805825da76410c181273ba90b1";

/// A job's first lines: `runs-on` is line 4, the first step line 6.
fn job(runs_on: &str, steps: &str) -> String {
    format!("on: push\njobs:\n  build:\n    runs-on: {runs_on}\n    steps:\n{steps}")
}

#[test]
fn a_runner_that_moves_is_red_on_its_line() {
    for (label, kind) in [
        ("ubuntu-latest", K::Runner("ubuntu-latest")),
        ("macos-latest", K::Runner("macos-latest")),
        ("windows-latest", K::Runner("windows-latest")),
    ] {
        let src = job(label, "      - run: true\n");
        assert_eq!(kinds(&src), vec![(4, kind)], "{label}");
    }
}

#[test]
fn a_named_image_is_green() {
    for label in [
        "ubuntu-24.04",
        "macos-26",
        "windows-2025",
        "ubuntu-24.04-arm",
        "self-hosted",
    ] {
        let src = job(label, "      - run: true\n");
        assert_eq!(kinds(&src), vec![], "{label}");
    }
}

#[test]
fn every_shape_a_label_travels_in_is_read() {
    // A flow list, a block list, a matrix, a labels mapping, an expression, a bigger runner.
    let rows: &[(&str, usize)] = &[
        ("on: push\njobs:\n  a:\n    runs-on: [self-hosted, ubuntu-latest]\n    steps: []\n", 4),
        ("on: push\njobs:\n  a:\n    runs-on:\n      - macos-latest\n    steps: []\n", 5),
        (
            "on: push\njobs:\n  a:\n    strategy:\n      matrix:\n        os: [ubuntu-24.04, ubuntu-latest]\n    runs-on: ${{ matrix.os }}\n    steps: []\n",
            6,
        ),
        (
            "on: push\njobs:\n  a:\n    runs-on:\n      group: g\n      labels: ubuntu-latest\n    steps: []\n",
            6,
        ),
        (
            "on: push\njobs:\n  a:\n    runs-on: ${{ inputs.os || 'ubuntu-latest' }}\n    steps: []\n",
            4,
        ),
        ("on: push\njobs:\n  a:\n    runs-on: macos-latest-xlarge\n    steps: []\n", 4),
        ("on: push\njobs:\n  a:\n    runs-on: \"ubuntu-latest\"\n    steps: []\n", 4),
        // The workflow_call input a caller's runner comes from.
        (
            "on:\n  workflow_call:\n    inputs:\n      runner:\n        type: string\n        default: ubuntu-latest\n",
            6,
        ),
    ];
    for (src, line) in rows {
        let got = kinds(src);
        assert_eq!(got.len(), 1, "{src}: {got:?}");
        assert_eq!(got[0].0, *line, "{src}");
        assert!(matches!(got[0].1, K::Runner(_)), "{src}: {got:?}");
    }
}

#[test]
fn the_matrix_of_named_images_and_an_unresolvable_expression_is_green() {
    let src = "on: push\njobs:\n  a:\n    strategy:\n      matrix:\n        os: [ubuntu-24.04, macos-26]\n    runs-on: ${{ matrix.os }}\n    steps: []\n";
    assert_eq!(kinds(src), vec![]);
}

#[test]
fn an_action_on_a_ref_its_owner_moves_is_red() {
    let rows: &[(&str, K)] = &[
        // The incident shapes: a major-only tag and a branch.
        ("      - uses: actions/checkout@v4\n", K::Action("v4")),
        ("      - uses: actions/checkout@v7\n", K::Action("v7")),
        (
            "      - uses: dtolnay/rust-toolchain@stable\n",
            K::Action("stable"),
        ),
        ("      - uses: owner/tool@main\n", K::Action("main")),
        ("      - uses: owner/tool@v7.0\n", K::Action("other")),
        ("      - uses: owner/tool@release/v2\n", K::Action("other")),
        ("      - uses: owner/tool@abc123\n", K::Action("other")),
        ("      - uses: 'actions/cache@v4'\n", K::Action("v4")),
        ("      - uses: owner/tool/sub/dir@v2\n", K::Action("other")),
        ("      - uses: owner/tool\n", K::NoRef),
    ];
    for (step, kind) in rows {
        let src = job("ubuntu-24.04", step);
        let got = kinds(&src);
        assert_eq!(got, vec![(6, kind.clone())], "{step}");
    }
}

#[test]
fn a_commit_a_release_tag_and_a_local_path_are_green() {
    let rows = [
        format!("      - uses: actions/checkout@{SHA} # v7.0.1\n"),
        format!("      - uses: actions/checkout@{SHA}\n"),
        "      - uses: Swatinem/rust-cache@v2.9.3\n".to_owned(),
        "      - uses: actions/checkout@v7.0.1\n".to_owned(),
        "      - uses: owner/tool@1.2.3\n".to_owned(),
        "      - uses: owner/tool@v1.2.3-rc.1\n".to_owned(),
        "      - uses: ./.github/actions/system-deps\n".to_owned(),
        "      - uses: ./goh/.github/actions/goh-floors\n".to_owned(),
        // judged by `goh moving-tags`, not here
        "      - uses: docker://alpine:latest\n".to_owned(),
        // an expression cannot be resolved: unreadable, not a finding
        "      - uses: ${{ matrix.action }}\n".to_owned(),
    ];
    for step in rows {
        assert_eq!(kinds(&job("ubuntu-24.04", &step)), vec![], "{step}");
    }
}

#[test]
fn a_uses_in_script_text_is_not_a_reference() {
    // The parse decides: this `uses:` is the text of a `run: |` block.
    let src = job(
        "ubuntu-24.04",
        "      - run: |\n          echo 'uses: actions/checkout@v4'\n          echo ubuntu-latest\n",
    );
    assert_eq!(kinds(&src), vec![]);
}

#[test]
fn the_reasoned_escape_exempts_one_reference_and_nothing_else() {
    // On the line.
    let on = job(
        "ubuntu-24.04",
        "      - uses: vendor/tool@main  # workflow-pin-ok: the vendor tags no releases\n",
    );
    assert_eq!(kinds(&on), vec![]);
    assert_eq!(scan(&on).exempt, 1);
    // In the comment lines directly above.
    let above = job(
        "ubuntu-24.04",
        "      # why it floats\n      # workflow-pin-ok: the vendor tags no releases\n      - uses: vendor/tool@main\n",
    );
    assert_eq!(kinds(&above), vec![]);
    // A blank line between is not "directly above".
    let apart = job(
        "ubuntu-24.04",
        "      # workflow-pin-ok: reason\n\n      - uses: vendor/tool@main\n",
    );
    assert_eq!(kinds(&apart), vec![(6, K::Stale), (8, K::Action("main"))]);
    // It covers the reference under it, not the next one.
    let next = job(
        "ubuntu-24.04",
        "      - uses: vendor/tool@main  # workflow-pin-ok: reason\n      - uses: other/tool@main\n",
    );
    assert_eq!(kinds(&next), vec![(7, K::Action("main"))]);
    // A runner label takes it too.
    let runner = "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest  # workflow-pin-ok: needs the newest image\n    steps: []\n";
    assert_eq!(kinds(runner), vec![]);
}

#[test]
fn an_empty_reason_and_a_stale_marker_are_findings() {
    let empty = job(
        "ubuntu-24.04",
        "      # workflow-pin-ok:\n      - uses: a/b@main\n",
    );
    assert_eq!(kinds(&empty), vec![(7, K::Empty)]);
    let stale = job(
        "ubuntu-24.04",
        "      # workflow-pin-ok: a reason\n      - uses: a/b@v1.2.3\n",
    );
    assert_eq!(kinds(&stale), vec![(6, K::Stale)]);
}

#[test]
fn a_file_that_is_not_yaml_cannot_be_shown_pinned() {
    assert_eq!(kinds("jobs:\n  a: [unclosed\n"), vec![(3, K::Unparsable)]);
}

#[test]
fn one_workflow_reports_every_finding_in_line_order() {
    let src = "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - uses: dtolnay/rust-toolchain@stable\n";
    assert_eq!(
        kinds(src),
        vec![
            (4, K::Runner("ubuntu-latest")),
            (6, K::Action("v4")),
            (7, K::Action("stable")),
        ]
    );
}

#[test]
fn what_is_read_is_counted_for_the_floor() {
    let src = job(
        "ubuntu-24.04",
        &format!(
            "      - uses: actions/checkout@{SHA}\n      - uses: a/b@v1.2.3\n      - uses: ./local\n      - uses: ${{{{ x }}}}\n"
        ),
    );
    let s = scan(&src);
    // one runner + three non-local references; the local path is not a reference
    assert_eq!((s.refs, s.shas, s.releases, s.unreadable), (4, 1, 1, 1));
}

#[test]
fn the_release_tag_and_runner_predicates() {
    for r in ["v7.0.1", "7.0.1", "v1.2.3-rc.1", "v10.20.30+build5"] {
        assert!(is_release(r), "{r}");
    }
    for r in [
        "v7", "v7.0", "7", "main", "stable", "v7.x.1", "v1.2.3.4", "", "v",
    ] {
        assert!(!is_release(r), "{r}");
    }
    for l in [
        "ubuntu-latest",
        "UBUNTU-LATEST",
        "macos-latest-xlarge",
        "windows-latest-8-cores",
    ] {
        assert!(is_moving_runner(l), "{l}");
    }
    for l in [
        "ubuntu-24.04",
        "macos-26",
        "ubuntu-latestx",
        "my-ubuntu-latest",
        "self-hosted",
        "latest",
    ] {
        assert!(!is_moving_runner(l), "{l}");
    }
    assert_eq!(judge_uses("./x"), Use::Local);
    assert_eq!(judge_uses("a/b@main"), Use::Moving("main".to_owned()));
    // a 64-hex ref is a commit id of the sha-256 object format
    assert_eq!(
        judge_uses(&format!("a/b@{SHA}{}", "0".repeat(24))),
        Use::Sha
    );
    // 39 hex is not a commit id
    assert_eq!(
        judge_uses(&format!("a/b@{}", &SHA[1..])),
        Use::Moving(SHA[1..].to_owned())
    );
}

#[test]
fn only_workflows_and_composite_actions_are_in_scope() {
    for rel in [
        ".github/workflows/ci.yml",
        ".github/workflows/ci.yaml",
        ".github/actions/x/action.yml",
        ".github/actions/x/y/action.yaml",
    ] {
        assert!(in_scope(rel), "{rel}");
    }
    for rel in [
        "ci.yml",
        ".github/workflows/sub/ci.yml",
        ".github/workflows/README.md",
        ".github/actions/x/script.sh",
        ".github/dependabot.yml",
    ] {
        assert!(!in_scope(rel), "{rel}");
    }
}
