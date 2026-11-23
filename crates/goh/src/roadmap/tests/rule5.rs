//! Rule 5: a dead roadmap id (GOH.ROADMAPIDS).

use crate::roadmap::judge;
use crate::roadmap::scan::Rule;
use std::collections::BTreeMap;

// ── rule 5: a dead roadmap id (GOH.ROADMAPIDS) ─────────────────────────────────────────────────

#[test]
fn rule_5_a_citation_names_its_words_and_an_id_shaped_id() {
    use crate::roadmap::cite;
    let words = cite::words("docs/BACKLOG.md");
    assert_eq!(words, ["roadmap", "backlog"]);
    let re = cite::pattern(&words);
    let text = concat!(
        "closes ROADMAP G6\n",                       // roadmap-ok: the fixture
        "(backlog 6.2) and `docs/BACKLOG.md` 4.3\n", // roadmap-ok: the fixture
        "roadmap item RT.ONE\n",                     // roadmap-ok: the fixture
        "the roadmap Rule, ROADMAP Phase L\n",
        "GOH_ROADMAP=docs\nroadmap_seed.json P1\n",
        "ROADMAP X9 roadmap-ok: quoted on purpose\n", // roadmap-ok: the fixture
    );
    let got: Vec<_> = cite::citations(text, &re)
        .into_iter()
        .map(|(line, _, id)| (line, id))
        .collect();
    assert_eq!(
        got,
        [
            (1, "G6".to_owned()),
            (2, "6.2".to_owned()),
            (2, "4.3".to_owned()),
            (3, "RT.ONE".to_owned())
        ]
    );
}

#[test]
fn rule_5_a_roadmap_holds_every_id_an_entry_ruling_row_or_bold_line_opens_with() {
    let held = crate::roadmap::cite::held(super::CLEAN);
    for id in ["R1", "RT.WINCLOSE", "P0.4", "AU.WAF", "H1"] {
        assert!(held.contains(id), "{id}: {held:?}");
    }
    assert!(!held.contains("OW.2"), "a queue citation is not an entry");
    let table = crate::roadmap::cite::held("| G6 | a row |\n|---|---|\n**KB.12 Bold.** text\n");
    assert!(!table.contains("X9"));
    assert!(table.contains("G6") && table.contains("KB.12"), "{table:?}");
}

#[test]
fn rule_5_reads_no_history_vendor_roadmap_or_seed() {
    use crate::roadmap::cite::read_here;
    for skipped in [
        "docs/BACKLOG.md",
        "roadmap_seed.json",
        "CHANGELOG.md",
        "changelog/GOH.X.md",
        "vendor/x.py",
        "a/third_party/b.rs",
    ] {
        assert!(!read_here(skipped, "docs/BACKLOG.md"), "{skipped}");
    }
    assert!(read_here(".github/workflows/ci.yml", "docs/BACKLOG.md"));
}

/// A repo whose roadmap holds `RT.ONE` and whose workflow cites it and a pruned id.
fn citing_repo() -> goh_testkit::Repo {
    let cite = ["# closes ROADMAP G6\n", "# see roadmap RT.ONE\n"].concat(); // roadmap-ok: fixture
    let files = [
        (
            "ROADMAP.md",
            "# Roadmap\n\n## Phase 1\n\n- RT.ONE a thing. Done when: a test.\n",
        ),
        ("ci.yml", cite.as_str()),
        ("CHANGELOG.md", "- closes ROADMAP G6\n"), // roadmap-ok: fixture
        ("bin.dat", "ROADMAP G7\0binary\n"),       // roadmap-ok: fixture
    ];
    goh_testkit::repo_with(&files).unwrap_or_else(|e| panic!("fixture: {e}"))
}

#[test]
fn rule_5_gathers_a_dead_citation_from_the_tree_at_both_scopes() {
    let r = citing_repo();
    let declared = crate::roadmap::Declared::File("ROADMAP.md".to_owned());
    for staged in [false, true] {
        let tree = crate::gitutil::listed_files(r.path(), false).unwrap_or_default();
        let g = crate::roadmap::gather(
            r.path(),
            &tree,
            None,
            &declared,
            (staged, true, true),
            || Ok(BTreeMap::new()),
        );
        let keys: Vec<&String> = g.found.keys().collect();
        assert_eq!(keys, ["ci.yml#dead-id"], "staged={staged}: {:?}", g.found);
        assert!(g.found["ci.yml#dead-id"][0].starts_with("ci.yml:1: ROADMAP G6:")); // roadmap-ok: fixture
        let off = crate::roadmap::gather(
            r.path(),
            &tree,
            None,
            &declared,
            (staged, true, false),
            || Ok(BTreeMap::new()),
        );
        assert!(
            off.found.is_empty(),
            "rule 5 off reads nothing: {:?}",
            off.found
        );
    }
}

#[test]
fn rule_5_seeds_within_its_grace_above_heads_seed() {
    let r = citing_repo();
    let cfg = crate::gatesrc::Gatesrc {
        roadmap: Some("ROADMAP.md".to_owned()),
        ..Default::default()
    };
    assert_eq!(crate::roadmap::write_seed(r.path(), &cfg, None), 0);
    let text =
        std::fs::read_to_string(r.path().join(crate::roadmap::SEED_FILE)).unwrap_or_default();
    let seed = judge::parse_seed(&text).unwrap_or_default();
    assert_eq!(seed.get("ci.yml#dead-id"), Some(&1), "{text}");
    // a seed that exists is written again: the key may stand within the grace, never rise after
    assert_eq!(crate::roadmap::write_seed(r.path(), &cfg, None), 0);
}

#[test]
fn rule_5_keys_may_rise_against_head_only_within_the_grace() {
    let head = BTreeMap::from([("a.py#no-id".to_owned(), 1)]);
    let now = BTreeMap::from([
        ("a.py#no-id".to_owned(), 2),
        ("ci.yml#dead-id".to_owned(), 3),
    ]);
    let within = crate::roadmap::graced(head.clone(), &now, false);
    assert_eq!(within.get("ci.yml#dead-id"), Some(&3));
    assert_eq!(
        within.get("a.py#no-id"),
        Some(&1),
        "another rule's key never rises"
    );
    assert_eq!(crate::roadmap::graced(head.clone(), &now, true), head);
}

#[test]
fn rule_5_names_its_key_and_what_a_failing_git_grep_said() {
    use crate::roadmap::cite;
    assert_eq!(Rule::DeadId.key(), "dead-id");
    let r = citing_repo();
    assert_eq!(
        cite::grep_files(r.path(), &["no-such-word-here"], false),
        Ok(Vec::new())
    );
    assert_eq!(
        cite::grep_files(r.path(), &["roadmap"], true).map(|f| f.len()),
        Ok(3)
    );
    let said = cite::grep_with(
        std::process::Command::new("goh-no-such-git"),
        r.path(),
        &["x"],
        false,
    );
    assert!(said.is_err_and(|m| m.starts_with("git grep failed: ")));
    let bare = tempfile::tempdir().unwrap_or_else(|e| panic!("tempdir: {e}"));
    let said = cite::grep_files(bare.path(), &["x"], false);
    assert!(said.is_err_and(|m| m.starts_with("git grep failed (exit ")));
}

#[test]
fn rule_5_a_grep_that_fails_is_a_hard_finding_not_a_clean_pass() {
    let dir = tempfile::tempdir().unwrap_or_else(|e| panic!("tempdir: {e}"));
    let bare = dir.path();
    std::fs::write(bare.join("ROADMAP.md"), "# Roadmap\n").unwrap_or_else(|e| panic!("write: {e}"));
    let declared = crate::roadmap::Declared::File("ROADMAP.md".to_owned());
    let g = crate::roadmap::gather(
        bare,
        &["ROADMAP.md".to_owned()],
        None,
        &declared,
        (false, true, true),
        || Ok(BTreeMap::new()),
    );
    assert!(
        g.hard
            .iter()
            .any(|h| h.contains("rule 5 (a dead roadmap id) cannot list its files")),
        "{:?}",
        g.hard
    );
}
