//! What the grace's end turns red, SHOWN within it (GOH.GRACESHAPES).
//!
//! A scanner that holds a shape back until `crate::grace::OVER_FROM` judges nothing from it, and
//! before this it SAID nothing either: a consumer saw no site from the new shape, so it could
//! neither fix one nor seed it before the shape turned on, and met every one on the day. Within
//! the grace each such scanner now also reads the shape the way the day will, and names what is
//! only there: a warning, the verdict and the exit unchanged.
//!
//! A SEEDED SITE IS HELD, NOT STALE. A consumer that wrote a seed entry for a new-shape site (the
//! week to do it in) read as STALE to the scan that cannot see the site yet, red today. The found
//! counts `shown` returns count a seeded path at `max(today, min(day, seeded))`, so the entry
//! stands while the site does and is lowered when the site is fixed -- the count the day will
//! make. What the seed does not cover is the warning.

use crate::estate_seed::{Seed, Sites};
use std::collections::BTreeMap;
use std::path::Path;

/// The sites listed in a warning; the rest are counted.
const LISTED: usize = 8;

/// What the day's scan makes of a run today's cannot see.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Shown {
    /// Today's sites, and the day's seeded ones: what the verdict judges within the grace.
    pub held: Sites,
    /// The warning for the sites the day will find red; empty when there are none.
    pub warn: String,
}

/// The seeded counts of the repository at `root`, `path -> count`.
///
/// One git spawn at most: none when nothing is seeded.
#[must_use]
pub fn seeded(root: &Path, seed: &Seed) -> BTreeMap<String, usize> {
    if seed.is_empty() {
        return BTreeMap::new();
    }
    let me = crate::gitutil::root_commit(root).unwrap_or_default();
    seed.iter()
        .filter(|((r, _), _)| *r == me)
        .map(|((_, path), held)| (path.clone(), held.count))
        .collect()
}

/// `today`'s sites against `day`'s (the same scan with every new shape on), and what each seeded
/// path holds.
#[must_use]
pub fn shown(tag: &str, today: Sites, day: &Sites, seeded: &BTreeMap<String, usize>) -> Shown {
    let mut held = today;
    let mut red: Vec<&String> = Vec::new();
    let mut files = 0usize;
    for (path, lines) in day {
        let have: Vec<String> = held.get(path).cloned().unwrap_or_default();
        let new: Vec<&String> = lines.iter().filter(|l| !have.contains(l)).collect();
        if new.is_empty() {
            continue;
        }
        let room = seeded
            .get(path)
            .copied()
            .unwrap_or(0)
            .saturating_sub(have.len());
        let (kept, rest) = new.split_at(room.min(new.len()));
        if !kept.is_empty() {
            let mut all = have;
            all.extend(kept.iter().map(|l| (*l).clone()));
            held.insert(path.clone(), all);
        }
        if !rest.is_empty() {
            files += 1;
            red.extend(rest.iter().copied());
        }
    }
    Shown {
        held,
        warn: warning(tag, &red, files),
    }
}

/// The warning for `red` sites in `files` files, empty for none.
fn warning(tag: &str, red: &[&String], files: usize) -> String {
    use std::fmt::Write as _;
    if red.is_empty() {
        return String::new();
    }
    let mut out = format!(
        "{} {tag} {} in {} turn red from {}, when the new shapes are judged (a warning until \
         then): fix them, or seed them with `goh seed-add`\n",
        goh_tui::WARN,
        goh_tui::counted(red.len(), "site"),
        goh_tui::counted(files, "file"),
        crate::grace::OVER_DAY
    );
    for line in red.iter().take(LISTED) {
        let _ = writeln!(out, "{line}");
    }
    if red.len() > LISTED {
        let _ = writeln!(out, "    ... and {} more", red.len() - LISTED);
    }
    out
}

/// A verdict with `warn` ahead of its stderr; a refusal (exit 2) and an empty warning are as they were.
#[must_use]
pub fn with(verdict: (i32, String, String), warn: &str) -> (i32, String, String) {
    let (code, out, err) = verdict;
    if code == 2 || warn.is_empty() {
        return (code, out, err);
    }
    (code, out, format!("{warn}{err}"))
}

/// The verdict of a scanner that judges NOTHING until the grace's end.
///
/// Exit 0, and `day`'s sites (its full scan) as the warning, less what the consumer already seeded.
/// A scan that failed is the refusal it would be after the day. The seed is read, never judged: an
/// entry written within the grace is neither stale nor settled (GOH.GRACEEND).
#[must_use]
pub fn warn_only(
    (root, staged): (&Path, bool),
    (kind, tag): (crate::estate_seed::Kind, &str),
    day: Result<Sites, String>,
) -> (i32, String, String) {
    let day = match day {
        Ok(d) => d,
        Err(m) => return (2, String::new(), format!("{} {tag} {m}\n", goh_tui::ERR)),
    };
    let seed = crate::estate_seed::load(root, kind, staged).unwrap_or_default();
    let seeded = seeded(root, &seed);
    (
        0,
        String::new(),
        shown(tag, Sites::new(), &day, &seeded).warn,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sites(rows: &[(&str, &[&str])]) -> Sites {
        rows.iter()
            .map(|(p, ls)| {
                (
                    (*p).to_string(),
                    ls.iter().map(|l| (*l).to_string()).collect(),
                )
            })
            .collect()
    }

    #[test]
    fn only_what_the_day_adds_is_warned() {
        let today = sites(&[("a.sh", &["    a.sh:1: old"])]);
        let day = sites(&[("a.sh", &["    a.sh:1: old", "    a.sh:2: new"])]);
        let s = shown("[t]", today.clone(), &day, &BTreeMap::new());
        assert_eq!(s.held, today, "the verdict is today's");
        assert!(
            s.warn.contains("a.sh:2: new") && !s.warn.contains("a.sh:1"),
            "{}",
            s.warn
        );
        assert!(s.warn.contains("2026-11-23"), "{}", s.warn);
        let same = shown("[t]", today.clone(), &today, &BTreeMap::new());
        assert!(same.warn.is_empty(), "expected no warning: {}", same.warn);
    }

    #[test]
    fn a_seeded_site_is_held_at_the_count_the_day_will_make() {
        let day = sites(&[("a.sh", &["    a.sh:1: x", "    a.sh:2: y", "    a.sh:3: z"])]);
        let seeded = BTreeMap::from([("a.sh".to_string(), 2)]);
        let s = shown("[t]", Sites::new(), &day, &seeded);
        assert_eq!(s.held["a.sh"].len(), 2, "held at the seed, not stale");
        assert!(
            s.warn.contains("a.sh:3: z") && !s.warn.contains("a.sh:2"),
            "{}",
            s.warn
        );
        let fixed = sites(&[("a.sh", &["    a.sh:1: x"])]);
        let s = shown("[t]", Sites::new(), &fixed, &seeded);
        assert_eq!(s.held["a.sh"].len(), 1, "a fixed site lowers the count");
        assert!(s.warn.is_empty(), "expected no warning: {}", s.warn);
    }

    #[test]
    fn a_refusal_and_an_empty_warning_pass_through() {
        let v = (2, String::new(), "no\n".to_string());
        assert_eq!(with(v.clone(), "w\n"), v);
        let ok = (0, String::new(), "e\n".to_string());
        assert_eq!(with(ok.clone(), ""), ok);
        assert_eq!(with(ok, "w\n").2, "w\ne\n");
    }
}
