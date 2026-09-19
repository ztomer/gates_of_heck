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

/// Structural knobs read from `.gatesrc`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Gatesrc {
    /// File-length cap; unset disables the check.
    pub max_lines: Option<usize>,
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
    /// Skills-corpus gate enabled.
    pub skills_corpus: bool,
    /// Hard-coded-home-path gate enabled (`GOH_NO_HOME_PATHS`).
    pub no_home_paths: bool,
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
/// Returns a message when `GOH_MAX_LINES` is set but not a number (the shell
/// version fails downstream at the checker; failing here names the key).
pub fn from_pairs(pairs: &BTreeMap<String, String>) -> Result<Gatesrc, String> {
    let get = |key: &str| pairs.get(key).cloned().unwrap_or_default();
    let max_lines = match pairs.get("GOH_MAX_LINES") {
        None => None,
        Some(raw) => match raw.parse::<usize>() {
            Ok(n) => Some(n),
            Err(_) => return Err(format!("GOH_MAX_LINES is not a number: {raw}")),
        },
    };
    Ok(Gatesrc {
        max_lines,
        exclude: get("GOH_EXCLUDE"),
        line_exclude: get("GOH_LINE_EXCLUDE"),
        allow: get("GOH_ALLOW"),
        line_baseline: pairs.get("GOH_LINE_BASELINE").cloned(),
        line_unbounded: get("GOH_LINE_UNBOUNDED"),
        skills_corpus: pairs
            .get("GOH_SKILLS_CORPUS")
            .is_some_and(|v| !v.is_empty()),
        no_home_paths: pairs
            .get("GOH_NO_HOME_PATHS")
            .is_some_and(|v| !v.is_empty()),
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

#[cfg(test)]
mod tests {
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
    fn length_union_is_additive() {
        let mut cfg = Gatesrc::default();
        assert_eq!(length_exclude(&cfg), "");
        cfg.exclude = "vendor/".to_owned();
        assert_eq!(length_exclude(&cfg), "vendor/");
        cfg.line_exclude = "third_party/".to_owned();
        assert_eq!(length_exclude(&cfg), "vendor/|third_party/");
    }
}
