//! The check's specification as a table: every checkout that keeps its token RED on the line
//! named, every one that drops it (or keeps it with a reason) GREEN.
//!
//! The first row is the incident's shape (monitor's CI, red 2026-10-05..08): a default checkout,
//! then a step that ran the structural gate and found the token in `.git/config`.

use super::scan::{comment_of, findings, in_scope, Problem};

/// Problem kinds without payloads, so a row says WHAT, not the parser's wording.
#[derive(Debug, PartialEq, Eq)]
enum K {
    Unset,
    NotFalse(&'static str),
    Empty,
    Stale,
    Unparsable,
}

fn kinds(src: &str) -> Vec<(usize, K)> {
    findings(src)
        .into_iter()
        .map(|f| {
            let k = match f.problem {
                Problem::Unset => K::Unset,
                Problem::NotFalse(v) => K::NotFalse(match v.as_str() {
                    "true" => "true",
                    "no" => "no",
                    v if v.contains("${{") => "expr",
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

const HEAD: &str = "on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n";

/// `(steps appended to HEAD, the findings by line)`. HEAD is five lines: the first step is line 6.
const RED: &[(&str, &[(usize, K)])] = &[
    // The incident: the default checkout, then the gate.
    (
        "      - uses: actions/checkout@v4\n      - run: tools/gate.sh --full\n",
        &[(6, K::Unset)],
    ),
    // A `with:` without the key is still the default.
    ("      - uses: actions/checkout@v5\n        with:\n          fetch-depth: 0\n", &[(6, K::Unset)]),
    ("      - uses: actions/checkout@v4\n        with:\n          persist-credentials: true\n", &[(8, K::NotFalse("true"))]),
    (
        "      - uses: actions/checkout@v4\n        with:\n          persist-credentials: ${{ inputs.push }}\n",
        &[(8, K::NotFalse("expr"))],
    ),
    // `no` is false to YAML 1.1 and to checkout's own parse, but it is not the literal false.
    ("      - uses: actions/checkout@v4\n        with:\n          persist-credentials: no\n", &[(8, K::NotFalse("no"))]),
    // Pinned by sha; owner/repo in another case.
    ("      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2\n", &[(6, K::Unset)]),
    ("      - uses: Actions/Checkout@v4\n", &[(6, K::Unset)]),
    // Several checkouts, one bad: the bad one is named.
    (
        "      - uses: actions/checkout@v4\n        with:\n          persist-credentials: false\n      - name: second tree\n        uses: actions/checkout@v4\n        with:\n          path: other\n      - uses: actions/checkout@v4\n        with: {persist-credentials: false}\n",
        &[(9, K::Unset)],
    ),
    // A flow-mapping step.
    ("      - {uses: actions/checkout@v4, with: {persist-credentials: true}}\n", &[(6, K::NotFalse("true"))]),
    // An empty reason is no reason, above the step or on it.
    ("      # persist-credentials-ok:\n      - uses: actions/checkout@v4\n", &[(7, K::Empty)]),
    ("      - uses: actions/checkout@v4  # persist-credentials-ok:   \n", &[(6, K::Empty)]),
    // A marker on a checkout that keeps nothing is stale.
    (
        "      # persist-credentials-ok: release tags\n      - uses: actions/checkout@v4\n        with:\n          persist-credentials: false\n",
        &[(7, K::Stale)],
    ),
    // A marker belongs to the step it is on or directly above -- not the step before, and not
    // shell text in a previous step's `run: |` block.
    (
        "      - uses: actions/checkout@v4\n      # persist-credentials-ok: pushes tags\n      - run: git push --tags\n",
        &[(6, K::Unset)],
    ),
    (
        "      - run: |\n          true\n          # persist-credentials-ok: not a YAML comment\n      - uses: actions/checkout@v4\n",
        &[(9, K::Unset)],
    ),
    // A blank line between the marker and the step breaks "directly above".
    ("      # persist-credentials-ok: tags\n\n      - uses: actions/checkout@v4\n", &[(8, K::Unset)]),
];

/// Steps appended to HEAD that keep no token, or keep it with a reason.
const GREEN: &[&str] = &[
    "      - uses: actions/checkout@v4\n        with:\n          persist-credentials: false\n",
    // Other keys before and after.
    "      - uses: actions/checkout@v4\n        with:\n          fetch-depth: 0\n          persist-credentials: false\n          ref: main\n",
    // Quoted, and other spellings of the same literal.
    "      - uses: actions/checkout@v4\n        with:\n          persist-credentials: 'false'\n",
    "      - uses: actions/checkout@v4\n        with:\n          persist-credentials: \"False\"\n",
    "      - {uses: actions/checkout@v4, with: {persist-credentials: false}}\n",
    // Justified: above the step, on it, inside it, after its keys.
    "      # Tags the release and pushes it.\n      # persist-credentials-ok: the release job pushes its tag\n      - uses: actions/checkout@v4\n",
    "      - uses: actions/checkout@v4 # persist-credentials-ok: pushes the tag\n",
    "      - name: checkout\n        # persist-credentials-ok: commits the regenerated lockfile\n        uses: actions/checkout@v4\n        with:\n          persist-credentials: true\n",
    "      - uses: actions/checkout@v4\n        with:\n          persist-credentials: true\n        # persist-credentials-ok: pushes the tag\n      - run: git push --tags\n",
    // Not checkout: another action's input of the same name, and checkout named in a script.
    "      - uses: some/action@v1\n        with:\n          persist-credentials: true\n",
    "      - run: |\n          echo uses: actions/checkout@v4\n",
    "      - uses: ./.github/actions/checkout\n",
    "      - uses: docker://alpine:3\n",
];

#[test]
fn every_kept_token_is_found_on_its_line() {
    for (steps, want) in RED {
        let src = format!("{HEAD}{steps}");
        assert_eq!(&kinds(&src), want, "{src}");
    }
}

#[test]
fn a_dropped_or_reasoned_token_passes() {
    for steps in GREEN {
        let src = format!("{HEAD}{steps}");
        assert_eq!(kinds(&src), Vec::new(), "{src}");
    }
}

#[test]
fn a_composite_action_is_judged_like_a_workflow() {
    let red = "name: setup\nruns:\n  using: composite\n  steps:\n    - uses: actions/checkout@v4\n";
    assert_eq!(kinds(red), vec![(5, K::Unset)]);
    let green = format!("{red}      with:\n        persist-credentials: false\n");
    assert_eq!(kinds(&green), Vec::new());
}

#[test]
fn an_alias_is_judged_by_what_it_names() {
    let anchors = "x-co: &co\n  persist-credentials: ";
    let tail = "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n        with: *co\n";
    assert_eq!(kinds(&format!("{anchors}false\n{tail}")), Vec::new());
    // The anchor's line is outside the step: the step's own line is named.
    assert_eq!(
        kinds(&format!("{anchors}true\n{tail}")),
        vec![(6, K::NotFalse("true"))]
    );
}

#[test]
fn every_job_is_read() {
    let src = "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n  b:\n    steps:\n    - uses: actions/checkout@v4\n";
    assert_eq!(kinds(src), vec![(4, K::Unset), (7, K::Unset)]);
}

#[test]
fn unreadable_yaml_is_a_finding_not_a_pass() {
    assert_eq!(kinds("jobs: [\n  a: b\n"), vec![(3, K::Unparsable)]);
}

#[test]
fn scope_is_workflows_and_composite_actions() {
    for rel in [
        ".github/workflows/ci.yml",
        ".github/workflows/release.yaml",
        ".github/actions/setup/action.yml",
        ".github/actions/a/b/action.yaml",
        ".github/actions/action.yml",
        ".github/workflows/.hidden.yml",
    ] {
        assert!(in_scope(rel), "{rel}");
    }
    for rel in [
        ".github/workflows/sub/ci.yml",
        ".github/workflows/README.md",
        ".github/workflows/.yml",
        ".github/dependabot.yml",
        ".github/actions/setup/other.yml",
        "action.yml",
        "ci/.github/workflows/ci.yml",
    ] {
        assert!(!in_scope(rel), "{rel}");
    }
}

#[test]
fn a_comment_starts_at_a_free_hash() {
    assert_eq!(comment_of("  # x"), Some("# x"));
    assert_eq!(comment_of("uses: a@v1 # x"), Some("# x"));
    assert_eq!(comment_of("name: \"a # b\" # c"), Some("# c"));
    assert_eq!(comment_of("name: 'a # b'"), None);
    assert_eq!(comment_of("name: Don't # c"), Some("# c"));
    assert_eq!(comment_of("url: a#b"), None);
}
