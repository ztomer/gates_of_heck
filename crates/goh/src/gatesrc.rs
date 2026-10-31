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
pub const OPT_IN_KEYS: [&str; 6] = [
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
    // Closes the early-exit-pipe ratchet: every finding fails, not only a new one. The step
    // itself runs in every repo (a NEW racy pipe is refused at commit); this is the switch a
    // repo throws once its tree is clean.
    "GOH_NO_EARLY_EXIT_PIPE",
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
    /// `goh requires-call`'s rules file, repo-relative (unset: the step does not run).
    pub requires_call: Option<String>,
    /// Every scanner's declared floor (`GOH_<SCANNER>_MIN_<UNIT>`, `crate::subject`), parsed:
    /// how much of its subject a full-scope scan must read before a clean run is trusted. An
    /// absent key is absent (the gate says so); a value that is not a whole number refuses the
    /// whole file NAMING THE KEY, never a floor of zero -- a typo that silently disables a floor
    /// is the defect the floor exists to prevent.
    pub floors: std::collections::BTreeMap<&'static str, usize>,
    /// `GOH_SUBPROCESS_STDIN`: unset or `on` runs `goh subprocess-stdin` in the structural
    /// pipeline, and only a written `off` skips it. Off by default until 2026-10-13; flipped
    /// ahead of the estate sweep by the owner's ruling that consumers clear their own debt
    /// (BACKLOG 3.4). Raw, so a value that is neither is an error naming the key.
    pub stdin_gate: Option<String>,
    /// `GOH_JS_SYNTAX`: unset or `off` leaves the JavaScript parse gate off, and only a written
    /// `on` runs `goh js-syntax` in the structural pipeline. Raw, so a value that is neither is
    /// an error naming the key -- a typo that disabled the gate would be the defect the gate
    /// exists to prevent. Opt-in because most repositories in the estate track no JavaScript at
    /// all, and a gate with no subject is a gate that has to say "not applicable" forever.
    pub js_syntax_gate: Option<String>,
    /// `GOH_BARE_PYTHON_SCOPE`: a regex on repo-relative paths ADDED to the bare-python step's
    /// scope (the hooks and `tools/gate.sh`). Empty: the default scope alone.
    pub bare_python_scope: String,
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
/// (the shell version fails downstream at the checker; failing here names the key), or when a
/// scanner's floor (`crate::subject`) is not a whole number.
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
        requires_call: pairs
            .get("GOH_REQUIRES_CALL")
            .filter(|v| !v.is_empty())
            .cloned(),
        floors: crate::subject::parse_floors(pairs)?,
        stdin_gate: pairs.get("GOH_SUBPROCESS_STDIN").cloned(),
        js_syntax_gate: pairs.get("GOH_JS_SYNTAX").cloned(),
        bare_python_scope: get("GOH_BARE_PYTHON_SCOPE"),
    })
}

/// Every structural `.gatesrc` key, spelled once: the pipeline's configuration.
///
/// A key is configuration if and only if the repo's own file says so. Measured
/// 2026-10-02: a push of one repo reached the export gate with that repo's real
/// `GOH_SKILLS_ROOT`, `GOH_SKILLS_CORPUS` and `GOH_PYTHON_FORMATTED` in its
/// environment, and 18 fixture tests went red on a clean tree.
/// [`adopt_into_env`] makes the environment AGREE with the file for exactly these
/// keys, so a step that reads one from the environment (`GOH_STEP_TIMEOUT`, and
/// every child it spawns) sees the file's value, never an inherited one.
/// Pinned against `docs/config.md` by `tests/test_gate_environment.py`.
pub const PIPELINE_KEYS: [&str; 39] = [
    "GOH_MAX_LINES",
    "GOH_LINE_EXCLUDE",
    "GOH_LINE_BASELINE",
    "GOH_LINE_UNBOUNDED",
    "GOH_EXCLUDE",
    "GOH_ALLOW",
    "GOH_SKILLS_CORPUS",
    "GOH_SKILLS_ROOT",
    "GOH_SKILLS_MAX_WORDS",
    "GOH_NO_HOME_PATHS",
    "GOH_NO_KILL_BY_NAME",
    "GOH_PYTHON_FORMATTED",
    "GOH_CLAIM_DERIVATION",
    "GOH_NO_EARLY_EXIT_PIPE",
    "GOH_STEP_TIMEOUT",
    "GOH_STEP_GRACE",
    "GOH_REQUIRES_CALL",
    "GOH_SUBPROCESS_STDIN_MIN_CALLS",
    "GOH_SUBPROCESS_STDIN",
    // Every scanner's floor (`crate::subject::ALL`; a unit test pins the two lists).
    "GOH_EMOJI_MIN_FILES",
    "GOH_CONFLICT_MARKERS_MIN_FILES",
    "GOH_FILE_LENGTH_MIN_FILES",
    "GOH_SECRETS_MIN_FILES",
    "GOH_SHELL_LINT_MIN_FILES",
    "GOH_EARLY_EXIT_PIPE_MIN_FILES",
    "GOH_DEAD_AFTER_EXEC_MIN_FILES",
    "GOH_BARE_HOOK_INDEX_MIN_FILES",
    "GOH_BARE_PYTHON_MIN_FILES",
    "GOH_BARE_PYTHON_SCOPE",
    "GOH_CHECKOUT_CREDENTIALS_MIN_FILES",
    "GOH_TAP_WRITES_MIN_FILES",
    "GOH_MOVING_TAGS_MIN_REFS",
    "GOH_MD_LINKS_MIN_LINKS",
    "GOH_UNREAPED_SPAWN_MIN_FILES",
    "GOH_FIXED_SLEEP_MIN_FILES",
    "GOH_STATUS_MARKERS_MIN_FILES",
    "GOH_JS_SYNTAX",
    "GOH_JS_SYNTAX_MIN_FILES",
    "GOH_BUSY_LOOP_MIN_FILES",
];

/// Make the process environment say what `root/.gatesrc` says for every
/// [`PIPELINE_KEYS`] key: an inherited value is dropped, a declared one set.
/// Called once, before any thread or child starts.
pub fn adopt_into_env(root: &Path) {
    let pairs = std::fs::read_to_string(root.join(".gatesrc"))
        .map(|t| parse_pairs(&t))
        .unwrap_or_default();
    for key in PIPELINE_KEYS {
        match pairs.get(key) {
            Some(value) => std::env::set_var(key, value),
            None => std::env::remove_var(key),
        }
    }
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

/// Which exemption a direct `goh <check>` applies when no `--exclude` is given.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Exempt {
    /// `GOH_EXCLUDE`, as every structural step but the length cap reads it.
    Paths,
    /// `GOH_EXCLUDE` ∪ `GOH_LINE_EXCLUDE`, the length cap's (see [`length_exclude`]).
    Length,
    /// `GOH_ALLOW`, the extra glyphs the emoji scan permits: a direct `goh emoji` defaulted its
    /// `--allow` to nothing and refused what the repo's structural gate passed (BACKLOG 6.17).
    Glyphs,
}

/// The exclusion a direct `goh <check>` applies: `--exclude` when given (an empty one opts out),
/// else the exemption the repo DECLARES in its `.gatesrc` -- the value its structural gate passes.
///
/// A direct check ignored the file, so `goh.sh md-links` in `app_updates` reported a vendored
/// crate's dead anchor that its own gate exempts, and every sweep that calls checks directly
/// judged the repo differently from the repo (2026-10-08). One door, one verdict. Never the
/// ambient environment: an inherited `GOH_EXCLUDE` is process state the repo never chose (the
/// hostile-environment rule, `tests/test_goh_structural.py`). `root` is the check's own `--root`
/// when it has one, else the repo the command runs in.
///
/// # Errors
/// The repo's `.gatesrc` is unreadable or carries an invalid value.
pub fn declared_exclude(
    flag: Option<String>,
    root: Option<&Path>,
    which: Exempt,
) -> Result<String, String> {
    if let Some(given) = flag {
        return Ok(given);
    }
    let cfg = load(&root.map_or_else(config_root, Path::to_path_buf))?;
    Ok(match which {
        Exempt::Paths => cfg.exclude,
        Exempt::Length => length_exclude(&cfg),
        Exempt::Glyphs => cfg.allow,
    })
}

/// The skills corpus's word ceiling: `GOH_SKILLS_MAX_WORDS`, else the built-in one.
///
/// Read here for both entry points -- a direct `goh skills` ignored the key its structural step
/// honoured (BACKLOG 6.17, the sibling of `GOH_ALLOW`).
///
/// # Errors
/// The key is set to something that is not a whole number.
pub fn skills_max_words(cfg: &Gatesrc) -> Result<usize, String> {
    cfg.skills_max_words
        .as_ref()
        .map_or(Ok(crate::skills::DEFAULT_MAX_WORDS), |raw| {
            raw.parse()
                .map_err(|_| format!("bad GOH_SKILLS_MAX_WORDS value: {raw:?}"))
        })
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
#[path = "gatesrc_tests.rs"]
mod tests;
