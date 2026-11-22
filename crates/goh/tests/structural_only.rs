//! `goh structural --only STEP [--only STEP]...` (GOH.SELFWALKFILTER): a run narrowed to the named steps,
//! so a caller that judges every step (the self-walk) can split one serial run into parts a
//! parallel runner spreads. A name the binary does not carry is refused by name, never a run of
//! nothing that reads as a pass.

// Test-only crate: a fixture that cannot be built is a broken test, not a result.
#![cfg(test)]

use goh_testkit::{goh_at, repo_with, Out, Repo};

fn goh(repo: &Repo, args: &[&str]) -> Out {
    goh_at(
        std::path::Path::new(env!("CARGO_BIN_EXE_goh")),
        Some(repo),
        args,
        &[],
    )
    .expect("goh runs")
}

/// A repo with a conflict marker AND an over-long file: two steps, each red on its own.
fn two_reds() -> Repo {
    repo_with(&[
        (".gatesrc", "GOH_MAX_LINES=3\n"),
        ("conflict.txt", "<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n"),
        ("long.py", "1\n2\n3\n4\n5\n"),
    ])
    .expect("fixture")
}

/// The steps a run announced, by their `· label (<=Ns)` line.
fn announced(out: &Out) -> Vec<String> {
    out.stdout
        .lines()
        .filter_map(|l| l.strip_prefix("· "))
        .filter_map(|l| l.rsplit_once(" (").map(|(label, _)| label.to_owned()))
        .collect()
}

#[test]
fn only_runs_the_named_step_and_no_other() {
    let r = two_reds();
    let out = goh(&r, &["structural", "--full", "--only", "file length"]);
    assert_eq!(announced(&out), ["file length <= 3"], "{}", out.text());
    assert!(!out.status.success(), "{}", out.text());
    assert!(out.text().contains("long.py"), "{}", out.text());
    assert!(!out.text().contains("conflict.txt"), "{}", out.text());
}

#[test]
fn only_repeats_for_more_steps_and_keeps_the_pipeline_order() {
    let r = two_reds();
    let out = goh(
        &r,
        &[
            "structural",
            "--full",
            "--keep-going",
            "--only",
            "file length",
            "--only",
            "no conflict markers",
        ],
    );
    assert_eq!(
        announced(&out),
        ["no conflict markers", "file length <= 3"],
        "{}",
        out.text()
    );
    assert!(out.stderr.contains("2 steps failed"), "{}", out.text());
}

#[test]
fn only_a_green_step_passes_though_another_step_is_red() {
    let r = two_reds();
    let out = goh(
        &r,
        &["structural", "--full", "--only", "no committed secrets"],
    );
    assert!(out.status.success(), "{}", out.text());
    assert_eq!(announced(&out), ["no committed secrets"], "{}", out.text());
}

#[test]
fn only_refuses_a_step_the_binary_does_not_carry_by_name() {
    let r = two_reds();
    let out = goh(
        &r,
        &[
            "structural",
            "--full",
            "--only",
            "file length",
            "--only",
            "no such step",
        ],
    );
    assert_eq!(out.status.code(), Some(2), "{}", out.text());
    assert!(out.stderr.contains("no such step"), "{}", out.text());
    assert!(out.stderr.contains("--list-steps"), "{}", out.text());
    assert!(
        announced(&out).is_empty(),
        "a refused run ran: {}",
        out.text()
    );
}

#[test]
fn a_step_whose_name_holds_a_comma_is_one_step() {
    let r = two_reds();
    let name = "no temp, lock or stage path in a work tree";
    let out = goh(&r, &["structural", "--full", "--only", name]);
    assert_ne!(out.status.code(), Some(2), "refused: {}", out.text());
    assert!(
        announced(&out).iter().all(|l| l.starts_with(name)),
        "{}",
        out.text()
    );
}
