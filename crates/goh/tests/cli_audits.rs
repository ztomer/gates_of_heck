//! The whole-tree audits -- `lints`, `skills` and `ceiling` -- end to end:
//! each verdict the reference checker can print (pass, violation, skip,
//! refusal), the baseline ratchets both ways, and every usage error.
//! Driven as a process so `cargo llvm-cov` sees `commands::run_*`.

// Test-only crate: helpers `expect` fixture writes, and a fixture that
// cannot be built is a broken test, not a result.
#![cfg(test)]

use std::path::Path;

use goh_testkit::{goh_at, repo_with, Out, Repo};

fn goh(repo: Option<&Repo>, args: &[&str]) -> Out {
    goh_at(Path::new(env!("CARGO_BIN_EXE_goh")), repo, args, &[]).expect("goh runs")
}

// --------------------------------------------------------------------- lints

const WORKSPACE: &str = "[workspace]\nmembers = [\"a\", \"b\", \"c\", \"missing\", \"d\"]\nexclude = [\"d\"]\n\n[workspace.lints.clippy]\nall = \"warn\"\n";
const INHERITS: &str = "[package]\nname = \"m\"\n\n[lints]\nworkspace = true\n";

#[test]
fn lints_names_each_member_that_inherits_nothing() {
    let r = repo_with(&[
        ("Cargo.toml", WORKSPACE),
        ("a/Cargo.toml", INHERITS),
        ("b/Cargo.toml", "[package]\nname = \"b\"\n"),
        (
            "c/Cargo.toml",
            "[package]\nname = \"c\"\n\n[lints.clippy]\nall = \"warn\"\n",
        ),
        ("d/Cargo.toml", "[package]\nname = \"d\"\n"),
        // Build output and reference trees are never audited.
        (
            "target/x/Cargo.toml",
            "[workspace.lints]\nmembers = [\"zz\"]\n",
        ),
    ])
    .expect("fixture");
    let out = goh(Some(&r), &["lints"]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out
        .stderr
        .contains("2 crate(s) not subject to the workspace lint policy"));
    assert!(
        out.stderr.contains("b/Cargo.toml: no [lints] table"),
        "{}",
        out.stderr
    );
    assert!(
        out.stderr
            .contains("c/Cargo.toml: has its own [lints] table but does not inherit"),
        "{}",
        out.stderr
    );
    assert!(
        !out.stderr.contains("d/Cargo.toml"),
        "excluded: {}",
        out.stderr
    );
    assert!(
        out.stdout.contains("workspace = true"),
        "the fix: {}",
        out.stdout
    );
    // Every member inherits: green, with the count.
    r.write("b/Cargo.toml", INHERITS).expect("write");
    r.write("c/Cargo.toml", INHERITS).expect("write");
    let out = goh(Some(&r), &["lints"]);
    assert!(out.status.success(), "{}", out.text());
    assert_eq!(
        out.stdout,
        "✓ [lints_optin] OK — 3 workspace member(s) inherit the declared policy\n"
    );
}

#[test]
fn lints_skips_by_name_when_no_workspace_declares_a_policy() {
    let r = repo_with(&[("Cargo.toml", "[package]\nname = \"x\"\n")]).expect("fixture");
    let out = goh(Some(&r), &["lints"]);
    assert!(out.status.success(), "{}", out.text());
    assert!(out
        .stdout
        .contains("no workspace declares [workspace.lints]"));
}

#[test]
fn lints_reports_an_out_of_tree_member_and_skips_unreadable_dirs() {
    use std::os::unix::fs::PermissionsExt as _;
    let outside = tempfile::tempdir().expect("tempdir");
    std::fs::write(
        outside.path().join("Cargo.toml"),
        "[package]\nname = \"o\"\n",
    )
    .expect("write");
    let member = outside.path().to_string_lossy().into_owned();
    let r = repo_with(&[(
        "Cargo.toml",
        &format!("[workspace]\nmembers = [\"{member}\"]\n[workspace.lints.rust]\nx = \"deny\"\n"),
    )])
    .expect("fixture");
    let locked = r.path().join("locked");
    std::fs::create_dir(&locked).expect("mkdir");
    std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o000)).expect("chmod");
    let out = goh(Some(&r), &["lints"]);
    std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o755)).expect("chmod");
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(
        out.stderr
            .contains(&format!("{member}/Cargo.toml: no [lints] table")),
        "an out-of-tree member is named by its full path: {}",
        out.stderr
    );
}

// -------------------------------------------------------------------- skills

fn skill(root: &Path, name: &str, body: &str) {
    let dir = root.join(name);
    std::fs::create_dir_all(&dir).expect("mkdir");
    std::fs::write(dir.join("SKILL.md"), body).expect("write");
}

fn valid(name: &str) -> String {
    format!("---\nname: {name}\ndescription: d\n---\n\n# {name}\n\nbody\n")
}

/// Five valid skills: exactly the default floor.
fn corpus() -> tempfile::TempDir {
    let dir = tempfile::tempdir().expect("tempdir");
    for i in 1..=5 {
        skill(dir.path(), &format!("s{i}"), &valid(&format!("s{i}")));
    }
    // A dot-dir is never a skill, even holding a SKILL.md.
    skill(dir.path(), ".hidden", &valid(".hidden"));
    dir
}

fn skills(root: &Path, extra: &[&str]) -> Out {
    let root = root.to_string_lossy().into_owned();
    let mut args = vec!["skills", "--root", root.as_str()];
    args.extend_from_slice(extra);
    goh(None, &args)
}

#[test]
fn skills_passes_a_valid_corpus_and_refuses_a_wrong_scope() {
    let c = corpus();
    let out = skills(c.path(), &[]);
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.stdout
            .contains("[skills_corpus] OK — 5 skills, 5 files"),
        "{}",
        out.stdout
    );
    let out = skills(c.path(), &["--min-skills", "6"]);
    assert_eq!(out.status.code(), Some(2), "{}", out.text());
    assert!(out.stdout.contains("only 5 skill(s)"), "{}", out.stdout);
    assert!(out.stdout.contains("(floor 6)"), "{}", out.stdout);
    let missing = c.path().join("nope");
    let out = skills(&missing, &[]);
    assert_eq!(out.status.code(), Some(2), "{}", out.text());
    assert!(out.stdout.contains("is not a directory"), "{}", out.stdout);
    // The default root is the current directory.
    let r = Repo::bare(c);
    let out = goh(Some(&r), &["skills"]);
    assert!(out.status.success(), "{}", out.text());
}

#[test]
fn skills_names_every_frontmatter_link_and_duplicate_defect() {
    let c = corpus();
    let lesson = "## A lesson heading long enough to be one\n";
    skill(c.path(), "s1", "no frontmatter at all\n");
    skill(
        c.path(),
        "s2",
        &format!("---\nname: other\n---\n\nsee [[ghost]] [[ghost]] and [r](references/gone.md)\n\n{lesson}"),
    );
    skill(
        c.path(),
        "s3",
        &format!("---\ndescription: d\n---\n\n{lesson}## Related\n"),
    );
    std::fs::create_dir_all(c.path().join("s4/references")).expect("mkdir");
    std::fs::write(
        c.path().join("s4/references/r.md"),
        "[[s5]] and [[nobody]]\n",
    )
    .expect("write");
    std::fs::write(c.path().join("s4/references/notes.txt"), "[[ignored]]\n").expect("write");
    skill(c.path(), "s5", &format!("{}## Related\n", valid("s5")));
    let out = skills(c.path(), &[]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    for line in [
        "s1/SKILL.md: no YAML frontmatter — this skill can never be triggered",
        "s2/SKILL.md: name `other` does not match directory `s2`",
        "s2/SKILL.md: frontmatter has no `description:`",
        "s2/SKILL.md: [[ghost]] matches no skill",
        "s2/SKILL.md: link to references/gone.md — file does not exist",
        "s3/SKILL.md: frontmatter has no `name:`",
        "s4/references/r.md: [[nobody]] matches no skill",
        "section \"a lesson heading long enough to be one\" appears in 2 skills (s2, s3)",
        "[skills_corpus] 8 violation(s) across 5 skills, 6 files",
    ] {
        assert!(
            out.stdout.contains(line),
            "missing {line:?} in\n{}",
            out.stdout
        );
    }
    // A short structural heading shared by two skills is a label, not a lesson.
    assert!(!out.stdout.contains("\"related\""), "{}", out.stdout);
    assert_eq!(
        out.stdout.matches("[[ghost]]").count(),
        1,
        "deduped per file"
    );
}

#[test]
fn skills_size_ratchet_seeds_holds_grows_and_goes_stale() {
    let c = corpus();
    let words = |n: usize| format!("{}\n", vec!["word"; n].join(" "));
    skill(c.path(), "s1", &format!("{}{}", valid("s1"), words(30)));
    let out = skills(c.path(), &["--max-words", "20"]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(
        out.stdout.contains("s1/SKILL.md: 39 words > ceiling 20"),
        "{}",
        out.stdout
    );
    // Re-record: the new entry is honestly unreviewed.
    let out = skills(c.path(), &["--max-words", "20", "--update-baseline"]);
    assert!(out.status.success(), "{}", out.text());
    assert!(out
        .stdout
        .contains("baseline re-recorded: 1 oversized skill(s)"));
    let recorded = std::fs::read_to_string(c.path().join("skills_size_baseline.json"))
        .expect("baseline written beside the root");
    let doc: serde_json::Value = serde_json::from_str(&recorded).expect("json");
    assert_eq!(doc["oversized"]["s1"]["words"], 39);
    assert_eq!(doc["oversized"]["s1"]["reason"], "unreviewed");
    // Held at the baselined size.
    let out = skills(c.path(), &["--max-words", "20"]);
    assert!(out.status.success(), "{}", out.text());
    assert!(out
        .stdout
        .contains("largest SKILL.md 39 words (ceiling 20)"));
    // Growth is refused: the ratchet only shrinks.
    skill(c.path(), "s1", &format!("{}{}", valid("s1"), words(40)));
    let out = skills(c.path(), &["--max-words", "20"]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(
        out.stdout.contains("49 words, up from a baselined 39"),
        "{}",
        out.stdout
    );
    // Under the ceiling with a live entry: stale, delete it.
    let out = skills(c.path(), &["--max-words", "100"]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(
        out.stdout.contains("still baselined as oversized"),
        "{}",
        out.stdout
    );
}

#[test]
fn skills_explicit_baseline_keeps_seeded_reasons_and_refuses_garbage() {
    let c = corpus();
    skill(
        c.path(),
        "s2",
        &format!("{}{}\n", valid("s2"), vec!["w"; 30].join(" ")),
    );
    let baseline = c.path().join("elsewhere.json");
    let path = baseline.to_string_lossy().into_owned();
    std::fs::write(&baseline, "{not json").expect("write");
    let out = skills(c.path(), &["--baseline", &path]);
    assert_eq!(out.status.code(), Some(2), "{}", out.text());
    assert!(out.stdout.contains("is unreadable"), "{}", out.stdout);
    std::fs::write(
        &baseline,
        r#"{"oversized": {"s2": {"words": 999, "reason": "seeded on purpose"}, "gone": {"reason": "no words"}}}"#,
    )
    .expect("write");
    let out = skills(c.path(), &["--baseline", &path, "--max-words", "20"]);
    assert!(out.status.success(), "{}", out.text());
    let out = skills(
        c.path(),
        &[
            "--baseline",
            &path,
            "--max-words",
            "20",
            "--update-baseline",
        ],
    );
    assert!(out.status.success(), "{}", out.text());
    let doc: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&baseline).expect("read")).expect("json");
    assert_eq!(doc["oversized"]["s2"]["reason"], "seeded on purpose");
    assert_eq!(doc["oversized"]["s2"]["words"], 39);
    assert!(doc["oversized"].get("gone").is_none(), "{doc}");
}

// ------------------------------------------------------------------- ceiling

fn ceiling_repo() -> Repo {
    let r = repo_with(&[
        ("src/big.rs", &"l\n".repeat(20)),
        ("src/small.rs", "l\n"),
        ("gen/g.rs", &"l\n".repeat(30)),
        ("base.txt", "# ceilings\n20\tsrc/big.rs\n"),
    ])
    .expect("fixture");
    r.git(&["commit", "-qm", "x"]).expect("git");
    r
}

#[test]
fn ceiling_passes_bound_exemptions_and_waivers() {
    let r = ceiling_repo();
    // Nothing configured: nothing to check, nothing printed.
    let out = goh(Some(&r), &["ceiling"]);
    assert!(out.status.success(), "{}", out.text());
    assert!(out.text().is_empty(), "{}", out.text());
    let out = goh(
        Some(&r),
        &[
            "ceiling",
            "--max",
            "10",
            "--line-exclude",
            "src/",
            "--baseline",
            "base.txt",
        ],
    );
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.stdout.contains("[ceiling] OK — 2 exempt file(s)"),
        "{}",
        out.stdout
    );
    assert!(out
        .stdout
        .contains("[ratchet] OK — 1 entry within ceilings"));
    let out = goh(
        Some(&r),
        &[
            "ceiling",
            "--max",
            "10",
            "--line-exclude",
            "src/|gen/",
            "--unbounded",
            "gen/",
            "--baseline",
            "base.txt",
        ],
    );
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.stdout.contains("1 waived as unbounded by nature"),
        "{}",
        out.stdout
    );
    let out = goh(
        Some(&r),
        &["ceiling", "--max", "10", "--baseline", "base.txt"],
    );
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.stdout.contains("no GOH_LINE_EXCLUDE entries"),
        "{}",
        out.stdout
    );
    // An exemption with no baseline binding it is bounded by nothing: say so.
    let out = goh(
        Some(&r),
        &["ceiling", "--max", "10", "--line-exclude", "src/"],
    );
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.stderr.contains("is bounded by nothing"),
        "{}",
        out.text()
    );
}

#[test]
fn ceiling_refuses_unbounded_growth_and_stale_patterns() {
    let r = ceiling_repo();
    let run = |extra: &[&str]| {
        let mut args = vec!["ceiling", "--max", "10", "--baseline", "base.txt"];
        args.extend_from_slice(extra);
        goh(Some(&r), &args)
    };
    let out = run(&["--line-exclude", "gen/"]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out
        .stderr
        .contains("1 file(s) exempt from the cap with NO ceiling"));
    assert!(out.stdout.contains("gen/g.rs (30 lines)"), "{}", out.stdout);
    assert!(
        out.stdout.contains("add '30 gen/g.rs' to base.txt"),
        "{}",
        out.stdout
    );
    let out = run(&["--line-exclude", "src/big", "--unbounded", "nothing/"]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out
        .stderr
        .contains("GOH_LINE_UNBOUNDED matched 0 tracked files"));
    let out = run(&["--line-exclude", "ghost/"]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out
        .stderr
        .contains("GOH_LINE_EXCLUDE matched 0 tracked files"));
    for (extra, name) in [
        (vec!["--line-exclude", "("], "GOH_LINE_EXCLUDE"),
        (
            vec!["--line-exclude", "src/", "--unbounded", "("],
            "GOH_LINE_UNBOUNDED",
        ),
    ] {
        let out = run(&extra);
        assert_eq!(out.status.code(), Some(2), "{}", out.text());
        assert!(
            out.stderr.contains(&format!("bad {name} regex")),
            "{}",
            out.text()
        );
    }
    // The ratchet half: growth past a recorded ceiling.
    r.write("src/big.rs", &"l\n".repeat(25)).expect("write");
    let out = run(&["--line-exclude", "src/big"]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(
        out.stderr.contains("src/big.rs: 20 -> 25"),
        "{}",
        out.text()
    );
}

#[test]
fn ceiling_baseline_preconditions() {
    let r = ceiling_repo();
    let out = goh(
        Some(&r),
        &[
            "ceiling",
            "--max",
            "10",
            "--line-exclude",
            "src/",
            "--baseline",
            "nope.txt",
        ],
    );
    assert_eq!(out.status.code(), Some(2), "{}", out.text());
    assert!(
        out.stderr.contains("baseline nope.txt not found"),
        "{}",
        out.text()
    );
    // A ratchet baseline that does not parse is a precondition, not a pass.
    r.write("bad.txt", "abc\tsrc/big.rs\n").expect("write");
    let out = goh(Some(&r), &["ceiling", "--baseline", "bad.txt"]);
    assert_eq!(out.status.code(), Some(2), "{}", out.text());
    assert!(
        out.stderr.contains("[ratchet] precondition missing"),
        "{}",
        out.text()
    );
    // A JSON baseline that does not parse carries no ceilings at all.
    r.write("bad.json", "{broken").expect("write");
    let out = goh(
        Some(&r),
        &[
            "ceiling",
            "--max",
            "10",
            "--line-exclude",
            "src/big",
            "--baseline",
            "bad.json",
        ],
    );
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out.stderr.contains("with NO ceiling"), "{}", out.text());
    // Without --baseline there is no ratchet half to run.
    let out = goh(Some(&r), &["ceiling", "--max", "10"]);
    assert!(out.status.success(), "{}", out.text());
}
