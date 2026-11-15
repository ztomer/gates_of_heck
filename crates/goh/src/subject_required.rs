//! A scanner that read something must have a declared floor (GOH.FLOORREQ, GOH.SCANNERFLOORS).
//!
//! The advisory this replaces ("N scanners declare no floor") was printed in six consumers on
//! 2026-11-12 and acted on in none: a scanner with no floor that silently reads zero files passes,
//! and an advisory is a rule somebody has to remember. An UNDECLARED floor is now, from
//! `REQUIRED_FROM` (2026-11-23: a week of grace, the advisory line until then), a finding of the
//! pipeline's last step, held to the estate-seed ratchet (`crate::estate_seed`):
//!
//! * a repo that declares the key (`KEY=N`, or `KEY=0` for "no floor here") has nothing to say;
//! * an undeclared key a repo's seed names (`floor_seed.json`, its own tracked file or the private
//!   store, keyed by root commit, the key as the path) WARNS, listing `KEY=N` ready to paste;
//! * an undeclared key with no entry is red, so a NEW scanner cannot add a silent consumer, and
//!   the release canary refuses a goh whose new scanner would turn a consumer red before the
//!   consumer is seeded -- the strand `bare-open` caused on 2026-11-14;
//! * an entry whose key is now declared, or whose scanner read nothing, is stale and red: the
//!   repository that declares the floor deletes its entry in the same commit.
//!
//! Full scope only, as the floors themselves are: at `--staged` a scanner reads only the commit.

use crate::estate_seed::{self, Kind, Report, Sites};
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

/// The advisory line, until the requirement is in force or under the `allow` seam: every undeclared floor, `KEY=N`, one line (empty when none).
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

/// The day an undeclared floor becomes a refusal: 2026-11-23T00:00:00Z (seconds since the epoch).
///
/// A week of grace, because a consumer whose CI runs goh cannot read the
/// private store the seed lives in: it declares its floors, or carries a tracked `floor_seed.json`,
/// before then, or its next push goes red. The same shape as `STORE_CI_REFUSED_FROM`; a new
/// scanner added later is one more key that consumers declare within the grace of its release,
/// which the release canary names per consumer before it ships.
pub const REQUIRED_FROM: u64 = 1_795_392_000;

/// The ambient override of the day.
///
/// `required` is red whatever the day (the suites that judge the requirement
/// set it), `allow` is the advisory whatever the day (goh's own fixtures, which are keyless repos).
/// Unset: by the day. No `.gatesrc` can say it: a repo's declaration is its file, never the
/// environment it was started in.
pub const SEAM: &str = "GOH_FLOOR_UNDECLARED";

/// Whether the requirement is in force at `now` under the seam's value `seam`.
#[must_use]
pub fn in_force(seam: Option<&str>, now: u64) -> bool {
    match seam {
        Some("allow") => false,
        Some("required") => true,
        _ => now >= REQUIRED_FROM,
    }
}

/// The verdict over the undeclared floors of the repo at `root`: `(exit code, stdout, stderr)`.
#[must_use]
pub fn verdict(
    root: &Path,
    records: &[(&Subject, usize)],
    floors: &BTreeMap<&'static str, usize>,
) -> (i32, String, String) {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs());
    if !in_force(std::env::var(SEAM).ok().as_deref(), now) {
        return (0, String::new(), advisory(records, floors));
    }
    let seed = match estate_seed::load(root, KIND, false) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{} {TAG} {m}\n", goh_tui::ERR)),
    };
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
    estate_seed::verdict(root, false, KIND, &seed, &found, &report)
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
    fn the_requirement_is_in_force_from_its_day_and_the_seam_overrides_the_day() {
        assert!(!in_force(None, REQUIRED_FROM - 1), "a week of grace");
        assert!(in_force(None, REQUIRED_FROM));
        assert!(
            in_force(Some("required"), 0),
            "a suite judging the requirement"
        );
        assert!(
            !in_force(Some("allow"), REQUIRED_FROM + 1),
            "goh's keyless fixtures"
        );
    }

    #[test]
    fn a_declared_zero_is_a_declaration() {
        let floors = BTreeMap::from([("GOH_EMOJI_MIN_FILES", 0)]);
        assert_eq!(undeclared(&[(&EMOJI, 12)], &floors).len(), 0);
    }
}
