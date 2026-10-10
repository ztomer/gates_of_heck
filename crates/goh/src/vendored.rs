//! No vendored copy of a house checker (SUPERSOTA R5).
//!
//! A copy of `check_no_allow.py` in a consumer's `tools/` is frozen at the day it was copied: four
//! repos carried one until 2026-09-14 and the rest were not checked at all, and antiknob still
//! carries a `tools/_gitutil.py`. The shared checkout is the only copy (hooks delegate to it at
//! runtime), so a file named after a house checker is refused WHEN IT IS ADDED -- at `--staged`,
//! where the commit adding it can still be changed -- and named at full scope, where an existing
//! copy is a migration for that repo rather than a reason to turn every gate red at once.
//!
//! The names are the shared checkout's own `checks/*.py` and `checks/*.sh`, plus every checker
//! Phase N3 retired (a copy of one of those is the most frozen of all). `gates_of_heck` itself --
//! any checkout, worktree or export of it -- is the one tree where those files belong.

use std::collections::BTreeSet;
use std::fmt::Write as _;
use std::path::Path;

use crate::step_report::{begin, fail, ok};

const LABEL: &str = "no vendored copies of house checkers";

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

/// The files of `files` whose basename is a house checker's.
fn copies<'a>(files: &'a [String], names: &BTreeSet<String>) -> Vec<&'a String> {
    files
        .iter()
        .filter(|f| names.contains(f.rsplit('/').next().unwrap_or(f.as_str())))
        .collect()
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
    let names = house_names(&goh);
    if staged {
        let added = match crate::gitutil::added_files(repo) {
            Ok(added) => added,
            Err(e) => return Some(fail(LABEL, "native", &format!("✗ {e}\n"), start)),
        };
        let new = copies(&added, &names);
        if !new.is_empty() {
            let mut report = String::new();
            for path in &new {
                let _ = writeln!(
                    report,
                    "✗ {path}: a copy of a house checker. A vendored copy is frozen at the day it \
                     was copied; run the shared one instead (bash \"$GOH_DIR/gates/goh.sh\" \
                     <check>). A file of your own that only shares the name: rename it."
                );
            }
            return Some(fail(LABEL, "native", &report, start));
        }
    } else {
        for path in copies(files, &names) {
            eprintln!(
                "⚠ {path}: a vendored copy of a house checker -- delete it and run the shared \
                 one (a NEW copy is refused at commit)"
            );
        }
    }
    ok(LABEL, start);
    None
}
