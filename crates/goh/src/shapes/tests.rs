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
fn every_layer_scanner_is_a_step_a_layer_gate_runs() {
    let gates = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../gates");
    let text: String = std::fs::read_dir(&gates)
        .expect("gates/")
        .filter_map(Result::ok)
        .filter(|e| e.path().extension().is_some_and(|x| x == "sh"))
        .filter_map(|e| std::fs::read_to_string(e.path()).ok())
        .collect();
    for label in super::LAYER_SCANNERS {
        let cmd = label
            .strip_prefix("goh ")
            .expect("a layer scanner is a goh command");
        assert!(
            text.contains(&format!("\"${{_goh_cmd[@]}}\" {cmd}")),
            "`{label}` is in LAYER_SCANNERS and no gate in gates/ runs it: a stale entry"
        );
    }
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
    // A row that owns no token judges whole files (`file length`): it counts no site, so it
    // overlaps nothing -- and to be one it must judge EVERY line of each file it covers, which no
    // line scanner does.
    for (row, d) in declared().filter(|(_, d)| d.owns.is_empty()) {
        for s in d.shapes.iter().filter(|s| s.verdict == Verdict::Covered) {
            let (rel, text) = s.fixture;
            let judged = (d.judged)(rel, text).unwrap_or_else(|e| panic!("{e}"));
            let all: Vec<usize> = (1..=text.lines().count()).collect();
            assert_eq!(
                judged, all,
                "`{}` owns no token, so it judges whole files, and `{}` is not judged whole",
                row.scanner, s.name
            );
        }
    }
    for (row, d) in declared().filter(|(_, d)| !d.owns.is_empty()) {
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
fn the_differential_judges_the_moving_tag_in_linux_test_sh() {
    // GOH.MOVINGTAGSH closed the gap the differential first found here: both probe hits (the
    // assignment and the run) are judged, and none is left to name.
    let t = tally(moving_tags(), &[("scripts/dev/linux_test.sh", LINUX_TEST)]);
    assert_eq!(t.judged, 2, "moving-tags reads the script: {t:?}");
    assert!(t.gaps.is_empty() && t.unnamed.is_empty(), "{t:?}");
}

/// A `.env` file compose reads `IMAGE=` from: a shape no row names and the scanner does not read.
const DOT_ENV: (&str, &str) = (".env", "IMAGE=rust:1\n");

/// The moving-tags row with `extra` appended: what the table reads once a shape is named.
fn with_shape(extra: Shape) -> &'static Row {
    let State::Declared(d) = &moving_tags().state else {
        unreachable!("moving-tags is declared")
    };
    let mut shapes: Vec<Shape> = d.shapes.to_vec();
    shapes.push(extra);
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

const ENV_GAP: Shape = Shape {
    name: "a compose .env file",
    path: r"(^|/)\.env$",
    line: "",
    verdict: Verdict::Gap("GOH.TEST"),
    fixture: DOT_ENV,
};

#[test]
fn an_unnamed_shape_is_red_in_the_differential_and_a_named_gap_is_not() {
    let t = tally(moving_tags(), &[DOT_ENV]);
    let lines: Vec<usize> = t.unnamed.iter().map(|h| h.line).collect();
    assert_eq!(lines, [1], "{t:?}");
    let (red, text) = diff::report(moving_tags().scanner, &t, 1);
    assert!(red, "{text}");
    assert!(text.contains("does not judge 1 line"), "{text}");
    let t = tally(with_shape(ENV_GAP), &[DOT_ENV]);
    assert!(t.unnamed.is_empty(), "{t:?}");
    let (red, text) = diff::report(moving_tags().scanner, &t, 1);
    assert!(!red && text.contains("gap (GOH.TEST)"), "{text}");
    assert!(
        text.contains("media_server: .env:1: IMAGE=rust:1"),
        "{text}"
    );
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
    let repo =
        goh_testkit::repo_with(&[DOT_ENV, ("Dockerfile", "FROM rust:1.99.1\n")]).expect("fixture");
    let roots = vec![repo.path().to_path_buf()];
    let (code, text) = diff::run(&[moving_tags()], &roots, true).expect("runs");
    assert_eq!(code, 1, "{text}");
    assert!(
        text.contains("2 probe hits over 2 files in 1 repo: 1 judged"),
        "{text}"
    );
    let (code, text) = diff::run(&[moving_tags()], &roots, false).expect("runs");
    assert_eq!(code, 0, "within the grace it warns: {text}");
    assert!(text.contains("2026-11-23"), "{text}");
    let (code, text) = diff::run(&[with_shape(ENV_GAP)], &roots, true).expect("runs");
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

#[test]
fn the_registry_json_is_the_registry_with_each_rows_state() {
    let rows = super::registry_json();
    let rows = rows.as_array().expect("an array");
    let labels: BTreeSet<&str> = rows.iter().filter_map(|r| r["scanner"].as_str()).collect();
    assert_eq!(labels, registry(), "the JSON names exactly the registry");
    let state = |l: &str| {
        rows.iter()
            .find(|r| r["scanner"] == l)
            .map(|r| r["state"].clone())
    };
    assert_eq!(state("goh status-markers"), Some("declared".into()));
    assert_eq!(
        state("gate self-proofs still pass"),
        Some("not-a-scanner".into())
    );
    assert_eq!(state("shell lint"), Some("unreviewed".into()));
}
