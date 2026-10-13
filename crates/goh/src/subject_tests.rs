//! The floor's every direction: unset says so, a bad value names its key, `0` is a declared no
//! floor, at the floor passes, under it is red naming key, floor and count, and nothing is NONE.

use super::*;

fn pairs(text: &str) -> BTreeMap<String, String> {
    crate::gatesrc::parse_pairs(text)
}

#[test]
fn every_key_is_one_scanners_and_the_pipeline_owns_it() {
    let mut keys: Vec<&str> = ALL.iter().map(|s| s.key).collect();
    keys.sort_unstable();
    keys.dedup();
    assert_eq!(keys.len(), ALL.len(), "two scanners share a floor key");
    for s in ALL {
        assert!(
            s.key.starts_with("GOH_") && s.key.contains("_MIN_"),
            "{} is not GOH_<SCANNER>_MIN_<UNIT>",
            s.key
        );
        assert!(
            crate::gatesrc::PIPELINE_KEYS.contains(&s.key),
            "{} is a .gatesrc key the pipeline does not adopt, and config.md cannot document",
            s.key
        );
    }
}

#[test]
fn an_unset_floor_is_absent_and_a_bad_one_names_its_key() {
    assert_eq!(
        parse_floors(&pairs("GOH_MAX_LINES=500\n")),
        Ok(BTreeMap::new())
    );
    let floors = parse_floors(&pairs("GOH_EMOJI_MIN_FILES=40\nGOH_MD_LINKS_MIN_LINKS=0\n"))
        .unwrap_or_else(|e| panic!("{e}"));
    assert_eq!(floors.get("GOH_EMOJI_MIN_FILES"), Some(&40));
    assert_eq!(floors.get("GOH_MD_LINKS_MIN_LINKS"), Some(&0));
    let empty = parse_floors(&pairs("GOH_SECRETS_MIN_FILES=\n"));
    assert_eq!(
        empty,
        Ok(BTreeMap::new()),
        "empty is unset, never a floor of 0"
    );
    for bad in ["many", "-1", "4.5", "1e3"] {
        let err = parse_floors(&pairs(&format!("GOH_SECRETS_MIN_FILES={bad}\n")))
            .err()
            .unwrap_or_else(|| panic!("{bad:?} parsed as a floor"));
        assert!(err.contains("GOH_SECRETS_MIN_FILES"), "{err}");
    }
}

#[test]
fn a_bad_floor_refuses_the_whole_gatesrc() {
    let err = crate::gatesrc::from_pairs(&pairs("GOH_UNREAPED_SPAWN_MIN_FILES=lots\n"))
        .err()
        .unwrap_or_else(|| panic!("a bad floor loaded"));
    assert!(err.contains("GOH_UNREAPED_SPAWN_MIN_FILES"), "{err}");
}

fn floors(entries: &[(&'static str, usize)]) -> BTreeMap<&'static str, usize> {
    entries.iter().copied().collect()
}

#[test]
fn at_the_floor_passes_and_one_under_is_red_naming_key_floor_and_count() {
    let read = [(&EMOJI, 12)];
    assert_eq!(judge(&read, &floors(&[("GOH_EMOJI_MIN_FILES", 12)])).0, 0);
    let (code, text) = judge(&read, &floors(&[("GOH_EMOJI_MIN_FILES", 13)]));
    assert_eq!(code, 1, "{text}");
    for want in ["GOH_EMOJI_MIN_FILES", "floor of 13", "read 12 text file(s)"] {
        assert!(text.contains(want), "{want}: {text}");
    }
}

#[test]
fn reading_nothing_under_a_floor_says_none() {
    let (code, text) = judge(
        &[(&UNREAPED_SPAWN, 0)],
        &floors(&[("GOH_UNREAPED_SPAWN_MIN_FILES", 1)]),
    );
    assert_eq!(code, 1);
    assert!(text.contains("read NONE"), "{text}");
}

#[test]
fn an_unset_floor_is_one_advisory_line_naming_key_and_count() {
    let (code, text) = judge(
        &[(&EMOJI, 12), (&SECRETS, 9), (&SHELL_LINT, 0)],
        &floors(&[("GOH_SECRETS_MIN_FILES", 5)]),
    );
    assert_eq!(code, 0, "{text}");
    assert_eq!(text.lines().count(), 1, "{text}");
    assert!(text.contains("GOH_EMOJI_MIN_FILES=12"), "{text}");
    // Declared, and a scanner with nothing to read, are not omissions to report.
    assert!(!text.contains("GOH_SECRETS"), "{text}");
    assert!(!text.contains("GOH_SHELL_LINT"), "{text}");
}

#[test]
fn a_declared_zero_is_no_floor_and_no_advisory() {
    let (code, text) = judge(&[(&EMOJI, 0)], &floors(&[("GOH_EMOJI_MIN_FILES", 0)]));
    assert_eq!((code, text.as_str()), (0, ""));
}

#[test]
fn records_come_back_in_registry_order_once_and_are_cleared() {
    let _ = take();
    record(&SECRETS, 3);
    record(&EMOJI, 1);
    record(&EMOJI, 2);
    let got: Vec<(&str, usize)> = take().into_iter().map(|(s, n)| (s.key, n)).collect();
    assert_eq!(
        got,
        [("GOH_EMOJI_MIN_FILES", 2), ("GOH_SECRETS_MIN_FILES", 3)]
    );
    assert_eq!(take(), Vec::<(&Subject, usize)>::new());
}
