//! No vendored copy of a house checker or of the house TUI (SUPERSOTA R5, GOH.VENDOR).
//!
//! A copy of `check_no_allow.py` in a consumer's `tools/` is frozen at the day it was copied: four
//! repos carried one until 2026-09-14, and divoom-control carried `tui/lib.sh`, `tui/lib.py` and
//! `tui/stylerc` until its `8e05425` -- already drifted from the shared library (no single-write
//! `report`, no bootstrap seam), which is exactly why a NAME or a HASH cannot be the test: a copy
//! that was renamed, or edited once, is still frozen. The shared checkout is the only copy (hooks
//! delegate to it at runtime), so a file is a copy when
//!
//!   * its NAME is a house checker's (`checks/*.py`, `checks/*.sh`, or a checker Phase N3 retired --
//!     a copy of one of those is the most frozen of all), or
//!   * its CONTENT is most of a house file's: of the house file's distinctive lines (whitespace
//!     collapsed, at least [`MIN_LEN`] characters, so `fi` and `}` are no evidence), the candidate
//!     holds at least [`MIN_SHARE_PCT`] percent and at least [`MIN_LINES`] of them. The house files
//!     are `tui/lib.sh`, `tui/lib.py`, `tui/stylerc` and every `checks/*` script with that many
//!     distinctive lines. Calibrated 2026-11-08 over the 44-repo estate: divoom's drifted copies
//!     held 64 % (`lib.py`), 74 % (`lib.sh`) and 100 % (`stylerc`) of the house lines; the most any
//!     file of a consumer's own held was 14 % (11 lines of `lib.sh` in scripts' `tui_fallback.sh`).
//!
//! It is REFUSED at both scopes. Until GOH.VENDOR a full-scope run only NAMED an existing copy, as a
//! migration for that repo rather than a reason to turn every gate red at once; the house rule is
//! never vendor, and the estate held no copy when the grace ended (measured the same day), so the
//! grace was a hole with nothing left in it. At `--staged` every file the commit brings is judged, an
//! edit included: an edit to a copy is still a copy. `gates_of_heck` itself -- any checkout,
//! worktree or export of it -- is the one tree where those files belong.

use goh_tui::ERR;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::fmt::Write as _;
use std::path::Path;

use crate::step_report::{begin, fail, ok};

const LABEL: &str = "no vendored copies of house files";

/// A line shorter than this (whitespace collapsed) is no evidence of copying.
pub const MIN_LEN: usize = 12;
/// A candidate must hold at least this many of a house file's distinctive lines ...
pub const MIN_LINES: usize = 8;
/// ... and at least this share of them, in percent.
pub const MIN_SHARE_PCT: usize = 40;
/// A file larger than this is not read for content (no house file is near it).
const MAX_BYTES: usize = 1 << 20;
/// The house TUI files, judged by content.
const TUI: [&str; 3] = ["tui/lib.sh", "tui/lib.py", "tui/stylerc"];

/// The checkers Phase N3 retired. Pinned equal to `checks/_retired.py::NATIVE` by
/// `tests/test_vendored_copies.py`, so the two spellings cannot drift.
pub const RETIRED: [&str; 23] = [
    "check_no_emoji",
    "check_no_conflict_markers",
    "check_file_length",
    "check_no_secrets",
    "check_no_home_paths",
    "check_no_allow",
    "check_no_empty_assert",
    "check_no_screen_presentation",
    "check_lints_optin",
    "check_skills_corpus",
    "check_dep_currency",
    "check_no_unreaped_spawn",
    "check_version_provenance",
    "check_no_kill_by_name",
    "check_claim_derivation",
    "check_md_links",
    "check_lock_version",
    "check_tag_version",
    "check_no_credential_urls",
    "check_python_formatted",
    "check_shell_lint",
    "check_exclusion_has_ceiling",
    "check_subprocess_stdin",
];

/// Every basename a house checker is shipped under.
fn house_names(goh: &Path) -> BTreeSet<String> {
    let mut names: BTreeSet<String> = std::fs::read_dir(goh.join("checks"))
        .map(|dir| {
            dir.filter_map(Result::ok)
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .filter(|n| {
                    let b = n.as_bytes();
                    b.ends_with(b".py") || b.ends_with(b".sh")
                })
                .collect()
        })
        .unwrap_or_default();
    for stem in RETIRED {
        names.insert(format!("{stem}.py"));
    }
    names.insert("check_shell_lint.sh".to_owned());
    names
}

/// A text's distinctive lines: whitespace collapsed, at least [`MIN_LEN`] characters.
fn distinctive(text: &str) -> HashSet<String> {
    text.lines()
        .map(|l| l.split_whitespace().collect::<Vec<_>>().join(" "))
        .filter(|l| l.chars().count() >= MIN_LEN)
        .collect()
}

/// The house files judged by content: each one's path under the shared checkout and its lines.
struct House {
    files: Vec<(String, usize)>,
    /// distinctive line -> the indexes in `files` that hold it.
    index: HashMap<String, Vec<usize>>,
}

impl House {
    fn load(goh: &Path) -> Self {
        let mut rels: Vec<String> = TUI.iter().map(|r| (*r).to_owned()).collect();
        if let Ok(dir) = std::fs::read_dir(goh.join("checks")) {
            let mut checks: Vec<String> = dir
                .filter_map(Result::ok)
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .filter(|n| {
                    std::path::Path::new(n)
                        .extension()
                        .is_some_and(|x| x == "py" || x == "sh")
                })
                .map(|n| format!("checks/{n}"))
                .collect();
            checks.sort();
            rels.extend(checks);
        }
        let mut house = Self {
            files: Vec::new(),
            index: HashMap::new(),
        };
        for rel in rels {
            let Ok(text) = std::fs::read_to_string(goh.join(&rel)) else {
                continue;
            };
            let lines = distinctive(&text);
            if lines.len() < MIN_LINES {
                continue;
            }
            let at = house.files.len();
            house.files.push((rel, lines.len()));
            for line in lines {
                house.index.entry(line).or_default().push(at);
            }
        }
        house
    }

    /// Whether every house TUI file was read: without them content is judged against nothing.
    fn has_tui(&self) -> bool {
        TUI.iter()
            .all(|t| self.files.iter().any(|(rel, _)| rel == t))
    }

    /// The house file `text` is a copy of, with how many of its lines it holds.
    fn copy_of(&self, text: &str) -> Option<(&str, usize, usize)> {
        let mut held: HashMap<usize, usize> = HashMap::new();
        for line in distinctive(text) {
            for at in self.index.get(&line).into_iter().flatten() {
                *held.entry(*at).or_default() += 1;
            }
        }
        held.into_iter()
            .filter_map(|(at, n)| {
                let (rel, total) = &self.files[at];
                (n >= MIN_LINES && n * 100 >= total * MIN_SHARE_PCT).then_some((
                    rel.as_str(),
                    n,
                    *total,
                ))
            })
            .max_by_key(|(_, n, total)| (n * 1000 / total, *n))
    }
}

/// Every copy among `files`, one report line each.
fn copies(
    repo: &Path,
    files: &[String],
    names: &BTreeSet<String>,
    house: &House,
    staged: bool,
) -> String {
    let mut report = String::new();
    for path in files {
        if names.contains(path.rsplit('/').next().unwrap_or(path.as_str())) {
            let _ = writeln!(
                report,
                "{ERR} {path}: a copy of a house checker. A vendored copy is frozen at the day it \
                 was copied; run the shared one instead (bash \"$GOH_DIR/gates/goh.sh\" <check>). \
                 A file of your own that only shares the name: rename it."
            );
            continue;
        }
        let Some(bytes) = crate::gitutil::content_bytes(repo, path, staged) else {
            continue;
        };
        if bytes.len() > MAX_BYTES || bytes.contains(&0) {
            continue;
        }
        if let Some((rel, n, total)) = house.copy_of(&String::from_utf8_lossy(&bytes)) {
            let _ = writeln!(
                report,
                "{ERR} {path}: a copy of the house's {rel} ({n} of its {total} distinctive lines). \
                 A vendored copy is frozen at the day it was copied; delete it and use the shared \
                 one through $GOH_DIR (scripts source \"${{GOH_DIR:-$HOME/Projects/gates_of_heck}}/tui/lib.sh\"; \
                 Python puts GOH_DIR on sys.path before `from tui.lib import ...`)."
            );
        }
    }
    report
}

/// The structural step. `files` is the run's listing (staged: the index's ACM entries).
#[must_use]
pub fn step(repo: &Path, files: &[String], staged: bool) -> Option<i32> {
    let start = begin(LABEL);
    let goh = crate::goh_root();
    let same = |a: &Path, b: &Path| {
        a.canonicalize()
            .is_ok_and(|a| b.canonicalize().ok() == Some(a))
    };
    if same(repo, &goh) || repo.join("crates/goh/src/structural.rs").is_file() {
        // This IS the shared checkout (a checkout, worktree, export or copy of it).
        ok(LABEL, start);
        return None;
    }
    let house = House::load(&goh);
    if !house.has_tui() {
        // Fail closed: a GOH_DIR without the house TUI judges content against nothing.
        let msg = format!(
            "{ERR} {}: the house TUI (tui/lib.sh, tui/lib.py, tui/stylerc) is not readable there -- \
             a copy cannot be recognised by content against nothing; point GOH_DIR at the shared \
             checkout\n",
            goh.display()
        );
        return Some(fail(LABEL, "native", &msg, start).max(2));
    }
    let report = copies(repo, files, &house_names(&goh), &house, staged);
    if !report.is_empty() {
        return Some(fail(LABEL, "native", &report, start));
    }
    ok(LABEL, start);
    None
}

#[cfg(test)]
mod tests {
    use super::{House, MIN_LINES};
    use std::path::Path;

    fn goh() -> &'static Path {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(Path::parent)
            .unwrap_or_else(|| unreachable!("crates/goh sits two below the checkout"))
    }

    #[test]
    fn a_goh_dir_without_the_house_tui_reads_as_unusable() {
        let empty = std::env::temp_dir().join(format!("goh-vendored-empty-{}", std::process::id()));
        let _ = std::fs::create_dir_all(empty.join("checks"));
        let house = House::load(&empty);
        let _ = std::fs::remove_dir_all(&empty);
        assert!(
            !house.has_tui(),
            "an empty GOH_DIR must be a refusal, not a scan against nothing"
        );
        assert!(House::load(goh()).has_tui());
    }

    #[test]
    fn a_copy_is_most_of_a_house_files_lines_and_a_few_shared_lines_are_not() {
        let house = House::load(goh());
        let lib = std::fs::read_to_string(goh().join("tui/lib.sh")).unwrap_or_default();
        assert_eq!(house.copy_of(&lib).map(|c| c.0), Some("tui/lib.sh"));
        let few: Vec<&str> = lib
            .lines()
            .filter(|l| l.trim().len() >= 12)
            .take(MIN_LINES)
            .collect();
        assert_eq!(
            house.copy_of(&few.join("\n")),
            None,
            "{MIN_LINES} lines of a 90-line file are not a copy"
        );
        assert_eq!(house.copy_of(""), None);
    }
}
