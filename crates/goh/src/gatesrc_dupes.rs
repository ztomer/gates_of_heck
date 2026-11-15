//! Every `.gatesrc` key is set once (`GOH.GATESRCDUPES`).
//!
//! `.gatesrc` is read twice, by bash (`source`) and by `crate::gatesrc::parse_pairs`, and both take
//! the LAST assignment of a key. `scripts` set its scanner floors twice (SC.PRUNECONFIG,
//! 2026-11-12): the lower block silently won, 11 keys, and nothing said so; it grew a repo-local
//! check (`tools/repo_gates.py`, `test_this_repos_gatesrc_assigns_each_key_once`). This is that
//! check in goh, for every consumer: a key assigned on two lines is a finding naming both lines.
//!
//! WIRING: a structural step in EVERY repo with a `.gatesrc`. The estate's offenders on the day it
//! landed are seeded in `gatesrc_dupes_seed.json` (`crate::estate_seed`, the path is `.gatesrc`, the
//! count is how many keys are set twice): a seeded file warns, anything else is red, and a seed for
//! a file that no longer repeats a key is stale and red.

use crate::estate_seed::{self, Kind, Report, Sites};
use std::collections::BTreeMap;
use std::path::Path;

const TAG: &str = "[gatesrc_dupes]";
/// The step's label, the same line as in `structural_steps.txt`.
pub const LABEL: &str = "every .gatesrc key is set once";
const FILE: &str = ".gatesrc";

/// The seed of the estate's offenders on the day the check landed.
pub const KIND: Kind = Kind {
    file: "gatesrc_dupes_seed.json",
    unit: "repeated key",
    name: "gatesrc-dupes seed",
};

const FIX: &str = "    Both bash and goh take the LAST assignment of a key, so the earlier one is dead text and the\n    \
     later one silently wins. Keep ONE assignment per key (the value the later one set today, if the\n    \
     two differ: that is the one in force), and delete the other.\n";

/// `(key, 1-based lines)` of every key assigned on more than one line of a `.gatesrc`.
#[must_use]
pub fn repeated(text: &str) -> Vec<(String, Vec<usize>)> {
    let mut seen: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    for (n, raw) in text.lines().enumerate() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let line = line.strip_prefix("export ").map_or(line, str::trim_start);
        let Some((key, _)) = line.split_once('=') else {
            continue;
        };
        let key = key.trim();
        if !key.is_empty() && key.chars().all(|c| c.is_alphanumeric() || c == '_') {
            seen.entry(key.to_owned()).or_default().push(n + 1);
        }
    }
    seen.into_iter().filter(|(_, v)| v.len() > 1).collect()
}

fn sites(root: &Path, staged: bool) -> Sites {
    let mut out = Sites::new();
    let blob = if staged {
        crate::gitutil::content_bytes(root, FILE, true)
    } else {
        std::fs::read(root.join(FILE)).ok()
    };
    let Some(text) = blob.and_then(|b| String::from_utf8(b).ok()) else {
        return out;
    };
    let found: Vec<String> = repeated(&text)
        .into_iter()
        .map(|(key, lines)| {
            let at: Vec<String> = lines.iter().map(ToString::to_string).collect();
            format!(
                "    {FILE}:{}: {key} is set {} times (lines {}), the last one wins",
                lines[0],
                lines.len(),
                at.join(", ")
            )
        })
        .collect();
    if !found.is_empty() {
        out.insert(FILE.to_string(), found);
    }
    out
}

fn judge(root: &Path, staged: bool) -> (i32, String, String) {
    let seed = match estate_seed::load(root, KIND, staged) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{} {TAG} {m}\n", goh_tui::ERR)),
    };
    let report = Report {
        tag: TAG,
        item: "GOH.GATESRCDUPES",
        what: ("key set twice in .gatesrc", "keys set twice in .gatesrc"),
        fix: FIX,
        ok: "every .gatesrc key is assigned once".to_string(),
    };
    estate_seed::verdict(root, staged, KIND, &seed, &sites(root, staged), &report)
}

/// The structural step, in every repo.
#[must_use]
pub fn step(repo: &Path, files: &[String], staged: bool) -> Option<i32> {
    // A commit is judged on what it brings: no `.gatesrc` in it, nothing to read (and no git spawn).
    let verdict = if staged && !files.iter().any(|f| f == FILE) {
        (0, String::new(), String::new())
    } else {
        judge(repo, staged)
    };
    estate_seed::step(LABEL, "crates/goh/src/gatesrc_dupes.rs", verdict)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_key_set_twice_names_both_lines() {
        let text = "FOO_A=1\n# FOO_B=2\nexport FOO_A=3   # later wins\nGOH_B=4\nGOH_C='x y'\n";
        assert_eq!(repeated(text), vec![("FOO_A".to_string(), vec![1, 3])]);
    }

    #[test]
    fn comments_blank_lines_and_distinct_keys_pass() {
        assert_eq!(
            repeated("# FOO_A=1\n# FOO_A=2\n\nGOH_A=1\nFOO_AB=2\n").len(),
            0
        );
        assert_eq!(repeated("echo hi\nif x; then\nfi\n").len(), 0);
    }
}
