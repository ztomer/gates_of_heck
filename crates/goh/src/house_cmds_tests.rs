use super::{chosen, command, entries, parse, TABLE};
use clap::Command;
use std::collections::BTreeSet;

#[test]
fn the_table_parses_whole() {
    let all = parse(TABLE).unwrap_or_else(|e| panic!("{e}"));
    assert_eq!(all.len(), entries().len());
    assert!(all.iter().any(|e| e.names == ["land", "landq"]), "{all:?}");
}

#[test]
fn every_name_and_alias_is_unique_and_no_script_repeats() {
    let mut names = BTreeSet::new();
    let mut scripts = BTreeSet::new();
    for e in entries() {
        for n in &e.names {
            assert!(names.insert(*n), "{n} is named twice in house_scripts.tsv");
        }
        assert!(
            scripts.insert(e.script),
            "{} is run by two subcommands",
            e.script
        );
    }
}

#[test]
fn every_script_exists_under_the_goh_root() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    for e in entries() {
        assert!(
            root.join(e.script).is_file(),
            "{} names {}, which is not a file",
            e.names[0],
            e.script
        );
    }
}

#[test]
fn a_malformed_line_is_named() {
    assert_eq!(
        parse("# c\nok\tx.py\tabout\nbroken x.py\n").unwrap_err(),
        "house_scripts.tsv:3: not NAME TAB SCRIPT TAB ABOUT"
    );
}

#[test]
fn a_subcommand_passes_every_argument_and_its_alias_resolves() {
    let cmd = command(Command::new("goh"));
    let m = cmd
        .clone()
        .try_get_matches_from(["goh", "landq", "--wait", "X", "--help"])
        .unwrap_or_else(|e| panic!("{e}"));
    assert_eq!(
        chosen(&m),
        Some((
            "land",
            "tools/landq.py",
            vec!["--wait".to_owned(), "X".to_owned(), "--help".to_owned()]
        ))
    );
    let bare = cmd
        .try_get_matches_from(["goh", "budget"])
        .unwrap_or_else(|e| panic!("{e}"));
    assert_eq!(chosen(&bare), Some(("budget", "lib/budget.py", vec![])));
}
