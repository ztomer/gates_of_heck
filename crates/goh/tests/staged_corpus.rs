//! The skills-corpus step at `--staged` reads the INDEX, not the working
//! tree (`~/.claude/skills`, 2026-09-23: a clean commit was refused over
//! files other sessions had edited and never staged). Both directions of
//! that class are pinned here, natively, so `cargo llvm-cov` sees the
//! export; `tests/test_skills_corpus_staged_scope.py` drives both
//! pipelines. Its own file because `cli.rs` sits at the line cap.

use goh_testkit::{goh_at, repo_with, Out, Repo};

/// The binary this test run built; the `#[test]` caller unwraps.
fn goh(repo: &Repo, args: &[&str]) -> std::io::Result<Out> {
    goh_at(
        std::path::Path::new(env!("CARGO_BIN_EXE_goh")),
        Some(repo),
        args,
        &[],
    )
}

/// Six valid skills making up the whole repo (the `~/.claude/skills` shape),
/// committed, with the corpus gate opted in. The `#[test]` caller unwraps.
fn committed_corpus() -> Result<Repo, String> {
    let skill = |i: usize| {
        (
            format!("skill-{i}/SKILL.md"),
            format!("---\nname: skill-{i}\ndescription: d\n---\n\n# S\n\nbody\n"),
        )
    };
    let files: Vec<(String, String)> = (0..6).map(skill).collect();
    let mut refs: Vec<(&str, &str)> = files
        .iter()
        .map(|(p, c)| (p.as_str(), c.as_str()))
        .collect();
    refs.push((".gatesrc", "GOH_MAX_LINES=500\nGOH_SKILLS_CORPUS=1\n"));
    let r = repo_with(&refs)?;
    r.git(&["commit", "-qm", "corpus"])?;
    Ok(r)
}

const DANGLING: &str = "---\nname: skill-1\ndescription: d\n---\n\nSee [[no-such-skill]].\n";
const FIXED: &str = "---\nname: skill-1\ndescription: d\n---\n\nfixed\n";

#[test]
fn the_staged_corpus_gate_ignores_an_unstaged_violation() {
    // ~/.claude/skills, 2026-09-23: a clean commit refused over files other
    // sessions had edited and never staged. The commit is what is judged.
    let r = committed_corpus().expect("fixture");
    r.write(
        "skill-0/SKILL.md",
        "---\nname: skill-0\ndescription: d\n---\n\nmore\n",
    )
    .expect("write");
    r.git(&["add", "skill-0/SKILL.md"]).expect("git");
    r.write("skill-1/SKILL.md", DANGLING).expect("write");
    let out = goh(&r, &["structural", "--staged"]).expect("goh runs");
    assert!(out.status.success(), "{}", out.text());
    assert!(out.stdout.contains("skills corpus"), "{}", out.text());
    // --full still reads the tree: the violation is real, only unstaged.
    let out = goh(&r, &["structural", "--full"]).expect("goh runs");
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out.text().contains("no-such-skill"), "{}", out.text());
}

#[test]
fn the_staged_corpus_gate_fails_a_staged_violation_whose_fix_is_unstaged() {
    // The reverse direction: reading the tree PASSES a commit that records
    // the violation, because the fix never reached the index.
    let r = committed_corpus().expect("fixture");
    r.write("skill-1/SKILL.md", DANGLING).expect("write");
    r.git(&["add", "skill-1/SKILL.md"]).expect("git");
    r.write("skill-1/SKILL.md", FIXED).expect("write");
    let out = goh(&r, &["structural", "--staged"]).expect("goh runs");
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out.text().contains("no-such-skill"), "{}", out.text());
    let out = goh(&r, &["structural", "--full"]).expect("goh runs");
    assert!(out.status.success(), "{}", out.text());
}

#[test]
fn a_corpus_outside_the_repo_is_skipped_by_name_at_staged_and_read_at_full() {
    // gates_of_heck's own wiring of ~/.claude/skills: no part of the commit,
    // so at --staged a violation there could only ever be a false red.
    let outside = committed_corpus().expect("fixture");
    outside.write("skill-1/SKILL.md", DANGLING).expect("write");
    let r = repo_with(&[(
        ".gatesrc",
        &format!(
            "GOH_MAX_LINES=500\nGOH_SKILLS_CORPUS=1\nGOH_SKILLS_ROOT={}\n",
            outside.path().display()
        ),
    )])
    .expect("fixture");
    let out = goh(&r, &["structural", "--staged"]).expect("goh runs");
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.stderr.contains("NOT checked at --staged"),
        "{}",
        out.text()
    );
    let out = goh(&r, &["structural", "--full"]).expect("goh runs");
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
}
