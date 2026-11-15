//! The walk (every scanner has a row, the burn-down only shrinks), every shape's fixture re-proven,
//! one owner per token, and the differential red on an unnamed shape.

use super::diff::{self, Compiled, Tally};
use super::{registry, table, Declared, Row, Shape, State, Verdict};
use std::collections::BTreeSet;

fn declared() -> impl Iterator<Item = (&'static Row, &'static Declared)> {
    table::ROWS.iter().filter_map(|r| match &r.state {
        State::Declared(d) => Some((r, d)),
        _ => None,
    })
}

fn compiled(row: &'static Row) -> Compiled {
    Compiled::new(row).unwrap_or_else(|e| panic!("{e}"))
}

#[test]
fn every_scanner_in_the_registry_has_exactly_one_row() {
    let reg = registry();
    let mut seen = BTreeSet::new();
    for r in table::ROWS {
        assert!(seen.insert(r.scanner), "two rows for `{}`", r.scanner);
        assert!(
            reg.contains(r.scanner),
            "`{}` has a row and is no scanner in the registry: a stale row",
            r.scanner
        );
    }
    let missing: Vec<_> = reg.difference(&seen).collect();
    assert!(
        missing.is_empty(),
        "scanners with no row in crates/goh/src/shapes/table.rs (declare their shapes): {missing:?}"
    );
    // The registry is the pipeline's: the walk reads both sources, so it cannot be a hand copy.
    assert!(
        reg.contains("no container image on a moving tag") && reg.contains("goh status-markers")
    );
}

#[test]
fn the_burn_down_only_shrinks_and_no_new_scanner_joins_it() {
    let unreviewed: BTreeSet<&str> = table::ROWS
        .iter()
        .filter(|r| matches!(r.state, State::Unreviewed))
        .map(|r| r.scanner)
        .collect();
    let listed: BTreeSet<&str> = table::BURN_DOWN.iter().copied().collect();
    assert_eq!(
        listed.len(),
        table::BURN_DOWN.len(),
        "a name twice in BURN_DOWN"
    );
    let joined: Vec<_> = unreviewed.difference(&listed).collect();
    assert!(
        joined.is_empty(),
        "unreviewed rows not in BURN_DOWN -- a new scanner declares its shapes: {joined:?}"
    );
    let stale: Vec<_> = listed.difference(&unreviewed).collect();
    assert!(
        stale.is_empty(),
        "BURN_DOWN names rows no longer unreviewed: delete them: {stale:?}"
    );
}

/// The probe hits in `s`'s fixture that `s` itself names.
fn own_hits(c: &Compiled, s: &Shape) -> Vec<usize> {
    let (rel, text) = s.fixture;
    let lines: Vec<&str> = text.lines().collect();
    c.hits(text)
        .into_iter()
        .filter(|n| c.matching(rel, lines[n - 1]).any(|m| std::ptr::eq(m, s)))
        .collect()
}

#[test]
fn every_shape_has_a_fixture_that_proves_its_verdict() {
    for (row, d) in declared() {
        let c = compiled(row);
        assert!(!d.shapes.is_empty(), "`{}` declares no shapes", row.scanner);
        assert!(!d.sources.is_empty(), "`{}` names no sources", row.scanner);
        for s in d.shapes {
            let (rel, text) = s.fixture;
            let hits = own_hits(&c, s);
            assert!(
                !hits.is_empty(),
                "`{}` shape `{}`: its fixture holds no probe hit the shape names",
                row.scanner,
                s.name
            );
            let judged = c.judged(rel, text).unwrap_or_else(|e| panic!("{e}"));
            for n in hits {
                let is = judged.contains(&n);
                match s.verdict {
                    Verdict::Covered => assert!(
                        is,
                        "`{}` claims to cover `{}` and does not judge {rel}:{n}",
                        row.scanner, s.name
                    ),
                    Verdict::OutOfScope(why) | Verdict::Gap(why) => assert!(
                        !is,
                        "`{}` now judges `{}` ({why}), {rel}:{n}: the row is stale -- make it Covered",
                        row.scanner, s.name
                    ),
                }
            }
        }
    }
}

#[test]
fn every_token_has_one_owner_and_no_scanner_judges_another_rows_token() {
    let mut owners = BTreeSet::new();
    for (row, d) in declared() {
        for t in d.owns {
            assert!(owners.insert(t.name), "token `{}` owned twice", t.name);
            let (rel, text) = t.sample;
            let mine = (d.judged)(rel, text).unwrap_or_else(|e| panic!("{e}"));
            assert!(
                !mine.is_empty(),
                "`{}` owns `{}` and does not judge its sample",
                row.scanner,
                t.name
            );
        }
    }
    let mut overlaps = Vec::new();
    for (row, d) in declared() {
        for (other, od) in declared().filter(|(o, _)| !std::ptr::eq(*o, row)) {
            for t in od.owns {
                let (rel, text) = t.sample;
                if !(d.judged)(rel, text)
                    .unwrap_or_else(|e| panic!("{e}"))
                    .is_empty()
                {
                    overlaps.push(format!(
                        "`{}` judges `{}`, which `{}` owns",
                        row.scanner, t.name, other.scanner
                    ));
                }
            }
        }
    }
    assert!(overlaps.is_empty(), "one site counted twice: {overlaps:?}");
}

/// `media_server`'s `scripts/dev/linux_test.sh` before 09cdcd89 pinned it (SRV.RUSTTAG): the moving
/// `rust:1` that `goh moving-tags` never read, its lines as they were.
const LINUX_TEST: &str = "#!/usr/bin/env bash\n\
# Run the workspace's tests in a Linux container.\n\
set -euo pipefail\n\
IMAGE=rust:1\n\
vol=media-server-target\n\
docker run --rm -v \"$vol:/v\" \"$IMAGE\" chown \"$uid\" /v\n";

fn moving_tags() -> &'static Row {
    super::row("no container image on a moving tag").expect("moving-tags row")
}

fn tally(row: &'static Row, files: &[(&str, &str)]) -> Tally {
    let c = compiled(row);
    let mut t = Tally::default();
    for (rel, text) in files {
        c.file("media_server", rel, text, &mut t).expect("hook");
    }
    t
}

#[test]
fn the_differential_finds_the_moving_tag_gap_in_linux_test_sh() {
    let t = tally(moving_tags(), &[("scripts/dev/linux_test.sh", LINUX_TEST)]);
    assert_eq!(t.judged, 0, "moving-tags does not read shell: {t:?}");
    let image = t
        .gaps
        .iter()
        .find(|(_, _, h)| h.rel == "scripts/dev/linux_test.sh" && h.text.contains("rust:1"))
        .unwrap_or_else(|| panic!("the rust:1 line is not reported: {t:?}"));
    assert_eq!((image.0, image.2.line), ("GOH.MOVINGTAGSH", 4));
    assert!(t.unnamed.is_empty(), "{t:?}");
    let (red, text) = diff::report(moving_tags().scanner, &t, 1);
    assert!(!red && text.contains("gap (GOH.MOVINGTAGSH)"), "{text}");
    assert!(
        text.contains("media_server: scripts/dev/linux_test.sh:4: IMAGE=rust:1"),
        "{text}"
    );
}

/// The same row with its two gap shapes deleted: what the table read before anyone named the gap.
fn without_gaps() -> &'static Row {
    let State::Declared(d) = &moving_tags().state else {
        unreachable!("moving-tags is declared")
    };
    let shapes: Vec<Shape> = d
        .shapes
        .iter()
        .filter(|s| !matches!(s.verdict, Verdict::Gap(_)))
        .copied()
        .collect();
    Box::leak(Box::new(Row {
        scanner: moving_tags().scanner,
        state: State::Declared(Declared {
            probe: d.probe,
            judged: d.judged,
            owns: d.owns,
            shapes: Box::leak(shapes.into_boxed_slice()),
            sources: d.sources,
        }),
    }))
}

#[test]
fn an_unnamed_shape_is_red_in_the_differential() {
    let t = tally(without_gaps(), &[("scripts/dev/linux_test.sh", LINUX_TEST)]);
    let lines: Vec<usize> = t.unnamed.iter().map(|h| h.line).collect();
    assert_eq!(lines, [4, 6], "{t:?}");
    let (red, text) = diff::report(moving_tags().scanner, &t, 1);
    assert!(red, "{text}");
    assert!(text.contains("does not judge 2 lines"), "{text}");
}

#[test]
fn a_covered_hit_the_scanner_misses_is_never_named() {
    // A judged Dockerfile, and a probe hit in one the reader does not see as an instruction (a
    // FROM continuing a RUN): covered shapes shield nothing, so the miss is unnamed.
    let t = tally(
        moving_tags(),
        &[("Dockerfile", "FROM rust:1.99.1\nRUN echo \\\n FROM x\n")],
    );
    assert_eq!(t.judged, 1, "{t:?}");
    assert_eq!(t.unnamed.iter().map(|h| h.line).collect::<Vec<_>>(), [3]);
}

#[test]
fn the_run_over_a_tree_reads_its_listed_files_and_obeys_the_grace() {
    let repo = goh_testkit::repo_with(&[
        ("scripts/dev/linux_test.sh", LINUX_TEST),
        ("Dockerfile", "FROM rust:1.99.1\n"),
    ])
    .expect("fixture");
    let roots = vec![repo.path().to_path_buf()];
    let (code, text) = diff::run(&[without_gaps()], &roots, true).expect("runs");
    assert_eq!(code, 1, "{text}");
    assert!(
        text.contains("3 probe hits over 2 files in 1 repo: 1 judged"),
        "{text}"
    );
    let (code, text) = diff::run(&[without_gaps()], &roots, false).expect("runs");
    assert_eq!(code, 0, "within the grace it warns: {text}");
    assert!(text.contains("2026-11-23"), "{text}");
    let (code, text) = diff::run(&[moving_tags()], &roots, true).expect("runs");
    assert_eq!(code, 0, "the gap is named: {text}");
}

#[test]
fn since_selects_the_rows_a_change_touched() {
    let names = |c: &[&str]| -> Vec<&str> {
        let c: Vec<String> = c.iter().map(|s| (*s).to_string()).collect();
        diff::touched(&c).iter().map(|r| r.scanner).collect()
    };
    assert_eq!(
        names(&["crates/goh/src/movingtag/scan.rs", "README.md"]),
        ["no container image on a moving tag"]
    );
    assert_eq!(
        names(&["crates/goh/src/shapes/table.rs"]).len(),
        declared().count()
    );
    let none = names(&["README.md"]);
    assert!(none.is_empty(), "a doc change differs no scanner: {none:?}");
}
