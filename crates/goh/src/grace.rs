//! A week of grace for a check that turns a consumer red the day it ships (GOH.BAREOPEN2,
//! GOH.FLOORREQ, GOH.LINEHEDGE).
//!
//! A new scanner, or a new SHAPE a scanner learns, finds sites in consumers that no seed names, and
//! the seed that would name them lives in the owner's private store, which the RELEASED goh reads
//! as stale the moment the new shape is not there to find them (divoom v0.45.12 failed at its
//! release on exactly this, 2026-11-14) and which a CI runner cannot read at all. So the new
//! shapes judge nothing until `OVER_FROM`: before it a scanner reads as it did at the last
//! release, and an undeclared floor is the advisory line; from it the full verdict is in force. The
//! week is the consumers' to seed, declare or fix in their own repos.
//!
//! THE FORWARD-CORRECT TREE IS GREEN ON BOTH SIDES (GOH.GRACEFWD, 2026-11-15). The week only
//! helps a consumer that can make its tree right for the day BEFORE the day: a scanner READS its
//! new shapes within the grace too, so its subject floor counts them in the mode the gate runs
//! in (a floor declared for the day is not `read NONE` today), and an escape marker over a site
//! that is a finding only from the day is accepted before it, never reported stale.
//! `movingtag::scan::scan_at`, `bareopen` and `gitdeadline` hold to it.
//!
//! THE GRACE SHOWS WHAT ITS END TURNS RED (GOH.GRACESHAPES). A shape held back judges nothing, but it
//! is never silent: `ahead` reads the scan the way the day will and names every site only the day
//! finds, as a warning with the exit unchanged, and holds a seeded one at the count the day will
//! make, so a consumer can fix or seed a site before it turns red. One helper for every scanner
//! that switches on `over()`; the scanners that print their own (`shape-diff`, the floors'
//! advisory) already say it.
//!
//! `GOH_GRACE` is the ambient override, for the suites that judge the new behaviour (`over`) and for
//! goh's own keyless fixtures (`within`); unset, the day decides. No `.gatesrc` can say it.

pub mod ahead;

/// The day `OVER_FROM` falls on, as the warnings within the grace name it.
pub const OVER_DAY: &str = "2026-11-23";
/// 2026-11-23T00:00:00Z, seconds since the epoch.
pub const OVER_FROM: u64 = 1_795_392_000;
/// The ambient override: `over` or `within`.
pub const SEAM: &str = "GOH_GRACE";

/// Whether the grace is over at `now` under the seam's value.
#[must_use]
pub fn over_at(seam: Option<&str>, now: u64) -> bool {
    over_at_from(seam, now, OVER_FROM)
}

/// Whether the grace is over now.
#[must_use]
pub fn over() -> bool {
    over_from(OVER_FROM)
}

/// Whether a grace of its own, ending at `from` (seconds since the epoch), is over now.
///
/// The same seam decides. For a scanner whose consumers need longer than the shared week: `goh dead-port`
/// landed while goh's ships were frozen (GOH.DEADPORTSCAN), so its week counts from a later day.
#[must_use]
pub fn over_from(from: u64) -> bool {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs());
    over_at_from(std::env::var(SEAM).ok().as_deref(), now, from)
}

/// `over_at` for a grace ending at `from`.
#[must_use]
pub fn over_at_from(seam: Option<&str>, now: u64, from: u64) -> bool {
    match seam {
        Some("within") => false,
        Some("over") => true,
        _ => now >= from,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `OVER_DAY` is the date `OVER_FROM` is (Hinnant's civil-from-days).
    #[test]
    fn the_named_day_is_the_day_that_decides() {
        let z = i64::try_from(OVER_FROM / 86_400).unwrap_or(0) + 719_468;
        let era = z.div_euclid(146_097);
        let doe = z.rem_euclid(146_097);
        let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let d = doy - (153 * mp + 2) / 5 + 1;
        let m = if mp < 10 { mp + 3 } else { mp - 9 };
        let y = yoe + era * 400 + i64::from(m <= 2);
        assert_eq!(OVER_DAY, format!("{y:04}-{m:02}-{d:02}"));
        assert_eq!(OVER_FROM % 86_400, 0, "midnight UTC");
    }

    #[test]
    fn the_day_decides_and_the_seam_overrides_it() {
        assert!(!over_at(None, OVER_FROM - 1), "a week of grace");
        assert!(over_at(None, OVER_FROM));
        assert!(
            over_at(Some("over"), 0),
            "a suite judging the new behaviour"
        );
        assert!(
            !over_at(Some("within"), OVER_FROM + 1),
            "goh's keyless fixtures"
        );
        assert!(
            !over_at(Some("sometimes"), 0),
            "an unknown value is the day"
        );
    }
}
