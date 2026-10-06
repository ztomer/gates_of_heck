//! A consumer's path pattern (`--exclude`, `GOH_EXCLUDE`), in the dialect it was written for.
//!
//! The checkers were Python, and their excludes were `re.search`. The native port compiled them
//! with `regex`, which has no look-around, and a pattern consumers had used for years was refused
//! -- ztools' `^(?!vendor/)`, the one way to scope a scan TO a directory (2026-10-06). `fancy-regex`
//! takes Python's look-around and backreferences, and delegates to `regex` whenever a pattern
//! uses neither, so the common case costs nothing. Every consumer pattern compiles HERE.

/// A compiled consumer pattern, searched (not anchored) like `re.search`.
#[derive(Debug)]
pub struct PathFilter(fancy_regex::Regex);

impl PathFilter {
    /// Compile `pattern`; the caller's error message names the setting it came from.
    ///
    /// # Errors
    /// The pattern is not a regex in the Python dialect this implements.
    pub fn new(pattern: &str) -> Result<Self, String> {
        fancy_regex::Regex::new(pattern)
            .map(Self)
            .map_err(|e| e.to_string())
    }

    /// Whether the pattern is found in `text`. A match that cannot finish (the backtracking
    /// budget) reads as NO match: the path stays in scope, the strict direction.
    #[must_use]
    pub fn is_match(&self, text: &str) -> bool {
        self.0.is_match(text).unwrap_or(false)
    }
}

#[cfg(test)]
mod tests {
    use super::PathFilter;

    #[test]
    fn look_around_and_search_semantics() {
        let to_vendor = PathFilter::new("^(?!vendor/)").expect("compiles");
        assert!(to_vendor.is_match("src/a.rs") && !to_vendor.is_match("vendor/b.rs"));
        let plain = PathFilter::new("vendor/").expect("compiles");
        assert!(
            plain.is_match("rust/src/myvendor/a.rs"),
            "a search, not an anchored match"
        );
        assert!(PathFilter::new("(").is_err());
    }
}
