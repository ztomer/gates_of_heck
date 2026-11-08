//! Every rule red on a planted line and green on the clean shape; the ratchet's five directions.

use super::judge::{self, Gathered};
use super::scan::{self, Rule};
use std::collections::BTreeMap;

/// The reworked shape (routines, Adv360-Pro-ZMK, `app_updates`): clean under every rule.
const CLEAN: &str = "# Roadmap\n\n## Rulings in force\n\n- R1 owner 2026-11-08: one plan.\n\n\
## 5. Open items — in phase order\n\nEvery entry has an id and a **Done when**.\n\n\
### Phase 1 — now\n\nWhy: first.\n\n\
- [ ] **RT.WINCLOSE — a close persists.** (queue:\n  RT.WINCLOSE.) Text.\n  \
- **Done when:** a red-first test.\n\
- **P0.4 Host chords (queue: OW.2; BLOCKED).**\n  *Done when:* the owner reports.\n\n\
#### AU.WAF — attended solve\n\n- a note under a heading entry\n\nDone when: a headed run.\n\n\
## Phase 2: parked\n\n- **H1 dependency currency.** Done when: `cargo update` locks 0.\n\n\
## 5.5 Standing hygiene\n\n- every pass: nothing behind.\n\n\
```\nDONE ~~quoted~~ in a fence\n```\n\nA `DONE` in inline code is a quote.\n";

fn rules(text: &str) -> Vec<Rule> {
    scan::read(text).findings.iter().map(|f| f.rule).collect()
}

#[test]
fn the_reworked_shape_is_clean() {
    let read = scan::read(CLEAN);
    assert!(read.findings.is_empty(), "{:?}", read.findings);
    let ids: Vec<_> = read
        .entries
        .iter()
        .map(|e| e.id.clone().unwrap_or_default())
        .collect();
    assert_eq!(ids, ["RT.WINCLOSE", "P0.4", "AU.WAF", "H1"]);
    assert_eq!(read.phases, 2);
    let cited: Vec<_> = read.citations.iter().map(|(_, id)| id.as_str()).collect();
    assert_eq!(cited, ["RT.WINCLOSE", "OW.2"]);
    assert_eq!(
        read.citations[0].0, 15,
        "a citation's line is where `(queue:` opens"
    );
}

#[test]
fn rule_2_every_completion_mark_is_red_and_a_reasoned_marker_is_not() {
    for planted in [
        "- **X1 thing.** DONE",
        "- ~~X2 struck~~",
        "- X3 shipped in v1.2",
        "| X4 | SHIPPED |",
        "- ✓ **X5 thing**",
        "- [x] X6 ticked",
        "### X7.1 the gap — closed",
        "  - X8 nested, done: DONE",
        "| A build's inputs are its key | the stamp | DONE (`22ab30b`) |",
        "- [x] ticked with no id",
        "- ✓ **Sprite pass**",
        "### ~~Old section~~",
    ] {
        let text = format!("{CLEAN}\n{planted}\n");
        assert_eq!(rules(&text), [Rule::Completed], "{planted}");
    }
    // Prose about the plan marks no entry (ztools e556eb5): green beside the red rows above.
    for prose in [
        "What shipped is in CHANGELOG.md, not here.",
        "- the shipped chain is the one to read",
        "| Column | shipped-work text |",
        "CLOSED items live in git.",
        "## How work is closed",
        "- Done is recorded in git, not here",
    ] {
        let text = format!("{CLEAN}\n{prose}\n");
        assert!(rules(&text).is_empty(), "prose is not an entry: {prose}");
    }
    let justified =
        format!("{CLEAN}\n- R3 shipped means installed. <!-- roadmap-ok: quotes R3 -->\n");
    assert!(rules(&justified).is_empty(), "{:?}", rules(&justified));
    let empty = format!("{CLEAN}\n- R3 shipped <!-- roadmap-ok: -->\n");
    assert_eq!(
        rules(&empty),
        [Rule::Completed],
        "a marker with no reason justifies nothing"
    );
}

#[test]
fn rule_3_an_entry_needs_an_id_and_a_clause_and_a_clause_needs_an_entry() {
    let no_id = CLEAN.replace("**H1 dependency currency.**", "**dependency currency.**");
    assert_eq!(rules(&no_id), [Rule::NoId]);
    let no_clause = CLEAN.replace(" Done when: `cargo update` locks 0.", " Locks 0.");
    assert_eq!(rules(&no_clause), [Rule::NoDoneWhen]);
    let orphan = CLEAN.replace(
        "- every pass: nothing behind.",
        "Done when: nothing behind.",
    );
    assert_eq!(rules(&orphan), [Rule::Orphan]);
    let zonewm = CLEAN.replace(
        "#### AU.WAF — attended solve",
        "#### 0.15 [OWNER] one swipe",
    );
    assert_eq!(
        rules(&zonewm),
        Vec::<Rule>::new(),
        "a dotted number is an id (ZoneWM's 0.15)"
    );
    let word_is_no_id = CLEAN.replace("**H1 dependency", "**Notarization dependency");
    assert_eq!(
        rules(&word_is_no_id),
        [Rule::NoId],
        "an id carries a digit or a dot"
    );
}

#[test]
fn a_sixth_phase_is_red() {
    let six: String = (1..=6).fold(String::new(), |s, n| s + &format!("## Phase {n}\n\n"));
    let read = scan::read(&six);
    assert_eq!(read.phases, 6);
    assert_eq!(rules(&six), [Rule::Phases]);
}

#[test]
fn plan_files_are_named_plans_not_documents_about_one() {
    for p in [
        "ROADMAP.md",
        "docs/roadmap.md",
        "BACKLOG.md",
        "FEATURE_PLAN.md",
        "PLAN_ALL_3.md",
        "TODO.md",
        "docs/plans/2026-05-11-x.md",
        "MASTER_PLAN.md",
    ] {
        assert!(scan::is_plan_file(p), "{p}");
    }
    for p in [
        "tests/fixtures/weekend_plan.md",
        "docs/timeplan.md",
        "README.md",
        "ROADMAP.txt",
        "CHANGELOG.md",
        "docs/planning.md",
    ] {
        assert!(!scan::is_plan_file(p), "{p}");
    }
}

fn gathered(found: &[(&str, usize)]) -> Gathered {
    Gathered {
        roadmap: "ROADMAP.md".into(),
        found: found
            .iter()
            .map(|(k, n)| {
                (
                    (*k).to_owned(),
                    (0..*n).map(|i| format!("ROADMAP.md:{i}: x")).collect(),
                )
            })
            .collect(),
        ..Gathered::default()
    }
}

fn seed(pairs: &[(&str, usize)]) -> BTreeMap<String, usize> {
    pairs.iter().map(|(k, n)| ((*k).to_owned(), *n)).collect()
}

#[test]
fn the_ratchet_holds_in_every_direction() {
    let key = "ROADMAP.md#completed";
    // A clean tree passes; a finding at its seeded count passes.
    assert_eq!(judge::judge(&gathered(&[]), &seed(&[]), None).0, 0);
    assert_eq!(
        judge::judge(&gathered(&[(key, 2)]), &seed(&[(key, 2)]), None).0,
        0
    );
    // Over the seed: red with its sites.
    let (code, text) = judge::judge(&gathered(&[(key, 3)]), &seed(&[(key, 2)]), None);
    assert_eq!(code, 1);
    assert!(
        text.contains("ROADMAP.md:2: x") && text.contains("seeded with 2; it has 3"),
        "{text}"
    );
    // Under it, and stale (gone entirely): red until lowered.
    assert_eq!(
        judge::judge(&gathered(&[(key, 1)]), &seed(&[(key, 2)]), None).0,
        1
    );
    let (code, text) = judge::judge(&gathered(&[]), &seed(&[(key, 2)]), None);
    assert!(code == 1 && text.contains("lower the seed"), "{text}");
    // One key does not launder another: a seeded `completed` does not excuse a `no-id`.
    assert_eq!(
        judge::judge(
            &gathered(&[(key, 1), ("ROADMAP.md#no-id", 1)]),
            &seed(&[(key, 1)]),
            None
        )
        .0,
        1
    );
    // The seed may not rise against HEAD's.
    let (code, text) = judge::judge(
        &gathered(&[(key, 3)]),
        &seed(&[(key, 3)]),
        Some(&seed(&[(key, 2)])),
    );
    assert!(code == 1 && text.contains("rose from 2 to 3"), "{text}");
    // A hard finding is red whatever the seed says.
    let mut hard = gathered(&[]);
    hard.hard
        .push("GOH_ROADMAP=X.md is not a tracked file here".into());
    assert_eq!(judge::judge(&hard, &seed(&[]), None).0, 1);
}

#[test]
fn a_rule_the_run_could_not_judge_is_withheld_not_read_as_fixed() {
    let key = "ROADMAP.md#queue-done";
    let mut g = gathered(&[]);
    g.queue_withheld = true;
    assert_eq!(judge::judge(&g, &seed(&[(key, 2)]), None).0, 0);
    let text = judge::seed_text(&g, Some(&seed(&[(key, 2)]))).unwrap_or_default();
    assert!(text.contains("\"ROADMAP.md#queue-done\": 2"), "{text}");
}

#[test]
fn seeding_refuses_a_rise_and_a_hard_finding() {
    let key = "ROADMAP.md#completed";
    assert!(judge::seed_text(&gathered(&[(key, 3)]), Some(&seed(&[(key, 2)]))).is_err());
    assert!(
        judge::seed_text(&gathered(&[(key, 3)]), None).is_ok(),
        "the first seed is today's"
    );
    let mut hard = gathered(&[]);
    hard.hard.push("missing".into());
    assert!(judge::seed_text(&hard, None).is_err());
    assert!(
        judge::parse_seed("{}").is_err(),
        "a corrupt seed is an error, never empty"
    );
}

#[test]
fn the_queue_parses_statuses_and_refuses_a_malformed_file() {
    let q = super::queue::parse(
        r#"{"items":[{"id":"A.1","status":"done"},{"id":"B.2","status":"todo"}]}"#,
    );
    assert_eq!(
        q.ok().and_then(|m| m.get("A.1").cloned()).as_deref(),
        Some("done")
    );
    assert!(super::queue::parse("[]").is_err());
    assert!(super::queue::parse(r#"{"items":[{"id":1}]}"#).is_err());
}
