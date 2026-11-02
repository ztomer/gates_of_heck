//! Reading and comparing shellcheck's dotted version (`goh shell-lint`'s floor check).

/// `1.2.3` as numbers, for comparison; `None` when it is not a dotted version.
fn version_parts(v: &str) -> Option<Vec<u64>> {
    v.split('.').map(|p| p.parse::<u64>().ok()).collect()
}

#[must_use]
/// `have >= floor` as dotted versions (`0.11` is `0.11.0`); an unreadable one is not.
pub fn newer_or_equal(have: &str, floor: &str) -> bool {
    let (Some(mut h), Some(mut f)) = (version_parts(have), version_parts(floor)) else {
        return false;
    };
    let n = h.len().max(f.len());
    h.resize(n, 0);
    f.resize(n, 0);
    h >= f
}

#[must_use]
/// The `version: X.Y.Z` line of `shellcheck --version`.
pub fn reported_version(text: &str) -> Option<&str> {
    text.lines()
        .find_map(|l| l.strip_prefix("version:"))
        .map(str::trim)
}
