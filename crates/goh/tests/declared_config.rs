//! A direct `goh <check>` reads the repo's `.gatesrc` the way its structural gate does
//! (BACKLOG 6.17).
//!
//! Measured 2026-10-14 in `remote_dotfiles`: glyphs its `.gatesrc` permits through `GOH_ALLOW` were
//! flagged by `goh emoji` run alone and passed by the structural gate -- two verdicts for one
//! tree, because the standalone `--allow` defaulted to "" rather than to the repo's declaration.
//! The sibling found by grepping every standalone subcommand for a key structural reads:
//! `goh skills` defaulted `--max-words` to the built-in ceiling, ignoring `GOH_SKILLS_MAX_WORDS`.
//! One config read for both entry points; an explicit flag still wins.

// Test-only crate: helpers `expect` fixture writes, and a fixture that
// cannot be built is a broken test, not a result.
#![cfg(test)]

use std::path::Path;

use goh_testkit::{goh_at, repo_with, Out, Repo};

fn goh(repo: &Repo, args: &[&str]) -> Out {
    goh_at(Path::new(env!("CARGO_BIN_EXE_goh")), Some(repo), args, &[]).expect("goh runs")
}

/// A glyph the house list does not permit, built from its code point: the gate reads an escape
/// as the glyph, so this file carries neither.
fn glyph() -> String {
    char::from_u32(0x0001_F642)
        .expect("a valid code point")
        .to_string()
}

fn emoji_repo(allow: &str) -> Repo {
    repo_with(&[
        (".gatesrc", &format!("GOH_ALLOW='{allow}'\n")),
        ("notes.md", &format!("a permitted glyph {}\n", glyph())),
    ])
    .expect("fixture")
}

#[test]
fn goh_emoji_alone_honours_the_repos_goh_allow_as_structural_does() {
    let r = emoji_repo(&glyph());
    let alone = goh(&r, &["emoji"]);
    assert!(alone.status.success(), "{}", alone.text());
    // The structural gate's own verdict on the same tree, the entry point that always read it.
    let gate = goh(&r, &["structural", "--full"]).text();
    assert!(
        gate.contains("no disallowed emoji") && !gate.contains("DISALLOWED EMOJI"),
        "{gate}"
    );
}

#[test]
fn an_explicit_allow_still_wins_over_the_repos() {
    let r = emoji_repo(&glyph());
    let out = goh(&r, &["emoji", "--allow", ""]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
}

#[test]
fn without_a_declaration_the_glyph_is_still_refused() {
    let r = emoji_repo("");
    let out = goh(&r, &["emoji"]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
}

fn skills_repo(max_words: &str) -> Repo {
    let long = "word ".repeat(40);
    let mut files: Vec<(String, String)> = (1..=5)
        .map(|i| {
            (
                format!("s{i}/SKILL.md"),
                format!("---\nname: s{i}\ndescription: d\n---\n\n# s{i}\n\nbody\n"),
            )
        })
        .collect();
    files.push((
        "s6/SKILL.md".into(),
        format!("---\nname: s6\ndescription: d\n---\n\n# s6\n\n{long}\n"),
    ));
    files.push((
        ".gatesrc".into(),
        format!("GOH_SKILLS_MAX_WORDS={max_words}\n"),
    ));
    let refs: Vec<(&str, &str)> = files
        .iter()
        .map(|(a, b)| (a.as_str(), b.as_str()))
        .collect();
    repo_with(&refs).expect("fixture")
}

#[test]
fn goh_skills_alone_reads_the_repos_word_ceiling() {
    let r = skills_repo("20");
    let out = goh(&r, &["skills"]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    let out = goh(&r, &["skills", "--max-words", "5000"]);
    assert!(out.status.success(), "{}", out.text());
}
