//! Semver, narrowly: what a `Cargo.toml` actually spells. Port of
//! the retired `checks/_semver.py`.
//!
//! A missing minor or patch WIDENS a comparator's range, as Cargo reads it
//! (`~1.0` is `>=1.0.0, <1.1.0`; `^0.0.3` is `>=0.0.3, <0.0.4`). An
//! unreadable requirement is `None`, never a verdict.

use std::cmp::Ordering;

/// `(major, minor, patch, (release?, prerelease))`, ordered as the reference orders it.
pub type Key = (u64, u64, u64, (u8, String));

const fn key(major: u64, minor: u64, patch: u64) -> Key {
    (major, minor, patch, (1, String::new()))
}

fn digits(s: &str) -> Option<u64> {
    (!s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
        .then(|| s.parse().ok())
        .flatten()
}

/// `parse_version`: `x.y.z[-pre]`, `x.y`, or `x`; build metadata ignored.
#[must_use]
pub fn parse_version(text: &str) -> Option<Key> {
    let t = text.trim().split('+').next().unwrap_or("");
    let (core, pre) = match t.split_once('-') {
        Some((c, p))
            if !p.is_empty()
                && p.chars()
                    .all(|ch| ch.is_ascii_alphanumeric() || ".-".contains(ch)) =>
        {
            (c, Some(p))
        }
        Some(_) => return None,
        None => (t, None),
    };
    let parts: Vec<&str> = core.split('.').collect();
    let nums: Option<Vec<u64>> = parts.iter().map(|p| digits(p)).collect();
    let nums = nums?;
    match (nums.as_slice(), pre) {
        ([a, b, c], pre) => Some((
            *a,
            *b,
            *c,
            pre.map_or((1, String::new()), |p| (0, p.to_owned())),
        )),
        ([a, b], None) => Some(key(*a, *b, 0)),
        ([a], None) => Some(key(*a, 0, 0)),
        _ => None,
    }
}

/// `max(versions, key=parse_version)` over the parsable ones.
#[must_use]
pub fn newest<'a>(versions: &[&'a str]) -> Option<&'a str> {
    versions
        .iter()
        .filter_map(|v| parse_version(v).map(|k| (k, *v)))
        .fold(None, |best: Option<(Key, &str)>, (k, v)| match &best {
            Some((bk, _)) if *bk >= k => best,
            _ => Some((k, v)),
        })
        .map(|(_, v)| v)
}

/// `(lowest admitted, lowest refused above)` for one comparator.
fn bounds(op: &str, maj: u64, mn: Option<u64>, pat: Option<u64>) -> (Option<Key>, Option<Key>) {
    let (m0, p0) = (mn.unwrap_or(0), pat.unwrap_or(0));
    match (op, mn, pat) {
        ("=" | "~", None, _) => (Some(key(maj, 0, 0)), Some(key(maj + 1, 0, 0))),
        ("=", Some(m), None) => (Some(key(maj, m, 0)), Some(key(maj, m + 1, 0))),
        ("=", Some(m), Some(p)) => (Some(key(maj, m, p)), Some(key(maj, m, p + 1))),
        (">=", ..) => (Some(key(maj, m0, p0)), None),
        (">", None, _) => (Some(key(maj + 1, 0, 0)), None),
        (">", Some(m), None) => (Some(key(maj, m + 1, 0)), None),
        (">", Some(m), Some(p)) => (Some(key(maj, m, p + 1)), None),
        ("<", ..) => (None, Some(key(maj, m0, p0))),
        ("<=", None, _) => (None, Some(key(maj + 1, 0, 0))),
        ("<=", Some(m), None) => (None, Some(key(maj, m + 1, 0))),
        ("<=", Some(m), Some(p)) => (None, Some(key(maj, m, p + 1))),
        ("~", Some(m), _) => (Some(key(maj, m, p0)), Some(key(maj, m + 1, 0))),
        _ if maj > 0 || mn.is_none() => (Some(key(maj, m0, p0)), Some(key(maj + 1, 0, 0))),
        _ if m0 > 0 || pat.is_none() => (Some(key(0, m0, p0)), Some(key(0, m0 + 1, 0))),
        _ => (Some(key(0, 0, p0)), Some(key(0, 0, p0 + 1))),
    }
}

/// `req_allows`: does `req` admit `version`? `None` when either is unreadable.
#[must_use]
pub fn req_allows(req: &str, version: &str) -> Option<bool> {
    let v = parse_version(version)?;
    let r = req.trim();
    if r.is_empty() || r == "*" {
        return Some(true);
    }
    for part in r.split(',').map(str::trim).filter(|p| !p.is_empty()) {
        let (op, rest) = [">=", "<=", ">", "<", "=", "~", "^"]
            .iter()
            .find_map(|op| part.strip_prefix(op).map(|rest| (*op, rest.trim_start())))
            .unwrap_or(("^", part));
        let fields: Vec<&str> = rest.split('.').collect();
        if fields.is_empty() || fields.len() > 3 {
            return None;
        }
        let maj = digits(fields[0])?;
        let mn = match fields.get(1) {
            Some(f) => Some(digits(f)?),
            None => None,
        };
        let pat = match fields.get(2) {
            Some(f) => Some(digits(f)?),
            None => None,
        };
        let (lo, hi) = bounds(op, maj, mn, pat);
        if lo.is_some_and(|lo| v.cmp(&lo) == Ordering::Less)
            || hi.is_some_and(|hi| v.cmp(&hi) != Ordering::Less)
        {
            return Some(false);
        }
    }
    Some(true)
}

#[cfg(test)]
mod tests {
    use super::req_allows;

    /// Cargo's requirement table, the spec this comparator answers to: the
    /// reference's rows, and the partial-version rows it read wrong until
    /// 2026-10-05 (`^0.0.3`, `^0.0`, `~1.0`, `=1.2`).
    const TABLE: &[(&str, &str, bool)] = &[
        ("1", "1.9.9", true),
        ("1", "2.0.0", false),
        ("0.19", "0.19.2", true),
        ("0.19", "0.20.0", false),
        ("0", "0.0.5", true),
        ("^0.14", "0.19.2", false),
        ("^1.0.5", "2.0.0", false),
        ("1.0.5", "1.0.6", true),
        ("1.0.5", "1.0.4", false),
        (">=1.2, <2", "1.7.0", true),
        (">=1.2, <2", "2.0.0", false),
        ("~1.2.3", "1.2.9", true),
        ("~1.2.3", "1.3.0", false),
        ("*", "9.9.9", true),
        ("^0.0.3", "0.0.4", false),
        ("0.0.3", "0.0.3", true),
        ("^0.0", "0.1.0", false),
        ("^0.0", "0.0.9", true),
        ("~1.0", "1.1.0", false),
        ("~1", "1.9.0", true),
        ("=1.2", "1.2.7", true),
        ("=1.2", "1.3.0", false),
        ("=1", "1.4.0", true),
        ("=1.2.3", "1.2.4", false),
        (">1", "1.9.9", false),
        (">1.2", "1.3.0", true),
        ("<=1.2", "1.2.9", true),
        ("<=1.2", "1.3.0", false),
    ];

    #[test]
    fn every_row_of_the_requirement_table_holds() {
        for (req, version, want) in TABLE {
            assert_eq!(
                req_allows(req, version),
                Some(*want),
                "req {req:?} admits {version}"
            );
        }
    }

    #[test]
    fn build_metadata_is_ignored_and_a_prerelease_sorts_below_its_release() {
        assert_eq!(req_allows("1.1.6", "1.1.6+spec-1.1.0"), Some(true));
        assert!(super::parse_version("1.0.0-rc.1") < super::parse_version("1.0.0"));
    }

    #[test]
    fn an_unreadable_requirement_abstains() {
        for req in [
            "1.*",
            ">=x",
            "1.2.3.4",
            "~>1",
            "not a version",
            "^1.2.3.4.5",
        ] {
            assert_eq!(
                req_allows(req, "1.0.0"),
                None,
                "{req:?} was read as a verdict"
            );
        }
    }
}
