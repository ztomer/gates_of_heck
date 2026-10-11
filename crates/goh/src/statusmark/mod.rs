//! `goh status-markers`: a hand-rolled status marker or colour escape is a finding, as an emoji is
//! (BACKLOG 4.9).
//!
//! The house prints status through `tui/lib.sh` / `tui.lib`. Nothing policed the other direction:
//! an agent's own instructions told it to write `print_info`/`print_err` helpers printing
//! `[ ==> ]`, `[ Wrn ]`, `[ Err ]`, `[ Ok  ]`, and 27 tracked files in five repos carried them. The
//! finder is `scan.rs`; the debt is a per-repo shrink-only seed, `status_marker_seed.json`
//! (`crate::seedcount`). A repo opts in by calling the command from its own gate.

pub mod scan;

use std::collections::BTreeMap;
use std::path::Path;

use crate::seedcount::{self, Spec};

/// The seed's file name, at the repo root.
pub const SEED_FILE: &str = "status_marker_seed.json";

const SPEC: Spec = Spec {
    tag: "[status_markers]",
    file: SEED_FILE,
    noun: "source file(s)",
    reseed: "goh status-markers --seed",
    clean: "no new hand-rolled status marker or colour escape",
    why: "hand-rolled status markers and colour escapes when this repo adopted goh \
          status-markers; a count may only fall -- print through tui/lib.sh or tui.lib \
          (gates_of_heck BACKLOG 4.9)",
};

/// The source languages a status line is printed from. A doc, a fixture's data or a config is
/// not source: one that quotes the forbidden style is describing it, not using it.
const SOURCE: [&str; 11] = [
    "py", "sh", "bash", "zsh", "rs", "swift", "js", "mjs", "ts", "rb", "go",
];

/// The findings per file the scan read.
pub type Scanned = seedcount::Scanned<scan::Finding>;

fn site(f: &scan::Finding) -> String {
    format!(
        "{}: {}: print through tui/lib.sh or tui.lib (info/ok/err/warn), which owns the icons, \
         the colours and NO_COLOR; a language with neither prints tui/stylerc's Kare glyphs",
        f.line, f.what
    )
}

/// The verdict over a scan and a seed: (exit code, report).
#[must_use]
pub fn judge(scanned: &Scanned, seed: &BTreeMap<String, usize>) -> (i32, String) {
    seedcount::judge(&SPEC, scanned, seed, site)
}

#[must_use]
pub fn is_source(rel: &str) -> bool {
    Path::new(rel)
        .extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| SOURCE.iter().any(|s| e.eq_ignore_ascii_case(s)))
}

/// `goh status-markers [--seed] [--exclude RE]` over the working tree's listed files.
#[must_use]
pub fn run_command(seed: bool, exclude: &str) -> i32 {
    seedcount::run(&SPEC, seed, exclude, is_source, scan::findings, site)
}

#[cfg(test)]
mod tests;
