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

/// The seed files whose scanners hold a shape back until `OVER_FROM` (GOH.GRACEEND).
///
/// Within the grace such a scan cannot see the sites the shape finds, so an entry seeded for one reads as
/// having nothing left: STALE, red today, and a commit's settle lowered or deleted it -- the seed
/// that has to stand on the 23rd was refused or erased on the 16th (goh's own
/// `tests/test_launch_app.py` entry, refused at its land). Until the grace ends, an entry of these
/// kinds is neither stale nor settled; from it, nothing differs.
pub const HELD_BACK_SEEDS: [&str; 7] = [
    "bare_open_seed.json",
    "floor_seed.json",
    "git_deadline_seed.json",
    "layer_parity_seed.json",
    "plural_hedge_seed.json",
    "release_quiet_seed.json",
    "tui_marks_seed.json",
];

/// Whether `seed_file`'s entries are held from the stale check and the settle at `over`.
#[must_use]
pub fn holds_seed_at(seed_file: &str, over: bool) -> bool {
    !over && HELD_BACK_SEEDS.contains(&seed_file)
}

/// `holds_seed_at` now.
#[must_use]
pub fn holds_seed(seed_file: &str) -> bool {
    holds_seed_at(seed_file, over())
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
    fn a_held_back_seed_is_held_only_within_the_grace() {
        assert!(holds_seed_at("bare_open_seed.json", false));
        assert!(
            !holds_seed_at("bare_open_seed.json", true),
            "from the 23rd, judged whole"
        );
        assert!(
            !holds_seed_at("mktemp_port_seed.json", false),
            "no shape held back"
        );
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
