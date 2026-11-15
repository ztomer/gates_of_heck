//! The scan as of the last release, which the grace period keeps (`crate::grace`, GOH.BAREOPEN2).

/// The last release's rule: the word before starts a command.
pub(super) fn leads(before: &str) -> bool {
    before.is_empty()
        || before.ends_with([';', '&', '|', '(', '{', '`', '!'])
        || super::LEADERS.iter().any(|w| {
            before.strip_suffix(w).is_some_and(|rest| {
                rest.chars()
                    .next_back()
                    .is_none_or(|c| c.is_whitespace() || ";&|(".contains(c))
            })
        })
}

/// The last release's quote test: a plain toggle, `\\` escapes outside single quotes.
pub(super) fn quoted(head: &str) -> bool {
    let (mut single, mut double, mut escaped) = (false, false, false);
    for c in head.chars() {
        if escaped {
            escaped = false;
        } else if c == '\\' && !single {
            escaped = true;
        } else if c == '\'' && !double {
            single = !single;
        } else if c == '"' && !single {
            double = !double;
        }
    }
    single || double
}
