//! Committed-secret scan — Rust port of `checks/check_no_secrets.py`.
//!
//! Deliberately narrow: known high-confidence prefixes, private-key
//! headers, and a credential-named key with a 32+ character quoted value
//! (a tracked runtime config's shape). No entropy heuristics — an unproven heuristic cries wolf, gets
//! switched off, and is worse than a narrow gate that never does. Tails are
//! length-gated so prose cannot trip them.
//!
//! A revoked vector is suppressed with a reasoned marker on its line or the
//! one above (`secret-ok: <reason>`); a bare marker suppresses nothing.

/// (kind, pattern) pairs. Mirrors `PATTERNS` in order — findings report
/// pattern-grouped, so order is user-visible.
pub const PATTERNS: &[(&str, &str)] = &[
    ("github token", r"\bghp_[A-Za-z0-9]{36,}"),
    ("github oauth token", r"\bgho_[A-Za-z0-9]{36,}"),
    ("github fine-grained pat", r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    ("anthropic api key", r"\bsk-ant-[A-Za-z0-9\-_]{20,}"),
    ("openai project key", r"\bsk-proj-[A-Za-z0-9\-_]{20,}"),
    ("openai api key", r"\bsk-[A-Za-z0-9]{20,}"),
    ("slack token", r"\bxox[bpas]-[A-Za-z0-9\-]{10,}"),
    ("aws access key id", r"\bAKIA[0-9A-Z]{16}"),
    (
        "private key",
        r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----",
    ),
    // A credential-NAMED key holding a long opaque value — the shape of a
    // tracked runtime config. The 32-char floor keeps placeholders out
    // (measured 2026-09-19: 33 hits at 8 chars across every local repo,
    // one real; exactly that one at 32).
    (
        "credential-named key with a long value",
        r#"(?i)\b(?:api_key|apikey|api_token|access_token|auth_token|client_secret|secret_key|password)\b["']?\s*[:=]\s*["'][^"'\s]{32,}["']"#,
    ),
];

/// Suppression marker with a mandatory reason. Mirrors `MARKER`.
pub const MARKER_SRC: &str = r"secret-ok:\s*\S";

/// Compiled secret patterns plus the suppression marker.
pub type Compiled = (Vec<(&'static str, regex::Regex)>, regex::Regex);

/// One secret sighting.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    /// Repo-relative path.
    pub path: String,
    /// 1-based line number.
    pub lineno: usize,
    /// 1-based character column (Python `match.start` counts chars).
    pub col: usize,
    /// Pattern kind, e.g. `"github token"`.
    pub kind: &'static str,
}

/// Compiled patterns plus the suppression marker.
///
/// # Errors
///
/// Returns the regex error text if a built-in pattern fails to compile (a bug).
pub fn compile_all() -> Result<Compiled, String> {
    let mut patterns = Vec::with_capacity(PATTERNS.len());
    for (kind, src) in PATTERNS {
        match regex::Regex::new(src) {
            Ok(rx) => patterns.push((*kind, rx)),
            Err(e) => return Err(format!("bad built-in pattern {kind}: {e}")),
        }
    }
    match regex::Regex::new(MARKER_SRC) {
        Ok(marker) => Ok((patterns, marker)),
        Err(e) => Err(format!("bad suppression marker: {e}")),
    }
}

/// True when `line` (or the line above it) carries a reasoned marker.
/// Mirrors `_suppressed`.
#[must_use]
pub fn is_suppressed(marker: &regex::Regex, prev: Option<&str>, line: &str) -> bool {
    marker.is_match(line) || prev.is_some_and(|above| marker.is_match(above))
}

/// Every pattern hit on one line, as `(char_col, kind)`.
#[must_use]
pub fn findings(
    line: &str,
    patterns: &[(&'static str, regex::Regex)],
) -> Vec<(usize, &'static str)> {
    let mut out = Vec::new();
    for (kind, rx) in patterns {
        for m in rx.find_iter(line) {
            out.push((line[..m.start()].chars().count() + 1, *kind));
        }
    }
    out
}

/// Scan every in-scope file under `root`. Returns findings plus the
/// decodable-file count.
///
/// # Errors
///
/// Returns a message when git lists files and fails.
pub fn scan_root(
    root: &std::path::Path,
    exclude: Option<&regex::Regex>,
    staged: bool,
) -> Result<(Vec<Finding>, usize), String> {
    let (patterns, marker) = compile_all()?;
    let mut bad = Vec::new();
    let mut checked = 0;
    for path in crate::gitutil::listed_files(root, staged)? {
        if let Some(rx) = exclude {
            if rx.is_match(&path) {
                continue;
            }
        }
        let Some(blob) = crate::gitutil::content_bytes(root, &path, staged) else {
            continue;
        };
        let Ok(text) = std::str::from_utf8(&blob) else {
            continue;
        };
        checked += 1;
        let mut prev: Option<&str> = None;
        for (lineno, line) in text.lines().enumerate() {
            if !is_suppressed(&marker, prev, line) {
                for (col, kind) in findings(line, &patterns) {
                    bad.push(Finding {
                        path: path.clone(),
                        lineno: lineno + 1,
                        col,
                        kind,
                    });
                }
            }
            prev = Some(line);
        }
    }
    Ok((bad, checked))
}

/// Full violation block. Shared by the `secrets` subcommand and the
/// structural pipeline so both print one text.
#[must_use]
pub fn format_report(bad: &[Finding], staged: bool) -> String {
    let scope = if staged { "staged" } else { "tracked" };
    let mut shown = String::new();
    for hit in bad.iter().take(200) {
        let line = format!("  {}:{}:{}: {}\n", hit.path, hit.lineno, hit.col, hit.kind);
        shown.push_str(&line);
    }
    let overflow = if bad.len() > 200 {
        format!("  … and {} more\n", bad.len() - 200)
    } else {
        String::new()
    };
    format!(
        "✗ DISALLOWED SECRET in {} location(s) ({scope}) — rotate the credential; revoked vectors use `secret-ok: <reason>` on the line or above:\n{shown}{overflow}",
        bad.len()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn compiled() -> Compiled {
        compile_all().unwrap()
    }

    #[test]
    fn tails_are_length_gated() {
        let (patterns, _) = compiled();
        // Built without literals: a matchable tail in source would trip the gate.
        let long = format!("x ghp_{}", "A".repeat(36));
        assert!(!findings(&long, &patterns).is_empty());
        let short = format!("x ghp_{}", "A".repeat(10));
        assert!(findings(&short, &patterns).is_empty());
    }

    #[test]
    fn marker_needs_a_reason_on_its_line_or_above() {
        let (_, marker) = compiled();
        let hit = "k = 1";
        assert!(!is_suppressed(&marker, None, hit));
        assert!(is_suppressed(
            &marker,
            None,
            "t = 1  # secret-ok: revoked vector"
        ));
        assert!(is_suppressed(
            &marker,
            Some("# secret-ok: rotated 2026-01-01"),
            hit
        ));
        assert!(!is_suppressed(&marker, Some("# secret-ok:"), hit));
        assert!(!is_suppressed(&marker, None, "# secret-ok:"));
    }

    #[test]
    fn columns_count_characters() {
        let (patterns, _) = compiled();
        let line = format!("→ x ghp_{}", "A".repeat(36));
        let hits = findings(&line, &patterns);
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].0, 5);
    }

    #[test]
    fn a_credential_named_key_fires_only_on_a_long_value() {
        let (patterns, _) = compiled();
        // Built by repetition: a matchable value in source would trip this gate.
        let name = concat!("api_", "key");
        let long = format!(r#"  "{name}": "osk-v1.{}""#, "a".repeat(40));
        assert_eq!(findings(&long, &patterns).len(), 1);
        assert_eq!(
            findings(&long, &patterns)[0].1,
            "credential-named key with a long value"
        );
        let toml = format!("{} = '{}'", concat!("pass", "word"), "x".repeat(32));
        assert!(!findings(&toml, &patterns).is_empty());
        // Placeholders and short fixtures are not findings.
        let short = format!(r#""{name}": "{}""#, "a".repeat(31));
        assert!(findings(&short, &patterns).is_empty());
        assert!(findings(&format!("{name} = \"LIDARR_API_KEY\""), &patterns).is_empty());
        // An unquoted or env-sourced value is not one either.
        assert!(findings(&format!("{name} = os.environ[\"K\"]"), &patterns).is_empty());
    }

    #[test]
    fn key_headers_match_all_forms() {
        // Split literals: a whole header in source would trip this very gate.
        let (patterns, _) = compiled();
        for header in [
            concat!("-----BEGIN ", "PRIVATE KEY-----"),
            concat!("-----BEGIN RSA ", "PRIVATE KEY-----"),
            concat!("-----BEGIN OPENSSH ", "PRIVATE KEY-----"),
            concat!("-----BEGIN EC ", "PRIVATE KEY-----"),
            concat!("-----BEGIN DSA ", "PRIVATE KEY-----"),
        ] {
            assert!(!findings(header, &patterns).is_empty(), "{header}");
        }
        assert!(findings(concat!("-----BEGIN ", "PUBLIC KEY-----"), &patterns).is_empty());
    }
}
