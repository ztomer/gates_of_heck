//! `.gatesrc` configuration — Rust port of the config sourcing in
//! `gates/structural.sh`.
//!
//! The file is sourced shell in the target repo root; this parser covers the
//! subset structural steps need: `KEY=value` lines with single-quoted, double-
//! quoted, or bare values, `#` comments, and `$NAME` / `${NAME}` expansion
//! from the process environment (e.g. `GOH_SKILLS_ROOT=$HOME/.claude/skills`).
//! All keys are optional, exactly like the shell version.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// Which OPTIONAL gates this repository has opted into, as the SET of `.gatesrc`
/// keys that are present.
///
/// A set, not four `bool` fields, and that is the second time this shape was
/// tried: `clippy::struct_excessive_bools` refuses more than three bools in a
/// struct, and moving them into a sub-struct does not satisfy it -- correctly,
/// because the lint's complaint is the struct-of-bools, not where it sits. These
/// are not four independent facts; they are one fact ("which of the optional
/// gates did this repo choose"), and a set is how that fact is shaped.
///
/// It also stops the next opt-in from being a code change: `GOH_*` keys arrive
/// as strings, so a fifth gate is one more key rather than one more field. The
/// cost is that a typo would silently not enable anything, which is what
/// `tests/test_gatesrc.rs::the_optional_gate_keys_are_the_ones_the_code_reads`
/// is for.
pub type OptIns = std::collections::BTreeSet<String>;

/// Every `.gatesrc` key that turns on an optional gate, spelled once.
///
/// Read by the test that pins the key set, so a gate cannot read a key nobody
/// documented and a doc cannot name a gate that reads nothing.
pub const OPT_IN_KEYS: [&str; 5] = [
    "GOH_SKILLS_CORPUS",
    "GOH_NO_HOME_PATHS",
    "GOH_NO_KILL_BY_NAME",
    // Declares a rule set rather than enforcing a house one, so it is opt-in
    // like the rest: a repo that has never chosen a rule set should not learn
    // one by going red.
    "GOH_PYTHON_FORMATTED",
    // A number in prose, re-derived from the tree. Opt-in because it lands RED
    // in every repo in the estate — every repo in the estate has the defect —
    // and a gate that goes red in twenty places on the day it lands is a gate
    // that gets disabled.
    "GOH_CLAIM_DERIVATION",
];

/// Structural knobs read from `.gatesrc`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Gatesrc {
    /// The optional gates this repo opted into.
    pub opt_ins: OptIns,
    /// File-length cap; unset disables the check.
    pub max_lines: Option<usize>,
    /// `GOH_MAX_LINES=off`: no cap BY DECISION, so the step says so instead of
    /// warning that one was forgotten. Never true while `max_lines` is `Some`.
    pub line_cap_off: bool,
    /// Shared vendor/generated exemption (emoji, length, shell-lint, secrets).
    pub exclude: String,
    /// Length-only exemption, additive to the length check.
    pub line_exclude: String,
    /// Extra permitted emoji characters.
    pub allow: String,
    /// Ratchet baseline for the exemption ceiling.
    pub line_baseline: Option<String>,
    /// Permanently unbounded exemptions.
    pub line_unbounded: String,
    /// Corpus root override (default: repo root).
    pub skills_root: Option<String>,
    /// Skills-corpus word cap.
    pub skills_max_words: Option<String>,
}

/// Parse one right-hand side: single/double-quoted or bare with `#` comment.
fn parse_value(rest: &str) -> String {
    if let Some(inner) = rest.strip_prefix('\'') {
        return inner.split('\'').next().unwrap_or("").to_owned();
    }
    if let Some(inner) = rest.strip_prefix('"') {
        return inner.split('"').next().unwrap_or("").to_owned();
    }
    if let Some(hash) = rest.find(" #") {
        return rest[..hash].trim_end().to_owned();
    }
    if let Some(hash) = rest.find('\t') {
        let (head, tail) = rest.split_at(hash);
        if tail.trim_start().starts_with('#') {
            return head.trim_end().to_owned();
        }
    }
    rest.to_owned()
}

/// Parse raw `KEY=value` pairs from `.gatesrc` text.
#[must_use]
pub fn parse_pairs(text: &str) -> BTreeMap<String, String> {
    let mut pairs = BTreeMap::new();
    for raw in text.lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        // `.gatesrc` is sourced by bash, where `export KEY=v` is the idiom
        // for a key that must reach child processes (three consumer repos
        // write it). Bash reads it as KEY=v; so must this parser — the
        // native side silently ignored the whole line and, with it, the
        // repo's GOH_EXCLUDE (ztools, 2026-09-14).
        let line = line.strip_prefix("export ").map_or(line, str::trim_start);
        let Some(eq) = line.find('=') else { continue };
        let key = line[..eq].trim().to_owned();
        if key.is_empty() {
            continue;
        }
        let rest = line[eq + 1..].trim();
        let value = parse_value(rest);
        pairs.insert(key, expand_env(&value));
    }
    pairs
}

/// Expand `$NAME` / `${NAME}` from the process environment.
#[must_use]
pub fn expand_env(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    let mut chars = value.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch != '$' {
            out.push(ch);
            continue;
        }
        match chars.peek() {
            Some('{') => {
                chars.next();
                let mut name = String::new();
                for c in chars.by_ref() {
                    if c == '}' {
                        break;
                    }
                    name.push(c);
                }
                out.push_str(&std::env::var(&name).unwrap_or_default());
            }
            Some(c) if c.is_ascii_alphanumeric() || *c == '_' => {
                let mut name = String::new();
                while let Some(c) = chars.peek() {
                    if c.is_ascii_alphanumeric() || *c == '_' {
                        name.push(*c);
                        chars.next();
                    } else {
                        break;
                    }
                }
                out.push_str(&std::env::var(&name).unwrap_or_default());
            }
            _ => out.push('$'),
        }
    }
    out
}

/// Build [`Gatesrc`] from parsed pairs.
///
/// # Errors
///
/// Returns a message when `GOH_MAX_LINES` is set but neither a number nor `off`
/// (the shell version fails downstream at the checker; failing here names the key).
pub fn from_pairs(pairs: &BTreeMap<String, String>) -> Result<Gatesrc, String> {
    let get = |key: &str| pairs.get(key).cloned().unwrap_or_default();
    let line_cap_off = pairs.get("GOH_MAX_LINES").is_some_and(|raw| raw == "off");
    let max_lines = match pairs.get("GOH_MAX_LINES") {
        None => None,
        Some(_) if line_cap_off => None,
        Some(raw) => match raw.parse::<usize>() {
            Ok(n) => Some(n),
            Err(_) => return Err(format!("GOH_MAX_LINES is not a number or `off`: {raw}")),
        },
    };
    Ok(Gatesrc {
        max_lines,
        line_cap_off,
        exclude: get("GOH_EXCLUDE"),
        line_exclude: get("GOH_LINE_EXCLUDE"),
        allow: get("GOH_ALLOW"),
        line_baseline: pairs.get("GOH_LINE_BASELINE").cloned(),
        line_unbounded: get("GOH_LINE_UNBOUNDED"),
        opt_ins: OPT_IN_KEYS
            .iter()
            .filter(|k| pairs.get(**k).is_some_and(|v| !v.is_empty()))
            .map(|k| (*k).to_owned())
            .collect(),
        skills_root: pairs.get("GOH_SKILLS_ROOT").cloned(),
        skills_max_words: pairs.get("GOH_SKILLS_MAX_WORDS").cloned(),
    })
}

/// Repo root for config resolution (`git` top level, else current dir).
#[must_use]
pub fn config_root() -> PathBuf {
    crate::gitutil::repo_root().map_or_else(
        || std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
        PathBuf::from,
    )
}

/// Load `.gatesrc` from the config root. Absent file means all defaults.
///
/// # Errors
///
/// Returns a message on unreadable files or invalid values.
pub fn load(root: &Path) -> Result<Gatesrc, String> {
    let path = root.join(".gatesrc");
    if !path.exists() {
        return Ok(Gatesrc::default());
    }
    let text = std::fs::read_to_string(&path).map_err(|e| format!(".gatesrc unreadable: {e}"))?;
    from_pairs(&parse_pairs(&text))
}

/// The length exemption is `GOH_EXCLUDE` ∪ `GOH_LINE_EXCLUDE`.
#[must_use]
pub fn length_exclude(cfg: &Gatesrc) -> String {
    match (cfg.exclude.as_str(), cfg.line_exclude.as_str()) {
        ("", "") => String::new(),
        ("", line) => line.to_owned(),
        (ex, "") => ex.to_owned(),
        (ex, line) => format!("{ex}|{line}"),
    }
}

/// Has this repo opted into the optional gate named by `key`?
///
/// Takes the key rather than exposing the set to membership tests, so a caller
/// cannot read the set and skip the question of whether the key is one this
/// build knows about. An unknown key answers `false`: a gate nothing reads
/// enables nothing, which is the safe direction, and the key set is pinned by a
/// test.
#[must_use]
pub fn opt_in(cfg: &Gatesrc, key: &str) -> bool {
    cfg.opt_ins.contains(key)
}

#[cfg(test)]
mod tests {
    /// The opt-in set is strings, so a mistyped key enables nothing and reads as
    /// "this repo did not choose that gate". This pins the two lists against
    /// each other: every key the code asks about must be one `OPT_IN_KEYS`
    /// declares, and nothing else may be.
    #[test]
    fn the_optional_gate_keys_are_the_ones_the_code_reads() {
        let src = String::from(include_str!("steps.rs"))
            + include_str!("steps_delegated.rs")
            + include_str!("main.rs");
        let asked: Vec<&str> = [
            "GOH_SKILLS_CORPUS",
            "GOH_NO_HOME_PATHS",
            "GOH_NO_KILL_BY_NAME",
            "GOH_PYTHON_FORMATTED",
            "GOH_CLAIM_DERIVATION",
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
}
