//! Unit tests for `changelog_tags` (split out for the line cap).

use super::*;

fn tags(t: &[&str]) -> Vec<String> {
    t.iter().map(|s| (*s).to_string()).collect()
}

const GOOD: &str = "# CHANGELOG\n\n## Unreleased\n\n## v1.2.0 -- two\n\n* b\n\n## v1.1.0 — one (2026-11-01)\n\n* a\n";

#[test]
fn a_changelog_that_agrees_with_its_tags_passes() {
    assert_eq!(
        findings("CHANGELOG.md", GOOD, &tags(&["v1.1.0", "v1.2.0"]), None),
        Vec::<String>::new()
    );
    // The pending stanza, its tag not cut yet: VERSION's, or the newest above every tag.
    assert_eq!(
        findings("CHANGELOG.md", GOOD, &tags(&["v1.1.0"]), Some("1.2.0")),
        Vec::<String>::new()
    );
    assert_eq!(
        findings("CHANGELOG.md", GOOD, &tags(&["v1.1.0"]), None),
        Vec::<String>::new()
    );
    // A heading older than the first tag names no tag and is history, not a finding.
    assert_eq!(
        findings("CHANGELOG.md", GOOD, &tags(&["v1.2.0"]), None),
        Vec::<String>::new()
    );
}

#[test]
fn a_planted_orphan_tail_is_found() {
    let text = GOOD.replace("## v1.1.0 — one (2026-11-01)", " — one (2026-11-01)");
    let f = findings("CHANGELOG.md", &text, &tags(&["v1.2.0"]), None);
    assert_eq!(f.len(), 1, "{f:?}");
    assert!(
        f[0].contains("CHANGELOG.md:9:") && f[0].contains("heading tail"),
        "{f:?}"
    );
}

#[test]
fn a_tag_with_no_heading_is_found() {
    let f = findings(
        "CHANGELOG.md",
        GOOD,
        &tags(&["v1.1.0", "v1.1.1", "v1.2.0"]),
        None,
    );
    assert_eq!(
        f,
        vec!["    CHANGELOG.md: tag v1.1.1 has no `## v1.1.1` heading"]
    );
}

#[test]
fn a_heading_no_tag_names_is_found_unless_it_is_current() {
    let text = format!("{GOOD}\n## v1.0.0\n");
    let f = findings("CHANGELOG.md", &text, &tags(&["v1.0.0", "v1.2.0"]), None);
    assert_eq!(f, vec!["    CHANGELOG.md:9: `## v1.1.0` names no tag"]);
    // Two stanzas above every tag: only one is pending.
    let f = findings("CHANGELOG.md", &text, &tags(&["v1.0.0"]), Some("1.2.0"));
    assert_eq!(f, vec!["    CHANGELOG.md:9: `## v1.1.0` names no tag"]);
}

#[test]
fn a_clone_whose_tags_cannot_be_trusted_is_not_judged() {
    let why = untrusted(true, &tags(&["v1.2.0"]), GOOD).unwrap_or_default();
    assert!(
        why.contains("shallow") && why.contains("fetch-depth: 0"),
        "{why}"
    );
    let why = untrusted(false, &tags(&["vnext"]), GOOD).unwrap_or_default();
    assert!(why.contains("no v* tag"), "{why}");
    assert_eq!(untrusted(false, &tags(&["v1.1.0"]), GOOD), None);
    // Before the first release there is nothing to compare: a pass, not a refusal.
    assert_eq!(
        untrusted(false, &[], "# CHANGELOG\n\n## Unreleased\n"),
        None
    );
}

#[test]
fn a_planted_shallow_clone_is_not_judged_and_its_seed_is_not_called_stale() {
    // GOH.SHALLOWROOT: in a --depth 1 clone the tags are the fetch's, so a clean-looking
    // changelog is no verdict. A changelog with an orphan heading tail, tagged, then cloned.
    let r = goh_testkit::repo_with(&[(
        "CHANGELOG.md",
        &GOOD.replace("## v1.1.0 — one (2026-11-01)", " — one (2026-11-01)"),
    )])
    .expect("fixture");
    r.git(&["commit", "-qm", "root"]).expect("commit");
    r.git(&["tag", "v1.2.0"]).expect("tag");
    let full = judge(r.path(), &["CHANGELOG.md".to_string()], false);
    assert_eq!(full.0, 1, "the full clone finds it: {full:?}");
    let dst = tempfile::tempdir().expect("tmp");
    let url = format!("file://{}", r.path().display());
    goh_testkit::git_in(dst.path(), &["clone", "-q", "--depth", "1", &url, "c"])
        .expect("shallow clone");
    let clone = dst.path().join("c");
    assert!(crate::gitutil::is_shallow(&clone));
    let (code, out, err) = judge(&clone, &["CHANGELOG.md".to_string()], false);
    assert_eq!(code, 0, "{out}{err}");
    assert!(
        err.contains("NOT JUDGED") && err.contains("shallow clone") && !out.contains("OK"),
        "{out}{err}"
    );
}

/// A repo whose main has v1.1.0 (heading present) and a LATER commit tagged v1.2.0 with its
/// heading, and a branch `cut` standing at the v1.1.0 commit (GOH.TAGSREACH).
fn branch_cut_before_a_release() -> goh_testkit::Repo {
    let one = "# CHANGELOG\n\n## v1.1.0 -- one (2026-11-01)\n\n* a\n";
    let r = goh_testkit::repo_with(&[("CHANGELOG.md", one)]).expect("fixture");
    r.git(&["commit", "-qm", "one"]).expect("commit one");
    r.git(&["tag", "v1.1.0"]).expect("tag one");
    let two = format!(
        "# CHANGELOG\n\n## v1.2.0 -- two (2026-11-02)\n\n* b\n{}",
        &one[13..]
    );
    r.write("CHANGELOG.md", &two).expect("write");
    r.git(&["commit", "-qam", "two"]).expect("commit two");
    r.git(&["tag", "v1.2.0"]).expect("tag two");
    r.git(&["checkout", "-q", "-b", "cut", "v1.1.0"])
        .expect("branch");
    r
}

#[test]
fn a_tag_on_a_later_main_commit_does_not_fail_a_branch_cut_before_it() {
    // GOH.TAGSREACH: the tag set judged is the commit's own history, not the repository's.
    let r = branch_cut_before_a_release();
    let files = ["CHANGELOG.md".to_string()];
    for staged in [false, true] {
        let (code, out, err) = judge(r.path(), &files, staged);
        assert_eq!(code, 0, "staged={staged}: {out}{err}");
        assert!(
            out.contains("every one of 1 tag has its heading"),
            "only v1.1.0 is reachable: {out}{err}"
        );
    }
}

#[test]
fn a_reachable_tag_with_no_heading_still_fails_on_a_branch() {
    let r = branch_cut_before_a_release();
    r.git(&["tag", "v1.1.1"]).expect("tag at the branch head");
    let (code, out, err) = judge(r.path(), &["CHANGELOG.md".to_string()], false);
    assert_eq!(code, 1, "{out}{err}");
    let all = format!("{out}{err}");
    assert!(
        all.contains("tag v1.1.1 has no `## v1.1.1` heading") && !all.contains("v1.2.0"),
        "{all}"
    );
}

#[test]
fn headings_without_a_v_and_non_release_tags_are_read() {
    let text = "## Unreleased\n\n## 0.8.0 - 2026-10-20\n\n## [0.3.1]\n";
    assert_eq!(
        findings(
            "CHANGELOG.md",
            text,
            &tags(&["v0.3.1", "v0.8.0", "vnext"]),
            None
        ),
        Vec::<String>::new()
    );
}

#[test]
fn a_repo_with_no_commit_has_no_reachable_tag_and_a_failed_listing_is_an_error() {
    use std::os::unix::process::ExitStatusExt;
    // GOH.TAGSREACH: an unborn HEAD has no history to carry a tag.
    let r = goh_testkit::repo_with(&[("CHANGELOG.md", GOOD)]).expect("fixture");
    assert_eq!(super::tags(r.path()).expect("tags"), Vec::<String>::new());
    let failed = std::process::Output {
        status: std::process::ExitStatus::from_raw(256),
        stdout: Vec::new(),
        stderr: Vec::new(),
    };
    assert!(listed(&failed).is_err());
    let ok = std::process::Output {
        status: std::process::ExitStatus::from_raw(0),
        stdout: b"v1.0.0\nv1.1.0\n".to_vec(),
        stderr: Vec::new(),
    };
    assert_eq!(listed(&ok).expect("ok"), tags(&["v1.0.0", "v1.1.0"]));
}
