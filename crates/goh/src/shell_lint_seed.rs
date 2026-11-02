//! The shell lint's ratchet: shellcheck findings below `error` severity, held to a seed.
//!
//! The gate ran `shellcheck --severity=error`, so a warning (an unused variable, an unquoted
//! `$(...)`, `cd` without `|| exit`) never reached any repo's gate. It now runs at the severity
//! `.gatesrc`'s `GOH_SHELL_LINT_SEVERITY` names (default `warning`). An ERROR is never seedable:
//! it fails outright, as it always did. Everything below it is debt, held by
//! `shell_lint_seed.json` at the repo root: per `file#SCnnnn`, how many findings of that code the
//! file had when the repo adopted the check. It may only fall -- a finding over its count fails
//! with its sites, a finding under it fails until the seed is lowered (`goh shell-lint --seed`),
//! and an entry for a file or code gone clean is stale and fails. The four directions are
//! `crate::seedcount`'s, shared with `goh fixed-sleep`.
//!
//! The key carries the CODE, not only the file, so a fixed `SC2034` cannot leave room for a new
//! `SC2086` in the same script.

use crate::seedcount::{self, Scanned, Spec};
use std::collections::BTreeMap;
use std::path::Path;

/// The seed's file name, at the repo root.
pub const SEED_FILE: &str = "shell_lint_seed.json";

/// The severities `GOH_SHELL_LINT_SEVERITY` names, as shellcheck's `--severity` spells them.
const SEVERITIES: [&str; 4] = ["error", "warning", "info", "style"];

/// The `.gatesrc` key that picks the lowest severity the gate judges.
pub const SEVERITY_KEY: &str = "GOH_SHELL_LINT_SEVERITY";

const SPEC: Spec = Spec {
    tag: "[shell_lint]",
    file: SEED_FILE,
    noun: "shell file(s)",
    reseed: "goh shell-lint --seed",
    clean: "no new shellcheck finding below error",
    why: "shellcheck findings below error severity when this repo adopted the ratcheted shell \
          lint, per file#code; a count may only fall (gates_of_heck GOH.SHSEV)",
    subject: &crate::subject::SHELL_LINT,
};

/// One shellcheck finding below `error`: `line:col: severity: message [SCnnnn]`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding(pub String);

/// The lowest severity the gate judges.
///
/// `.gatesrc`'s `GOH_SHELL_LINT_SEVERITY` (default `warning`). A value shellcheck has no such severity for is an error NAMING THE KEY, never a
/// silent fallback -- a typo that quietly lowered the gate is the defect this gate exists to stop.
///
/// # Errors
///
/// The refusal naming the key and the values it takes.
pub fn severity(root: &Path) -> Result<&'static str, String> {
    let pairs = std::fs::read_to_string(root.join(".gatesrc"))
        .map(|t| crate::gatesrc::parse_pairs(&t))
        .unwrap_or_default();
    let Some(want) = pairs.get(SEVERITY_KEY) else {
        return Ok("warning");
    };
    SEVERITIES
        .iter()
        .find(|s| **s == want.as_str())
        .copied()
        .ok_or_else(|| {
            format!(
                "{SEVERITY_KEY}={want} is not a shellcheck severity (one of {})",
                SEVERITIES.join(", ")
            )
        })
}

/// A shellcheck line for `display`, split: `(rest after the path, code)` when it is a finding
/// BELOW error that names its code; `None` for an error, a tool error, or a line another file owns.
#[must_use]
pub fn finding_of<'a>(line: &'a str, display: &str) -> Option<(&'a str, &'a str)> {
    let rest = line.strip_prefix(display)?.strip_prefix(':')?;
    let mut parts = rest.splitn(4, ": ");
    let (_pos, sev) = (parts.next()?, parts.next()?);
    if sev == "error" {
        return None;
    }
    let code = rest.rsplit_once(" [")?.1.strip_suffix(']')?;
    (code.starts_with("SC") && code[2..].bytes().all(|b| b.is_ascii_digit()))
        .then_some((rest, code))
}

/// Group findings as the seed keys them: `file#code`.
pub fn add(found: &mut BTreeMap<String, Vec<Finding>>, display: &str, code: &str, rest: &str) {
    found
        .entry(format!("{display}#{code}"))
        .or_default()
        .push(Finding(rest.to_owned()));
}

fn site(f: &Finding) -> String {
    f.0.clone()
}

/// The scan the files read imply.
const fn scanned(read: usize, found: BTreeMap<String, Vec<Finding>>) -> Scanned<Finding> {
    Scanned {
        listed: read,
        read,
        floor: None,
        found,
        unreadable: Vec::new(),
    }
}

/// The verdict over `found` and the repo's seed: `(exit code, report)`.
///
/// At `--staged` only the entries for the files the commit brings are judged (`paths`); the rest are not in scope, so
/// they are neither stale nor over.
///
/// # Errors
///
/// A seed that cannot be read.
pub fn judge(
    root: &Path,
    paths: &[String],
    found: BTreeMap<String, Vec<Finding>>,
    staged: bool,
    floor: Option<usize>,
) -> Result<(i32, String), String> {
    let mut seed = seedcount::load_seed(&SPEC, root)?;
    if staged {
        seed.retain(|k, _| {
            paths
                .iter()
                .any(|p| k.rsplit_once('#').is_some_and(|(f, _)| f == p))
        });
    }
    let mut scan = scanned(paths.len(), found);
    scan.floor = floor;
    Ok(seedcount::judge(&SPEC, &scan, &seed, site))
}

/// The verdict once nothing always-fatal stands: write the seed (`seed`), or judge `found`
/// against it. `(exit code, (to stderr, line))`, in the shape the gate prints.
#[must_use]
pub fn conclude(
    root: &Path,
    paths: &[String],
    found: BTreeMap<String, Vec<Finding>>,
    staged: bool,
    seed: bool,
) -> (i32, Vec<(bool, String)>) {
    use goh_tui::{ERR, OK};
    let floor = if staged {
        None
    } else {
        crate::gatesrc::load(root)
            .ok()
            .and_then(|c| crate::subject::floor(&c, &crate::subject::SHELL_LINT))
    };
    if seed {
        return match write_seed(root, paths.len(), found, floor) {
            Ok(text) => (0, vec![(false, format!("{OK} [shell_lint] {text}"))]),
            Err(text) => (1, vec![(true, text.trim_end().to_owned())]),
        };
    }
    let held: usize = found.values().map(Vec::len).sum();
    match judge(root, paths, found, staged, floor) {
        Err(e) => (2, vec![(true, format!("{ERR} [shell_lint] {e}"))]),
        Ok((0, _)) => {
            let scope = if staged { "staged" } else { "tracked" };
            let held = if held > 0 {
                format!(", {held} seeded below error")
            } else {
                String::new()
            };
            let n = paths.len();
            let line = format!("{OK} [shell_lint] OK — {n} {scope} shell files clean{held}");
            (0, vec![(false, line)])
        }
        Ok((code, text)) => (code, text.lines().map(|l| (true, l.to_owned())).collect()),
    }
}

/// Write the seed the scan implies, or refuse (a blind scan must not be written as the debt).
///
/// # Errors
///
/// The refusal text, or the write failure.
pub fn write_seed(
    root: &Path,
    read: usize,
    found: BTreeMap<String, Vec<Finding>>,
    floor: Option<usize>,
) -> Result<String, String> {
    let mut scan = scanned(read, found);
    scan.floor = floor;
    if let Some(text) = seedcount::refuse_seed(&SPEC, &scan) {
        return Err(text);
    }
    std::fs::write(root.join(SEED_FILE), seedcount::seed_text(&SPEC, &scan))
        .map_err(|e| format!("cannot write {SEED_FILE}: {e}"))?;
    let total: usize = scan.found.values().map(Vec::len).sum();
    Ok(format!(
        "seeded {SEED_FILE}: {total} finding(s) under {} file#code key(s), of {read} shell file(s) read",
        scan.found.len()
    ))
}

#[cfg(test)]
mod tests {
    use super::{add, finding_of, severity};
    use std::collections::BTreeMap;

    const WARN: &str =
        "a.sh:2:1: warning: x appears unused. Verify use (or export if used externally). [SC2034]";

    #[test]
    fn a_warning_is_a_finding_keyed_by_file_and_code() {
        let (rest, code) = finding_of(WARN, "a.sh").expect("a finding");
        assert_eq!(code, "SC2034");
        let mut found = BTreeMap::new();
        add(&mut found, "a.sh", code, rest);
        assert_eq!(found.keys().collect::<Vec<_>>(), ["a.sh#SC2034"]);
        assert!(found["a.sh#SC2034"][0].0.starts_with("2:1: warning:"));
    }

    #[test]
    fn an_error_a_tool_line_and_another_files_line_are_not_findings() {
        assert!(finding_of(
            "a.sh:1:1: error: Tips depend on target shell. [SC2148]",
            "a.sh"
        )
        .is_none());
        assert!(finding_of("shellcheck: cannot read", "a.sh").is_none());
        assert!(finding_of(WARN, "b.sh").is_none());
        assert!(finding_of("a.sh:2:1: warning: no code at all", "a.sh").is_none());
        assert!(finding_of("a.sh:2:1: warning: odd [SCx]", "a.sh").is_none());
    }

    #[test]
    fn the_severity_is_warning_unless_a_known_one_is_written() {
        let dir = std::env::temp_dir().join(format!("shsev.{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("dir");
        assert_eq!(severity(&dir), Ok("warning"));
        for want in ["error", "warning", "info", "style"] {
            std::fs::write(
                dir.join(".gatesrc"),
                format!("GOH_SHELL_LINT_SEVERITY={want}\n"),
            )
            .expect("w");
            assert_eq!(severity(&dir), Ok(want));
        }
        std::fs::write(dir.join(".gatesrc"), "GOH_SHELL_LINT_SEVERITY=warn\n").expect("w");
        assert!(severity(&dir)
            .expect_err("refused")
            .contains("GOH_SHELL_LINT_SEVERITY"));
        std::fs::remove_dir_all(&dir).expect("rm");
    }
}
