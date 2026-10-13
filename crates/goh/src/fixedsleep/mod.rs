//! `goh fixed-sleep`: a fixed sleep never stands in for a readiness signal (BACKLOG 4.10).
//!
//! A `time.sleep(1)` followed by `proc.poll()` asks "is it done?" after a guess at how long it
//! takes, and on a loaded machine the guess is sometimes short: the read says "not yet" about a
//! thing that was merely slow, and the caller acts on it. `ZoneWM` shipped three of these, each
//! fixed with a real signal, and nothing stopped the fourth: a capture racing the work it
//! captured, a harness reading a later marker on an earlier signal, and a lock read as held after
//! "not exited after 1 s" (fail-open under load). The finder is `scan.rs`.
//!
//! The debt is a per-repo SEED, `fixed_sleep_seed.json` at the repo root: how many such sleeps
//! each file had when the repo adopted the check. It may only fall. A file over its count is red
//! with its sites; a file under it is red too, until the seed is lowered (`--seed` rewrites it), so
//! a fixed site cannot leave headroom for a new one; and an entry for a file the scan no longer
//! reads is stale. A repo opts in by calling the command from its own gate. The seed's rules are
//! `crate::seedcount`'s, shared with `goh status-markers`.

pub mod scan;

use std::collections::BTreeMap;
use std::path::Path;

use crate::seedcount::{self, Spec};

/// The seed's file name, at the repo root.
pub const SEED_FILE: &str = "fixed_sleep_seed.json";

const SPEC: Spec = Spec {
    tag: "[fixed_sleep]",
    file: SEED_FILE,
    noun: "Python file(s)",
    reseed: "goh fixed-sleep --seed",
    clean: "no new fixed sleep stands in for a readiness signal",
    why: "fixed sleeps standing in for a readiness signal when this repo adopted goh \
          fixed-sleep; a count may only fall (gates_of_heck BACKLOG 4.10)",
    subject: &crate::subject::FIXED_SLEEP,
};

/// The findings per file the scan read, and the files it could not parse.
pub type Scanned = seedcount::Scanned<scan::Finding>;

fn site(f: &scan::Finding) -> String {
    format!(
        "{}: a fixed sleep, then `{}`: wait on the signal itself, in a loop bounded by a \
         deadline that refuses on expiry",
        f.line, f.read
    )
}

/// The verdict over a scan and a seed: (exit code, report).
#[must_use]
pub fn judge(scanned: &Scanned, seed: &BTreeMap<String, usize>) -> (i32, String) {
    seedcount::judge(&SPEC, scanned, seed, site)
}

/// Read the seed (`seedcount::load_seed`).
///
/// # Errors
///
/// What is wrong with the file.
pub fn load_seed(root: &Path) -> Result<BTreeMap<String, usize>, String> {
    seedcount::load_seed(&SPEC, root)
}

/// The seed a scan implies.
#[must_use]
pub fn seed_text(scanned: &Scanned) -> String {
    seedcount::seed_text(&SPEC, scanned)
}

fn is_python(rel: &str) -> bool {
    Path::new(rel)
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("py"))
}

/// `goh fixed-sleep [--seed] [--exclude RE]` over the working tree's listed files.
#[must_use]
pub fn run_command(seed: bool, exclude: &str) -> i32 {
    seedcount::run(&SPEC, seed, exclude, is_python, scan::findings, site)
}

#[cfg(test)]
mod tests;
