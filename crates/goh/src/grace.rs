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
//! `GOH_GRACE` is the ambient override, for the suites that judge the new behaviour (`over`) and for
//! goh's own keyless fixtures (`within`); unset, the day decides. No `.gatesrc` can say it.

/// 2026-11-23T00:00:00Z, seconds since the epoch.
pub const OVER_FROM: u64 = 1_795_392_000;
/// The ambient override: `over` or `within`.
pub const SEAM: &str = "GOH_GRACE";

/// Whether the grace is over at `now` under the seam's value.
#[must_use]
pub fn over_at(seam: Option<&str>, now: u64) -> bool {
    match seam {
        Some("within") => false,
        Some("over") => true,
        _ => now >= OVER_FROM,
    }
}

/// Whether the grace is over now.
#[must_use]
pub fn over() -> bool {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs());
    over_at(std::env::var(SEAM).ok().as_deref(), now)
}

#[cfg(test)]
mod tests {
    use super::*;

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
