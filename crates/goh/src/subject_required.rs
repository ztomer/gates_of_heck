//! A scanner that read something must have a declared floor (GOH.FLOORREQ, GOH.SCANNERFLOORS).
//!
//! The advisory this replaces ("N scanners declare no floor") was printed in six consumers on
//! 2026-11-12 and acted on in none: a scanner with no floor that silently reads zero files passes,
//! and an advisory is a rule somebody has to remember. An UNDECLARED floor is now, from
//! `crate::grace::OVER_FROM` (2026-11-23: a week of grace, the advisory line until then), a finding of the
//! pipeline's last step, held to the estate-seed ratchet (`crate::estate_seed`):
//!
//! * a repo that declares the key (`KEY=N`, or `KEY=0` for "no floor here") has nothing to say;
//! * an undeclared key a repo's seed names (`floor_seed.json`, its own tracked file or the private
//!   store, keyed by root commit, the key as the path) WARNS, listing `KEY=N` ready to paste;
//! * an undeclared key with no entry is red, so a NEW scanner cannot add a silent consumer, and
//!   the release canary refuses a goh whose new scanner would turn a consumer red before the
//!   consumer is seeded -- the strand `bare-open` caused on 2026-11-14;
//! * an entry whose key is now declared is RETIRED when it lives in the private store
//!   (GOH.SEEDPRUNE): the run that sees the declaration deletes it under the store's lock and says
//!   so, green. A declared key never consults the seed again, so the entry holds nothing, and the
//!   store is in no tree: a stale red there was cleared only by hand-editing a private file (12
//!   entries of two private consumers, 2026-11-16). Any declaration retires it, `KEY=0` too --
//!   the floor itself (`crate::subject::judge`) judges the declared value. A TRACKED entry whose
//!   key is declared is stale and red: the repository deletes it in the commit that declares it;
//! * an entry whose scanner read nothing is stale and red.
//!
//! Full scope only, as the floors themselves are: at `--staged` a scanner reads only the commit.

use crate::estate_seed::{self, Kind, Report, Seed, Settle, Sites};
use crate::subject::Subject;
use std::collections::BTreeMap;
use std::path::Path;

const TAG: &str = "[subject]";

/// The seed of the estate's undeclared floors on the day the requirement landed.
pub const KIND: Kind = Kind {
    file: "floor_seed.json",
    unit: "undeclared floor",
    name: "floor seed",
};

const FIX: &str = "    Declare each in .gatesrc at or a little under what it read (this repo uses nine tenths):\n    \
     set too high it reds a clean repo and is lowered with the tree; set too low it never fires.\n    \
     `KEY=0` declares \"no floor here\" (a repo with no shell, say), by decision.\n";

/// The advisory line, until the requirement is in force or under the `within` seam of `crate::grace`: every undeclared floor, `KEY=N`, one line (empty when none).
fn advisory(records: &[(&Subject, usize)], floors: &BTreeMap<&'static str, usize>) -> String {
    let keys: Vec<String> = records
        .iter()
        .filter(|(s, n)| *n > 0 && !floors.contains_key(s.key))
        .map(|(s, n)| format!("{}={n}", s.key))
        .collect();
    if keys.is_empty() {
        return String::new();
    }
    let who = if keys.len() == 1 {
        "1 scanner declares".to_string()
    } else {
        format!("{} scanners declare", keys.len())
    };
    format!(
        "{} {TAG} {who} no floor in .gatesrc, so a scan that stops reading would pass; this run read: {}\n",
        goh_tui::START,
        keys.join(" ")
    )
}

/// The undeclared floors: `key -> one listing line`, in registry order within the map's key order.
#[must_use]
pub fn undeclared(records: &[(&Subject, usize)], floors: &BTreeMap<&'static str, usize>) -> Sites {
    let mut out = Sites::new();
    for (subject, read) in records {
        if *read > 0 && !floors.contains_key(subject.key) {
            let n = estate_seed::clip(&format!(
                "{}={read}  -- read {} {}, no floor declared",
                subject.key,
                read,
                goh_tui::noun_as(*read, subject.unit, subject.units)
            ));
            out.insert(subject.key.to_string(), vec![format!("    {n}")]);
        }
    }
    out
}

/// The verdict over the undeclared floors of the repo at `root`: `(exit code, stdout, stderr)`.
#[must_use]
pub fn verdict(
    root: &Path,
    records: &[(&Subject, usize)],
    floors: &BTreeMap<&'static str, usize>,
) -> (i32, String, String) {
    if !crate::grace::over() {
        return (0, String::new(), advisory(records, floors));
    }
    let mut seed = match estate_seed::load(root, KIND, false) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{} {TAG} {m}\n", goh_tui::ERR)),
    };
    let repo = if seed.is_empty() {
        String::new()
    } else {
        crate::gitutil::root_commit(root).unwrap_or_default()
    };
    let retired = retire(&mut seed, &repo, floors);
    let said = retire_store(&repo, &retired);
    let found = undeclared(records, floors);
    let report = Report {
        tag: TAG,
        item: "GOH.FLOORREQ",
        what: (
            "scanner with no floor, so a scan that stops reading would pass",
            "scanners with no floor, so a scan that stops reading would pass",
        ),
        fix: FIX,
        ok: "every scanner that read something declares its floor".to_string(),
    };
    let (code, out, err) = estate_seed::verdict(root, false, KIND, &seed, &found, &report);
    (code, out, format!("{said}{err}"))
}

/// The private store's entries for `repo` whose key `floors` declares, taken out of `seed`.
///
/// Returned as a settle to 0 (`Settle::store`): a declared key is judged by its floor, never by
/// the seed, so its store entry holds nothing. A tracked entry stays, and is stale at the verdict.
fn retire(seed: &mut Seed, repo: &str, floors: &BTreeMap<&'static str, usize>) -> Settle {
    let mut out = Settle::default();
    if repo.is_empty() {
        return out;
    }
    seed.retain(|(r, key), held| {
        let gone = r == repo && held.stored && floors.contains_key(key.as_str());
        if gone {
            out.store.insert(key.clone(), 0);
        }
        !gone
    });
    out
}

/// Delete `retired`'s entries from the store under its lock, and say what was retired: through
/// the one retirement path (`estate_seed::retire_store`).
fn retire_store(repo: &str, retired: &Settle) -> String {
    estate_seed::retire_store(repo, KIND, &retired.store, TAG, |key, file| {
        format!(
            "{} {TAG} {key}: declared in .gatesrc, its GOH.FLOORREQ entry retired from {} (GOH.SEEDPRUNE)",
            goh_tui::START,
            file.display()
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::subject::{EMOJI, SECRETS, SHELL_LINT};

    #[test]
    fn only_a_scanner_that_read_something_with_no_floor_is_undeclared() {
        let floors = BTreeMap::from([("GOH_SECRETS_MIN_FILES", 5)]);
        let found = undeclared(&[(&EMOJI, 12), (&SECRETS, 9), (&SHELL_LINT, 0)], &floors);
        assert_eq!(
            found.keys().map(String::as_str).collect::<Vec<_>>(),
            ["GOH_EMOJI_MIN_FILES"],
            "a declared floor and a scanner with nothing to read are not omissions"
        );
        assert!(found["GOH_EMOJI_MIN_FILES"][0].contains("GOH_EMOJI_MIN_FILES=12"));
    }

    #[test]
    fn a_declared_zero_is_a_declaration() {
        let floors = BTreeMap::from([("GOH_EMOJI_MIN_FILES", 0)]);
        assert_eq!(undeclared(&[(&EMOJI, 12)], &floors).len(), 0);
    }
}
