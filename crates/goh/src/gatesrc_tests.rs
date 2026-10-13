//! `.gatesrc` parsing: the opt-in set, quoting, `export`, expansion and the length union.

/// The opt-in set is strings, so a mistyped key enables nothing and reads as
/// "this repo did not choose that gate". This pins the two lists against
/// each other: every key the code asks about must be one `OPT_IN_KEYS`
/// declares, and nothing else may be.
#[test]
fn the_optional_gate_keys_are_the_ones_the_code_reads() {
    // EVERY source file, not a list of the ones the readers were last seen in: the list
    // named steps.rs/main.rs and went red the day kill-by-name, python-formatted and
    // claim-derivation moved into their own modules (Phase N1).
    fn walk(dir: &Path, out: &mut String) {
        let mut entries: Vec<PathBuf> = std::fs::read_dir(dir)
            .expect("src readable")
            .map(|e| e.expect("entry").path())
            .collect();
        entries.sort();
        for path in entries {
            if path.is_dir() {
                walk(&path, out);
            } else if path.extension().is_some_and(|x| x == "rs") {
                out.push_str(&std::fs::read_to_string(&path).expect("utf-8 source"));
            }
        }
    }
    let mut src = String::new();
    walk(&Path::new(env!("CARGO_MANIFEST_DIR")).join("src"), &mut src);
    let asked: Vec<&str> = [
        "GOH_SKILLS_CORPUS",
        "GOH_NO_HOME_PATHS",
        "GOH_NO_KILL_BY_NAME",
        "GOH_PYTHON_FORMATTED",
        "GOH_CLAIM_DERIVATION",
        "GOH_NO_EARLY_EXIT_PIPE",
    ]
    .into_iter()
    .collect();
    for key in &asked {
        assert!(
            src.contains(&format!("opt_in(cfg, \"{key}\")")),
            "{key} is read by the code but must also be declared, or a repo \
             setting it gets nothing"
        );
        assert!(
            OPT_IN_KEYS.contains(key),
            "{key} is read by the code but is not in OPT_IN_KEYS: the parse \
             would ignore it and the gate would never run"
        );
    }
    assert_eq!(
        OPT_IN_KEYS.len(),
        asked.len(),
        "OPT_IN_KEYS declares {} but the code reads {asked:?}",
        OPT_IN_KEYS.len()
    );
}

#[test]
fn an_opt_in_is_present_and_non_empty_and_nothing_else_is() {
    let on = from_pairs(&parse_pairs("GOH_NO_KILL_BY_NAME=1\n")).expect("parse");
    assert!(opt_in(&on, "GOH_NO_KILL_BY_NAME"));
    // Present but EMPTY is not a choice: the shell side tests
    // `[ -n "${VAR:-}" ]` and the two must agree, or the pipeline and the
    // binary disagree about which gates this repo runs.
    let empty = from_pairs(&parse_pairs("GOH_NO_KILL_BY_NAME=\n")).expect("parse");
    assert!(!opt_in(&empty, "GOH_NO_KILL_BY_NAME"));
    let other = from_pairs(&parse_pairs("GOH_MAX_LINES=500\n")).expect("parse");
    assert!(!opt_in(&other, "GOH_NO_KILL_BY_NAME"));
}

use super::*;

#[test]
fn quotes_comments_and_bare_values() {
    let pairs = parse_pairs(
        "# comment\nGOH_MAX_LINES=500                 # file-length cap\nGOH_EXCLUDE='vendor/|\\.generated\\.'\nEMPTY=\n",
    );
    assert_eq!(pairs["GOH_MAX_LINES"], "500");
    assert_eq!(pairs["GOH_EXCLUDE"], r"vendor/|\.generated\.");
    assert_eq!(pairs["EMPTY"], "");
}

#[test]
fn export_prefix_is_the_bash_idiom_not_a_different_key() {
    let pairs = parse_pairs("export GOH_EXCLUDE='vendor/'\nexport  GOH_ALLOW=x # c\n");
    assert_eq!(pairs["GOH_EXCLUDE"], "vendor/");
    assert_eq!(pairs["GOH_ALLOW"], "x");
    assert!(!pairs.contains_key("export GOH_EXCLUDE"));
}

#[test]
fn env_expansion() {
    let home = std::env::var("HOME").unwrap();
    let pairs = parse_pairs("GOH_SKILLS_ROOT=$HOME/.claude/skills\nA=${HOME}/x\n");
    assert_eq!(pairs["GOH_SKILLS_ROOT"], format!("{home}/.claude/skills"));
    assert_eq!(pairs["A"], format!("{home}/x"));
}

#[test]
fn invalid_max_lines_is_an_error() {
    let mut pairs = BTreeMap::new();
    pairs.insert("GOH_MAX_LINES".to_owned(), "many".to_owned());
    assert!(from_pairs(&pairs).is_err());
    assert!(from_pairs(&BTreeMap::new()).unwrap().max_lines.is_none());
}

#[test]
fn max_lines_off_is_a_declared_no_cap() {
    let mut pairs = BTreeMap::new();
    pairs.insert("GOH_MAX_LINES".to_owned(), "off".to_owned());
    let cfg = from_pairs(&pairs).unwrap();
    assert!(cfg.line_cap_off && cfg.max_lines.is_none());
    assert!(!from_pairs(&BTreeMap::new()).unwrap().line_cap_off);
    pairs.insert("GOH_MAX_LINES".to_owned(), "500".to_owned());
    assert!(!from_pairs(&pairs).unwrap().line_cap_off);
}

#[test]
fn length_union_is_additive() {
    let mut cfg = Gatesrc::default();
    assert_eq!(length_exclude(&cfg), "");
    cfg.exclude = "vendor/".to_owned();
    assert_eq!(length_exclude(&cfg), "vendor/");
    cfg.line_exclude = "third_party/".to_owned();
    assert_eq!(length_exclude(&cfg), "vendor/|third_party/");
}
