use super::{classify, index_path, lock_entries, out_before, parse_utc, Base, Entries};
use crate::crates_behind::Move;

fn mv(name: &str, from: &str, to: &str) -> Move {
    Move {
        name: name.to_owned(),
        from: from.to_owned(),
        to: to.to_owned(),
    }
}

/// itoa's index as index.crates.io served it on 2026-11-09 (deps trimmed), plus a yanked and a
/// prerelease line.
const ITOA: &str = r#"{"name":"itoa","vers":"1.0.17","deps":[],"cksum":"a","features":{},"yanked":false}
{"name":"itoa","vers":"1.0.18","deps":[],"cksum":"b","features":{},"yanked":false}
{"name":"itoa","vers":"1.0.19","deps":[],"cksum":"c","features":{},"yanked":false,"rust_version":"1.68","pubtime":"2026-11-09T01:42:29Z"}
{"name":"itoa","vers":"1.0.20","deps":[],"cksum":"d","features":{},"yanked":true,"pubtime":"2026-01-01T00:00:00Z"}
{"name":"itoa","vers":"1.1.0-rc.1","deps":[],"cksum":"e","features":{},"yanked":false,"pubtime":"2026-01-01T00:00:00Z"}
{"name":"itoa","vers":"2.0.0","deps":[],"cksum":"f","features":{},"yanked":false,"pubtime":"2026-01-01T00:00:00Z"}
"#;

#[test]
fn a_utc_stamp_is_seconds_since_the_epoch() {
    assert_eq!(parse_utc("1970-01-01T00:00:00Z"), Some(0));
    assert_eq!(parse_utc("2026-11-09T01:42:29Z"), Some(1_794_188_549));
    assert_eq!(parse_utc("2000-03-01T00:00:00.5Z"), Some(951_868_800));
    assert_eq!(parse_utc("2026-11-09 01:42:29"), None, "no zone, no answer");
}

#[test]
fn the_index_path_is_the_registrys_layout() {
    assert_eq!(index_path("a"), "1/a");
    assert_eq!(index_path("cc"), "2/cc");
    assert_eq!(index_path("syn"), "3/s/syn");
    assert_eq!(index_path("Itoa"), "it/oa/itoa");
}

#[test]
fn a_release_counts_only_when_it_was_out_before_the_change() {
    let release = parse_utc("2026-11-09T01:42:29Z").unwrap();
    assert_eq!(out_before(ITOA, "1.0.18", release + 1), Some(true));
    assert_eq!(
        out_before(ITOA, "1.0.18", release),
        Some(false),
        "published at the cutoff is not before it; the yanked 1.0.20, the prerelease and 2.0.0 never count"
    );
    assert_eq!(
        out_before(ITOA, "1.0.17", 1),
        Some(true),
        "1.0.18 has no pubtime: it predates the field, so it is old"
    );
    assert_eq!(out_before(ITOA, "1.0.19", u64::MAX), Some(false));
    assert_eq!(out_before(ITOA, "not-a-version", 1), None);
}

#[test]
fn only_an_entry_the_change_locked_with_a_release_out_before_it_is_red() {
    let base: Entries = [("old".to_owned(), "1.0.0".to_owned())].into();
    let moves = vec![
        mv("old", "v1.0.0", "v1.0.1"),
        mv("itoa", "v1.0.18", "v1.0.19"),
        mv("late", "v0.3.0", "v0.3.1"),
        mv(
            "gitdep",
            "v0.1.0 (https://example.com/x.git#1234567)",
            "#89abcde",
        ),
    ];
    let late = r#"{"name":"late","vers":"0.3.1","yanked":false,"pubtime":"2030-01-01T00:00:00Z"}"#;
    let fetch = |n: &str| match n {
        "itoa" => Some(ITOA.to_owned()),
        "late" => Some(late.to_owned()),
        _ => None,
    };
    let cutoff = parse_utc("2026-12-01T00:00:00Z").unwrap();
    let (red, drift, notes) = classify(moves, &Base::Known(base), cutoff, false, &fetch);
    let names = |v: &[Move]| v.iter().map(|m| m.name.clone()).collect::<Vec<_>>();
    assert_eq!(names(&red), ["itoa", "gitdep"]);
    assert_eq!(names(&drift), ["old", "late"]);
    assert!(notes.is_empty(), "{notes:?}");
}

#[test]
fn an_unreadable_index_keeps_the_live_verdict_and_says_so() {
    let (red, drift, notes) = classify(
        vec![mv("itoa", "v1.0.18", "v1.0.19")],
        &Base::Known(Entries::new()),
        u64::MAX,
        false,
        &|_| None,
    );
    assert_eq!((red.len(), drift.len()), (1, 0));
    assert!(notes[0].contains("release dates unreadable"), "{notes:?}");
}

#[test]
fn no_base_warns_every_entry_and_live_reds_every_entry() {
    let moves = || vec![mv("itoa", "v1.0.18", "v1.0.19")];
    let unknown = Base::Unknown("a shallow clone".to_owned());
    let (red, drift, notes) = classify(moves(), &unknown, u64::MAX, false, &|_| None);
    assert_eq!((red.len(), drift.len()), (0, 1));
    assert!(notes[0].contains("a shallow clone"), "{notes:?}");
    let base = Base::Known([("itoa".to_owned(), "1.0.18".to_owned())].into());
    let (red, drift, _) = classify(moves(), &base, 0, true, &|_| None);
    assert_eq!(
        (red.len(), drift.len()),
        (1, 0),
        "--live: every behind entry is red"
    );
}

#[test]
fn a_locks_entries_are_its_packages() {
    let lock = "version = 4\n\n[[package]]\nname = \"itoa\"\nversion = \"1.0.18\"\n\n\
                [[package]]\nname = \"toml\"\nversion = \"1.2.1+spec-1.1.0\"\n";
    let got = lock_entries(lock).unwrap();
    assert!(got.contains(&("toml".to_owned(), "1.2.1+spec-1.1.0".to_owned())));
    assert_eq!(got.len(), 2);
    assert_eq!(lock_entries("not = [toml"), None);
}
