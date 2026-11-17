//! Within the grace, a check that holds a new shape back SHOWS what the grace's end turns red
//! (GOH.GRACESHAPES): a warning naming each site and the day, the verdict and the exit unchanged.
//! Until then a consumer saw nothing from the new shapes, so it could neither fix nor seed them
//! before they turned on. Each test is one `crate::grace::over()` caller, run at `GOH_GRACE=within`
//! (the warning, exit 0) and at `over` (the same site, red).

// Test-only crate: helpers `expect` fixture writes, and a fixture that
// cannot be built is a broken test, not a result.
#![cfg(test)]

use goh_testkit::{goh_at, repo_with, Out, Repo};

/// The day the new shapes are judged, as the warning names it.
const DAY: &str = "2026-11-23";

fn goh(repo: &Repo, args: &[&str], store: &std::path::Path, grace: &str) -> Out {
    goh_at(
        std::path::Path::new(env!("CARGO_BIN_EXE_goh")),
        Some(repo),
        args,
        &[
            ("GOH_SEED_DIR", store.to_str().expect("utf-8")),
            ("GOH_GRACE", grace),
        ],
    )
    .expect("goh runs")
}

/// A fixture line with its `@` spelled as the hedge the scanner looks for, so this file carries none.
fn hedged(line: &str) -> String {
    line.replace('@', "(s)")
}

/// `args` over `files`: within the grace it passes and shows `site` as a warning naming the day;
/// from the day the same site is red.
fn shows_ahead(args: &[&str], files: &[(&str, &str)], site: &str) {
    let store = tempfile::tempdir().expect("store");
    let r = repo_with(files).expect("fixture");
    let within = goh(&r, args, store.path(), "within");
    assert!(
        within.status.success(),
        "{args:?} within the grace: exit unchanged\n{}",
        within.text()
    );
    let text = within.text();
    assert!(
        text.contains(site) && text.contains(DAY),
        "{args:?} within the grace names {site} and {DAY}:\n{text}"
    );
    let over = goh(&r, args, store.path(), "over");
    assert!(
        !over.status.success() && over.text().contains(site),
        "{args:?} from the day {site} is red:\n{}",
        over.text()
    );
}

#[test]
fn bare_open_shell_shape_is_shown_within_the_grace() {
    shows_ahead(
        &["bare-open"],
        &[("ob.sh", "#!/bin/sh\nenv FOO=1 open -a \"$APP\"\n")],
        "ob.sh:2:",
    );
}

#[test]
fn bare_open_python_shape_is_shown_within_the_grace() {
    shows_ahead(
        &["bare-open"],
        &[(
            "ob.py",
            "import subprocess\nsubprocess.run([\"open\", \"-a\", APP])\n",
        )],
        "ob.py:2:",
    );
}

#[test]
fn plural_hedge_continuation_is_shown_within_the_grace() {
    shows_ahead(
        &["plural-hedge"],
        &[(
            "ph.py",
            &hedged("MSG = \"first \\\n    2 file@ wrong\"\nx = 1\n"),
        )],
        "ph.py:2:",
    );
}

#[test]
fn release_quiet_callee_is_shown_within_the_grace() {
    shows_ahead(
        &["release-quiet"],
        &[
            ("tools/release.sh", "#!/bin/bash\n./bin/ab_test || exit 1\n"),
            ("bin/ab_test", "#!/bin/bash\ncargo test >/dev/null\n"),
        ],
        "bin/ab_test:2:",
    );
}

#[test]
fn git_deadline_is_shown_within_the_grace() {
    shows_ahead(
        &["git-deadline"],
        &[("gd.sh", "#!/bin/sh\ngit status\n")],
        "gd.sh:2:",
    );
}

#[test]
fn tui_marks_are_shown_within_the_grace() {
    shows_ahead(
        &["tui-marks"],
        &[("tm.sh", "#!/bin/bash\necho \"✗ adb not found\"\n")],
        "tm.sh:2:",
    );
}

#[test]
fn layer_parity_is_shown_within_the_grace() {
    shows_ahead(
        &["layer-parity"],
        &[("a.py", "x = 1\n")],
        "tools/gate.sh: no python layer",
    );
}

#[test]
fn dead_port_is_shown_within_the_grace() {
    shows_ahead(
        &["dead-port"],
        &[(
            "test_dp.py",
            &format!("def test_x():\n    c = connect(\"localhost:{}\")\n", 54_321),
        )],
        "test_dp.py:2:",
    );
}

#[test]
fn moving_tags_release_line_and_script_are_shown_within_the_grace() {
    shows_ahead(
        &["moving-tags"],
        &[("Dockerfile", "FROM rust:1\n")],
        "Dockerfile:1:",
    );
    shows_ahead(
        &["moving-tags"],
        &[("mt.sh", "#!/bin/sh\ndocker run rust:1\n")],
        "mt.sh:2:",
    );
}

#[test]
fn roadmap_bold_entry_is_shown_within_the_grace() {
    shows_ahead(
        &["roadmap"],
        &[
            (".gatesrc", &goh_testkit::gatesrc("GOH_ROADMAP=ROADMAP.md\n")),
            (
                "ROADMAP.md",
                "# Roadmap\n\n## Phase 1 — now\n\n**KB.HYG1 — a bold entry.** Text with no clause.\n",
            ),
        ],
        "ROADMAP.md:5:",
    );
}

/// A site the grace already judges is not "ahead": it is red today, and says so once.
#[test]
fn a_site_the_grace_already_judges_is_not_shown_as_ahead() {
    let store = tempfile::tempdir().expect("store");
    let r = repo_with(&[("old.sh", "#!/bin/sh\nx || open -g Foo.app\n")]).expect("fixture");
    let out = goh(&r, &["bare-open"], store.path(), "within");
    assert!(!out.status.success(), "{}", out.text());
    assert!(!out.text().contains(DAY), "{}", out.text());
}

/// A site the consumer already seeded will warn, not turn red, so nothing is shown for it.
#[test]
fn a_seeded_site_is_not_shown_as_ahead() {
    let store = tempfile::tempdir().expect("store");
    let r = repo_with(&[("ob.sh", "#!/bin/sh\nenv FOO=1 open -a \"$APP\"\n")]).expect("fixture");
    r.git(&["commit", "-q", "-m", "root"]).expect("commit");
    let root = goh_testkit::git_command()
        .args(["rev-list", "--max-parents=0", "HEAD"])
        .current_dir(r.path())
        .output()
        .expect("git");
    let root = String::from_utf8_lossy(&root.stdout).trim().to_string();
    std::fs::write(
        store.path().join("bare_open_seed.json"),
        format!(
            "{{\"why\": \"w\", \"entries\": [{{\"root\": \"{root}\", \"repo\": \"r\", \"path\": \
             \"ob.sh\", \"count\": 1, \"reason\": \"reviewed: a test\"}}]}}\n"
        ),
    )
    .expect("seed");
    let out = goh(&r, &["bare-open"], store.path(), "within");
    assert!(out.status.success(), "{}", out.text());
    assert!(!out.text().contains(DAY), "{}", out.text());
}
