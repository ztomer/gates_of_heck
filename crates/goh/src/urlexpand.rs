//! A credential put into a URL by expansion, in a tracked file: `goh secrets`
//! reads it beside the literal shapes.
//!
//! A literal token in a URL is a secret pattern, and `goh credential-urls`
//! reads `.git/config`; neither saw a workflow clone the tap through
//! `https://` then `${TAP_TOKEN}` then `@github.com/...` (`CadGoose`
//! `build_and_release.yml:154`, 2026-10-12). The expansion is not the secret in
//! the file, but it is the secret in the URL at run time, in the argv every
//! tool that echoes its command logs. So a URL's userinfo is a finding when
//! its password slot holds any expansion (`user:${X}@`, `user:$X@`,
//! `user:${{ secrets.X }}@`), or its user slot holds one NAMED like a
//! credential (`${TAP_TOKEN}@`, `${{ secrets.X }}@`, `${{ github.token }}@`).
//! `ssh://${USER}@host` is a username, and passes.
//!
//! Prose is not read for it: a document QUOTING the line names no credential
//! of its own, and quoting it is how the defect gets written down.

/// The finding's kind, as `goh secrets` reports it.
pub const KIND: &str = "credential expansion in a URL";

/// What a finding of this kind is fixed by: nothing to rotate, the URL is the leak.
pub const ADVICE: &str = "  → a credential expansion in a URL is fixed by moving it out of the URL: \
`git -c http.extraheader=\"AUTHORIZATION: bearer $TOKEN\"`, or a credential helper reading the variable\n";

/// A URL up to the `@` that closes its userinfo; the userinfo is group 1. An Actions expansion
/// is read whole, spaces and all.
const URL_USERINFO: &str = r#"[A-Za-z][A-Za-z0-9+.-]*://((?:\$\{\{[^}]*\}\}|[^/@\s"'`])+)@"#;

/// A shell or Actions expansion; the name it expands is one of groups 1-3.
const EXPANSION: &str =
    r"\$\{\{\s*([^}]*?)\s*\}\}|\$\{([A-Za-z_][A-Za-z0-9_]*)[^}]*\}|\$([A-Za-z_][A-Za-z0-9_]*)";

/// An expanded name that holds a credential.
const CREDENTIAL_NAME: &str =
    r"(?i)token|passw|secret|api_?key|_key$|^key$|(^|_)pat($|_)|auth|cred|^secrets\.";

/// Extensions read as prose, never as something run.
const PROSE: &[&str] = &["md", "markdown", "rst", "txt"];

/// The three patterns, compiled once.
pub struct Matcher {
    url: regex::Regex,
    expansion: regex::Regex,
    credential: regex::Regex,
}

impl Matcher {
    /// # Errors
    /// A built-in pattern that does not compile (a bug, never input).
    pub fn compile() -> Result<Self, String> {
        let rx =
            |p: &str| regex::Regex::new(p).map_err(|e| format!("url expansion pattern {p:?}: {e}"));
        Ok(Self {
            url: rx(URL_USERINFO)?,
            expansion: rx(EXPANSION)?,
            credential: rx(CREDENTIAL_NAME)?,
        })
    }

    /// Whether `userinfo` names a credential: any expansion after the colon that ends the user
    /// (a colon inside an expansion, as in `${X:-}`, does not), or a credential-named one before it.
    fn carries_credential(&self, userinfo: &str) -> bool {
        let found: Vec<_> = self.expansion.captures_iter(userinfo).collect();
        let inside = |i: usize| {
            found
                .iter()
                .filter_map(|c| c.get(0))
                .any(|m| m.start() <= i && i < m.end())
        };
        let colon = userinfo
            .char_indices()
            .find(|(i, ch)| *ch == ':' && !inside(*i))
            .map_or(usize::MAX, |(i, _)| i);
        found.iter().any(|c| {
            let whole = c.get(0).map_or(0, |m| m.start());
            let name = c
                .get(1)
                .or_else(|| c.get(2))
                .or_else(|| c.get(3))
                .map_or("", |m| m.as_str());
            whole > colon || self.credential.is_match(name)
        })
    }

    /// The 1-based character column of each finding on `line`.
    #[must_use]
    pub fn findings(&self, line: &str) -> Vec<usize> {
        self.url
            .captures_iter(line)
            .filter(|c| self.carries_credential(c.get(1).map_or("", |m| m.as_str())))
            .filter_map(|c| c.get(0))
            .map(|m| line[..m.start()].chars().count() + 1)
            .collect()
    }
}

/// Whether `path` is prose, which this rule does not read.
#[must_use]
pub fn is_prose(path: &str) -> bool {
    std::path::Path::new(path)
        .extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| PROSE.contains(&e.to_ascii_lowercase().as_str()))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `scheme://userinfo@host/path`, built so no matchable URL is spelled in this source.
    fn url(userinfo: &str) -> String {
        format!("{}://{userinfo}@{}/x/tap.git", "https", "github.com")
    }

    fn hits(line: &str) -> usize {
        Matcher::compile().unwrap().findings(line).len()
    }

    /// THE CLASS: a credential expanded into a URL's userinfo, in either slot and every spelling.
    #[test]
    fn a_credential_expanded_into_a_url_is_a_finding() {
        for userinfo in [
            "${TAP_TOKEN}",
            "$TAP_TOKEN",
            "${{ secrets.TAP }}",
            "${{ github.token }}",
            "${GH_PAT}",
            "x-access-token:${{ secrets.GITHUB_TOKEN }}",
            "deploy:${DEPLOY}",
            "deploy:$P",
            "${API_KEY:-}",
        ] {
            let line = format!("git clone \"{}\" /tmp/tap", url(userinfo));
            assert_eq!(hits(&line), 1, "{line}");
        }
    }

    #[test]
    fn a_username_or_a_plain_url_is_not_one() {
        for line in [
            format!("ssh://${{USER}}@{}/x", "host"),
            url("git"),
            format!("{}://{}/x", "https", "github.com"),
            format!("{}://$HOST/a@b", "https"),
            format!("{}@{}:x/y.git", "git", "github.com"),
        ] {
            assert_eq!(hits(&line), 0, "{line}");
        }
    }

    #[test]
    fn the_column_is_the_urls_in_characters() {
        let line = format!("→ {}", url("${TAP_TOKEN}"));
        assert_eq!(Matcher::compile().unwrap().findings(&line), vec![3]);
    }

    #[test]
    fn prose_is_not_read() {
        assert!(is_prose("docs/BACKLOG.md") && is_prose("NOTES.TXT"));
        assert!(!is_prose(".github/workflows/release.yml") && !is_prose("tools/release.sh"));
    }
}
