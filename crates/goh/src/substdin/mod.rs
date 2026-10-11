//! `goh subprocess-stdin` -- every child process this repo starts says where its stdin
//! comes from. Always-on structural step; see `calls.rs` for the rule and the incident
//! behind it, `allow.rs` for the ratchet.
//!
//! THE CLASS, in one line: a child inherits a stdin it did not ask for. `cat`, `sed`,
//! `md5sum`, `ffmpeg`, a `bash -c` program lifted out of build.sh, a nested gate suite --
//! any of them reads stdin when handed one, and whether that HANGS depends on the caller,
//! not on the line that is wrong. That is why it survives review: the call works every
//! time it runs from a hook or a test, and hangs the first time someone runs it from a
//! terminal or another tool pipes into it (`ZeroThunder`'s md5 probe hung the games
//! metarepo's `tools/check_claims.py`, 2026-10-05).
//!
//! WHY IT IS MEANT TO BE ALWAYS-ON, when three of its siblings are opt-in. Those opt in
//! because the defect is rare or the rule is arguable. This one's defect is universal, its
//! fix is one keyword, and the whole risk of turning it on is a repo's existing debt rather
//! than the gate: with a seeded ratchet (`--seed`) a NEW undeclared call is refused at the
//! commit that adds it, which is the only scope where that is still cheap. Until every
//! estate repo carries its seed it runs only where `GOH_SUBPROCESS_STDIN=on`; the default
//! flips once none would go red.
//!
//! FOUR WAYS THIS COULD PASS WITHOUT HAVING LOOKED, and each is closed:
//!
//!   * NO PYTHON. A repo that tracks no `.py` has no subject. It says "not applicable" and
//!     NAMES what it looked for -- never a bare pass, because "no Python here" and "Python
//!     I could not read" would otherwise print identically.
//!   * EVERY PYTHON FILE EXCLUDED. `GOH_EXCLUDE` that covers `tools/` makes the gate blind
//!     while the count reads clean. Named as its own verdict (`Outcome::tracked_python`
//!     against `Outcome::examined`), never as "not applicable".
//!   * A `.py` it COULD NOT PARSE. A finding, not a skip (see `calls::scan_source`).
//!   * A SCAN THAT STOPPED SEEING CALLS. `GOH_SUBPROCESS_STDIN_MIN_CALLS` states this
//!     repo's floor; below it the step is red and says the scan lost its subject. The
//!     floor is per-repo because a hardcoded one is wrong everywhere else -- 80 was half
//!     of `ZeroThunder`'s 165 and would red every small repo in the estate.
//!
//! THE FLOOR IS FULL-SCOPE ONLY. At `--staged` the listing is the files THIS COMMIT
//! brings, so a commit touching one helper sees one call and would be red for its own
//! scope. The stage that matters -- "did this commit add an undeclared call" -- is exact
//! at both scopes, because an unallowed finding is unallowed wherever it is read from.

pub mod allow;
pub mod calls;
pub mod seed;
mod verdict;

pub use verdict::report;

use goh_tui::{ERR, START};
use std::path::Path;

/// The tag every line this check prints carries.
const TAG: &str = "[subprocess_stdin]";

/// Findings shown before the rest are counted. A repo with 500 seeded exemptions and one
/// new violation needs to see the NEW one, not 40 of the seeded ones.
const MAX_SHOWN: usize = 40;

/// What one scan of a tree found.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Outcome {
    /// Tracked `.py` files, before any exclusion: the scope the repo declares.
    pub tracked_python: usize,
    /// Python files actually read.
    pub examined: usize,
    /// Process calls seen, declared or not: the floor's subject.
    pub seen: usize,
    /// Repo-relative path and finding, in source order.
    pub findings: Vec<(String, calls::Finding)>,
    /// A tracked `.py` this check could not parse: `(path, the parser's message)`.
    pub unreadable: Vec<(String, String)>,
}

/// Is `rel` a Python source this check reads?
fn is_python(rel: &str) -> bool {
    // `eq_ignore_ascii_case`: a tracked `A.PY` is Python to every tool that runs it, and a
    // gate that skipped it would leave the one file whose case nobody normalises unchecked.
    Path::new(rel)
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("py"))
}

/// Is `rel` one of this check's own allowlist files, which are DATA about the gate and
/// must never be scanned as the gate's subject? (They spell `subprocess.run` on purpose.)
fn is_allow_file(rel: &str) -> bool {
    rel == allow::ALLOW_FILE || rel.starts_with(&format!("{}/", allow::ALLOW_DIR))
}

/// The listed files that are in scope: `.py`, minus the allowlist itself.
fn in_scope(files: &[String]) -> Vec<String> {
    files
        .iter()
        .filter(|f| is_python(f) && !is_allow_file(f))
        .cloned()
        .collect()
}

/// Scan the Python in `files` (the listing after `GOH_EXCLUDE`); count it in `listed` (before).
///
/// Counted from `files`, an exclusion that removed every `.py` read as "this repo tracks no
/// Python" and passed -- the blind case the module names. `root` reads the working tree, or the
/// index at `staged`.
#[must_use]
pub fn scan(root: &Path, listed: &[String], files: &[String], staged: bool) -> Outcome {
    let scope = in_scope(files);
    let mut out = Outcome {
        tracked_python: in_scope(listed).len(),
        ..Outcome::default()
    };
    for rel in scope {
        let Some(blob) = crate::gitutil::content_bytes(root, &rel, staged) else {
            continue;
        };
        // The same NUL window every other scanner reads: a binary named `.py` is not source.
        if blob[..blob.len().min(crate::gitutil::BINARY_SCAN_WINDOW)].contains(&0) {
            continue;
        }
        match calls::scan_source(&String::from_utf8_lossy(&blob)) {
            Err(reason) => out.unreadable.push((rel.clone(), reason)),
            Ok((found, seen)) => {
                out.examined += 1;
                out.seen += seen;
                out.findings
                    .extend(found.into_iter().map(|f| (rel.clone(), f)));
            }
        }
    }
    out
}

/// Everything the verdict needs beyond the scan: the exclusions and the floor. Grouped
/// into one struct so the verdict's argument list cannot grow a boolean at a time.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Policy {
    /// `GOH_EXCLUDE`, the pattern that took files out of scope (named in the verdict).
    pub exclude: String,
    /// `GOH_SUBPROCESS_STDIN_MIN_CALLS`.
    pub min_calls: Option<usize>,
}

impl Policy {
    /// The policy from a loaded `.gatesrc` and the exclusion this run applies (`--exclude` when given,
    /// so a verdict names the exclusion in effect, not the file's). An
    /// unparseable floor is an ERROR naming the key, never a floor of 0: a typo that
    /// silently disables the floor is the defect the floor exists to prevent
    /// (`GOH_STEP_TIMEOUT`'s rule).
    fn from_cfg(cfg: &crate::gatesrc::Gatesrc, exclude: &str) -> Result<Self, String> {
        let min_calls = cfg
            .stdin_min_calls
            .as_deref()
            .map(str::trim)
            .filter(|raw| !raw.is_empty())
            .map(str::parse::<usize>)
            .transpose()
            .map_err(|_| {
                format!(
                    "GOH_SUBPROCESS_STDIN_MIN_CALLS is not a whole number of calls: {:?}",
                    cfg.stdin_min_calls
                )
            })?;
        Ok(Self {
            exclude: exclude.to_owned(),
            min_calls,
        })
    }
}

/// The floor and the exclusions from the repo's `.gatesrc`.
fn policy(root: &Path, exclude: &str) -> Result<Policy, String> {
    Policy::from_cfg(&crate::gatesrc::load(root)?, exclude)
}

/// The files in scope, filtered by the repo's `GOH_EXCLUDE` -- the same one every other
/// step applies, resolved the same way (`--exclude` beats the file; the file beats the
/// ambient environment).
fn scoped(listed: &[String], exclude: &str) -> Result<Vec<String>, String> {
    let filter = crate::steps::compile_exclude(exclude).map_err(|m| format!("{m}\n"))?;
    Ok(listed
        .iter()
        .filter(|f| !filter.as_ref().is_some_and(|x| x.is_match(f)))
        .cloned()
        .collect())
}

/// `goh subprocess-stdin [--staged] [--exclude RE]`, or `--source PATH` to judge one file
/// from the working tree as JSON (a repo's own verify step, and the test harness).
#[must_use]
pub fn run_command(staged: bool, exclude: Option<&str>, source: Option<&str>, reseed: bool) -> i32 {
    if let Some(path) = source {
        let text = match std::fs::read_to_string(path) {
            Ok(t) => t,
            Err(e) => {
                goh_tui::eoutln!("{ERR} {TAG} cannot read {path}: {e}");
                return 2;
            }
        };
        return match calls::scan_source(&text) {
            Err(reason) => {
                goh_tui::eoutln!("{ERR} {TAG} {path} is not Python this check can read: {reason}");
                2
            }
            Ok((found, seen)) => {
                goh_tui::out(&calls::findings_json(path, &found, seen));
                i32::from(!found.is_empty())
            }
        };
    }
    let (code, text) = run(staged && !reseed, exclude, reseed).unwrap_or_else(|e| e);
    goh_tui::out(&text);
    code
}

/// One whole-tree run: (exit code, report). Every error path names itself; none of them
/// reports clean.
fn run(staged: bool, exclude: Option<&str>, reseed: bool) -> Result<(i32, String), (i32, String)> {
    let repo = crate::gitutil::repo_root().map_or_else(
        || std::env::current_dir().unwrap_or_default(),
        std::path::PathBuf::from,
    );
    let listed = crate::gitutil::listed_files(&repo, staged).map_err(|m| {
        (
            2,
            format!(
                "{ERR} {TAG} {m}\n{START} A git call that FAILS is not an empty tree. Fix the index, or the \
                 gate is not running.\n"
            ),
        )
    })?;
    let exclude = match exclude {
        Some(given) => given.to_owned(),
        None => crate::gatesrc::declared_exclude(None, Some(&repo), crate::gatesrc::Exempt::Paths)
            .map_err(|m| (2, format!("{ERR} {TAG} {m}\n")))?,
    };
    let policy = policy(&repo, &exclude).map_err(|m| (2, format!("{ERR} {TAG} {m}\n")))?;
    let files = scoped(&listed, &exclude).map_err(|m| (2, format!("{ERR} {TAG} {m}")))?;
    if reseed {
        return Ok(seed::run(&repo, &listed, &files));
    }
    let outcome = scan(&repo, &listed, &files, staged);
    Ok(report(&outcome, &repo, &listed, staged, &policy))
}

/// Whether the step runs: `GOH_SUBPROCESS_STDIN` is `on`, or `off`/unset (the default until
/// the estate is seeded). Anything else is an error naming the key, never a silent off: a
/// typo that disabled the gate is the defect the gate exists to prevent.
fn enabled(raw: Option<&str>) -> Result<bool, String> {
    match raw.map_or("", str::trim) {
        "" | "off" => Ok(false),
        "on" => Ok(true),
        other => Err(format!(
            "GOH_SUBPROCESS_STDIN is neither on nor off: {other:?}"
        )),
    }
}

/// The structural step, at both scopes, where `GOH_SUBPROCESS_STDIN=on`.
///
/// Takes the pipeline's ONE enumeration (`structural::run` shares it with every other native
/// scanner -- `git ls-files` costs a spawn each time it is asked) rather than asking again, so
/// adding this step cost zero git spawns.
#[must_use]
pub fn step(cfg: &crate::gatesrc::Gatesrc, listed: &[String], staged: bool) -> Option<i32> {
    let on = enabled(cfg.stdin_gate.as_deref());
    if on == Ok(false) {
        return None;
    }
    let label = if staged {
        "every child process names its stdin (staged)"
    } else {
        "every child process names its stdin"
    };
    let start = crate::step_report::begin(label);
    let repo = crate::gatesrc::config_root();
    let how = crate::step_report::ported("crates/goh/src/substdin/mod.rs");
    if let Err(message) = on {
        return Some(fail(
            label,
            &how,
            &format!("{ERR} {TAG} {message}\n"),
            start,
        ));
    }
    let policy = match Policy::from_cfg(cfg, &cfg.exclude) {
        Ok(policy) => policy,
        Err(message) => {
            return Some(fail(
                label,
                &how,
                &format!("{ERR} {TAG} {message}\n"),
                start,
            ))
        }
    };
    let files = match scoped(listed, &cfg.exclude) {
        Ok(files) => files,
        Err(message) => {
            return Some(fail(
                label,
                &how,
                &format!("{ERR} {TAG} {message}\n"),
                start,
            ))
        }
    };
    let outcome = scan(&repo, listed, &files, staged);
    let (code, text) = report(&outcome, &repo, listed, staged, &policy);
    if code == 0 {
        crate::step_report::ok(label, start);
        None
    } else {
        Some(fail(label, &how, &text, start))
    }
}

/// One red step, so every return above reads the same.
fn fail(label: &str, how: &str, text: &str, start: std::time::Instant) -> i32 {
    crate::step_report::fail(label, how, text, start)
}

#[cfg(test)]
mod tests;
