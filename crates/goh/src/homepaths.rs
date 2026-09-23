//! Hard-coded home-path scan — Rust port of `checks/check_no_home_paths.py`.
//!
//! A shipped script, binary, or doc naming a checkout by absolute location
//! works on one machine at one moment. Four shapes fail: `/Users/<n>/…`,
//! `/home/<n>/…`, `~/Projects/…`, `$HOME/Projects/…` (braced or bare).
//!
//! The reference uses look-behind assertions (`(?<![\w.])…`), which the
//! `regex` crate cannot express. Each pattern is matched WITHOUT its guard
//! and the preceding character is checked by hand instead — the guard is
//! zero-width in the reference, so accepted matches start at the same byte
//! and columns agree exactly.
//!
//! Line splitting follows `str::lines`; the reference uses `splitlines`
//! (additionally breaks on vertical tab and form feed). No tracked file
//! relies on those as line breaks — accepted, same as the emoji port.

/// One home-path shape: its report name plus the unguarded match pattern.
pub struct Shape {
    /// Report name, mirroring the reference `PATTERNS` kinds.
    pub kind: &'static str,
    /// Match pattern without the look-behind guard.
    pub pattern: &'static str,
    /// Reject when the preceding character is a word char or `.`.
    /// (The tilde shape rejects on word chars only — `a~/Projects/`
    /// is not a path, `x.~/Projects/` still is.)
    pub dot_guards: bool,
}

/// The four shapes, in reference order (report order follows it).
pub const SHAPES: &[Shape] = &[
    Shape {
        kind: "macOS home path",
        pattern: r"/Users/[A-Za-z0-9_.-]+/",
        dot_guards: true,
    },
    Shape {
        kind: "linux home path",
        pattern: r"/home/[A-Za-z0-9_.-]+/",
        dot_guards: true,
    },
    Shape {
        kind: "tilde checkout path",
        pattern: r"~/Projects/",
        dot_guards: false,
    },
    Shape {
        kind: "HOME checkout path",
        pattern: r"\$\{?HOME\}?/Projects/",
        dot_guards: false,
    },
];

/// True for a word character in the reference sense (`\w` ASCII).
const fn is_word_char(ch: char) -> bool {
    ch.is_ascii_alphanumeric() || ch == '_'
}

/// True when a match starting at byte `start` in `line` survives its shape's
/// guard: no word char (and no `.` for the rooted shapes) before it.
fn guard_passes(line: &str, start: usize, dot_guards: bool) -> bool {
    let Some(prev) = line[..start].chars().next_back() else {
        return true;
    };
    if is_word_char(prev) {
        return false;
    }
    if dot_guards && prev == '.' {
        return false;
    }
    true
}

/// One hard-coded home path.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Hit {
    /// Repo-relative path.
    pub path: String,
    /// 1-based line number.
    pub lineno: usize,
    /// 1-based character column of the match start.
    pub col: usize,
    /// Shape kind, mirroring the reference report text.
    pub kind: &'static str,
}

/// Compiled shapes plus the suppression and env-default patterns.
pub struct Scanner {
    shapes: Vec<(regex::Regex, &'static str, bool)>,
    marker: regex::Regex,
    env_default: regex::Regex,
}

impl Scanner {
    /// Compile every pattern.
    ///
    /// # Errors
    ///
    /// Returns a message only if a built-in pattern fails to compile,
    /// which is a programming error, never input-dependent.
    pub fn compile() -> Result<Self, String> {
        let mut shapes = Vec::with_capacity(SHAPES.len());
        for shape in SHAPES {
            let rx = regex::Regex::new(shape.pattern)
                .map_err(|e| format!("built-in home-path pattern failed: {e}"))?;
            shapes.push((rx, shape.kind, shape.dot_guards));
        }
        Ok(Self {
            shapes,
            marker: regex::Regex::new(r"path-ok:\s*\S")
                .map_err(|e| format!("built-in marker pattern failed: {e}"))?,
            env_default: regex::Regex::new(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-[^}]*\}")
                .map_err(|e| format!("built-in env-default pattern failed: {e}"))?,
        })
    }

    /// True when `line` (or its predecessor) carries a reasoned `path-ok:`.
    /// A bare marker with no reason suppresses nothing.
    #[must_use]
    pub fn suppressed(&self, prev: Option<&str>, line: &str) -> bool {
        self.marker.is_match(line) || prev.is_some_and(|above| self.marker.is_match(above))
    }

    /// `(1-based char column, kind)` for every unsuppressed-by-position
    /// finding on `line`. Suppression is the caller's job (`suppressed`),
    /// exactly like the reference splits `_findings` from `_suppressed`.
    #[must_use]
    pub fn findings(&self, line: &str) -> Vec<(usize, &'static str)> {
        let spans: Vec<(usize, usize)> = self
            .env_default
            .find_iter(line)
            .map(|m| (m.start(), m.end()))
            .collect();
        let mut out = Vec::new();
        for (rx, kind, dot_guards) in &self.shapes {
            for m in rx.find_iter(line) {
                if spans.iter().any(|(a, b)| *a <= m.start() && m.start() < *b) {
                    continue;
                }
                if !guard_passes(line, m.start(), *dot_guards) {
                    continue;
                }
                out.push((line[..m.start()].chars().count() + 1, *kind));
            }
        }
        out
    }
}

/// Scan decoded text, returning every finding plus the decodable-file count
/// contribution (1 when called per file — the caller aggregates).
#[must_use]
pub fn scan_text(scanner: &Scanner, path: &str, text: &str) -> Vec<Hit> {
    let mut hits = Vec::new();
    let mut prev: Option<&str> = None;
    for (lineno, line) in text.lines().enumerate() {
        if !scanner.suppressed(prev, line) {
            for (col, kind) in scanner.findings(line) {
                hits.push(Hit {
                    path: path.to_owned(),
                    lineno: lineno + 1,
                    col,
                    kind,
                });
            }
        }
        prev = Some(line);
    }
    hits
}

/// Scan every in-scope file under `root`. Returns findings plus the
/// decodable-file count (the zero-file branches below need it).
///
/// # Errors
///
/// Returns a message when git lists files and fails, or a built-in
/// pattern fails to compile.
pub fn scan_root(
    root: &std::path::Path,
    exclude: Option<&regex::Regex>,
    staged: bool,
) -> Result<(Vec<Hit>, usize), String> {
    let files = crate::gitutil::listed_files(root, staged)?;
    scan_files(root, &files, exclude, staged)
}

/// Scan a pre-enumerated file list: the structural pipeline lists the tree
/// once and shares it across scanners instead of each one spawning git.
///
/// # Errors
///
/// Returns a message when a built-in pattern fails to compile.
pub fn scan_files(
    root: &std::path::Path,
    files: &[String],
    exclude: Option<&regex::Regex>,
    staged: bool,
) -> Result<(Vec<Hit>, usize), String> {
    let scanner = Scanner::compile()?;
    let mut hits = Vec::new();
    let mut checked = 0;
    for path in files {
        if let Some(rx) = exclude {
            if rx.is_match(path) {
                continue;
            }
        }
        let Some(blob) = crate::gitutil::content_bytes(root, path, staged) else {
            continue;
        };
        let Ok(text) = std::str::from_utf8(&blob) else {
            continue;
        };
        checked += 1;
        hits.extend(scan_text(&scanner, path, text));
    }
    Ok((hits, checked))
}

/// The set of files this scan saw, for the empty-scope refusal the
/// reference performs: an empty index is honest, an empty tree is not.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ScopeVerdict {
    /// Findings to report.
    Violations,
    /// Nothing staged — honest pass, staged mode only.
    NothingStaged,
    /// Zero files over the full tree — refusal, never compliance.
    NothingToCheck,
    /// Clean pass over `checked` files.
    Clean,
}

/// Classify a finished scan the way the reference does.
#[must_use]
pub const fn classify(hits: &[Hit], checked: usize, staged: bool) -> ScopeVerdict {
    if !hits.is_empty() {
        ScopeVerdict::Violations
    } else if checked == 0 {
        if staged {
            ScopeVerdict::NothingStaged
        } else {
            ScopeVerdict::NothingToCheck
        }
    } else {
        ScopeVerdict::Clean
    }
}

/// Full violation block, mirroring the reference message verbatim.
#[must_use]
pub fn format_report(hits: &[Hit], staged: bool) -> String {
    let scope = if staged { "staged" } else { "tracked" };
    let mut shown = String::new();
    for hit in hits.iter().take(crate::gitutil::MAX_REPORT_HITS) {
        let line = format!("  {}:{}:{}: {}\n", hit.path, hit.lineno, hit.col, hit.kind);
        shown.push_str(&line);
    }
    let overflow = if hits.len() > crate::gitutil::MAX_REPORT_HITS {
        format!(
            "  … and {} more\n",
            hits.len() - crate::gitutil::MAX_REPORT_HITS
        )
    } else {
        String::new()
    };
    format!(
        "✗ HARD-CODED HOME PATH in {} location(s) ({scope}) — derive it from the executable, the repo root, an env var or the bundle; a path that must stand carries `path-ok: <reason>` on the line or above:\n{shown}{overflow}",
        hits.len()
    )
}

/// The clean-pass line, mirroring the reference.
#[must_use]
pub fn format_ok(checked: usize, staged: bool) -> String {
    let scope = if staged { "staged" } else { "tracked" };
    format!("✓ [no_home_paths] OK — {checked} {scope} files clean")
}

/// The empty-scope lines, mirroring the reference (each `print` ends the
/// line, so every arm carries its newline).
#[must_use]
pub fn format_empty(verdict: ScopeVerdict) -> String {
    match verdict {
        ScopeVerdict::NothingStaged => {
            "✓ [no_home_paths] nothing staged — 0 files to check\n".to_owned()
        }
        ScopeVerdict::NothingToCheck => {
            "✗ [no_home_paths] nothing to check (tracked) — refusing to report clean over zero files\n"
                .to_owned()
        }
        ScopeVerdict::Violations | ScopeVerdict::Clean => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scanner() -> Scanner {
        Scanner::compile().expect("built-in patterns compile")
    }

    #[test]
    fn four_shapes_fire() {
        let sc = scanner();
        let cases = [
            ("x = \"/Users/me/src/x\"", "macOS home path"),
            ("x = \"/home/me/src/x\"", "linux home path"),
            ("cd ~/Projects/x", "tilde checkout path"),
            ("cd $HOME/Projects/x", "HOME checkout path"),
            ("cd ${HOME}/Projects/x", "HOME checkout path"),
        ];
        for (line, kind) in cases {
            let hits = sc.findings(line);
            assert_eq!(hits.len(), 1, "{line}");
            assert_eq!(hits[0].1, kind, "{line}");
        }
    }

    #[test]
    fn guards_reject_word_and_dot_prefixes() {
        let sc = scanner();
        // Word char before the match: not a path start.
        assert!(sc.findings("a/Users/me/x/").is_empty());
        assert!(sc.findings("a~/Projects/x").is_empty());
        // Dot before a rooted shape: version strings, not paths.
        assert!(sc.findings("v1./Users/me/x/").is_empty());
        // Dot before tilde: the tilde shape guards words only.
        assert_eq!(sc.findings("x.~/Projects/x").len(), 1);
        // Line start always passes.
        assert_eq!(sc.findings("/Users/me/x/").len(), 1);
    }

    #[test]
    fn columns_are_character_based() {
        let sc = scanner();
        let hits = sc.findings("→ /Users/me/x/");
        assert_eq!(hits[0].0, 3);
    }

    #[test]
    fn env_default_expansions_are_not_findings() {
        let sc = scanner();
        assert!(sc
            .findings("GOH=\"${GOH_DIR:-$HOME/Projects/gates_of_heck}\"")
            .is_empty());
    }

    #[test]
    fn reasoned_marker_suppresses_bare_marker_does_not() {
        let sc = scanner();
        assert!(sc.suppressed(None, "x = \"/Users/m/x\";  // path-ok: dev fallback"));
        assert!(sc.suppressed(Some("// path-ok: recorded incident"), "x = \"/Users/m/x\""));
        assert!(!sc.suppressed(None, "x = \"/Users/m/x\";  // path-ok:"));
        assert!(!sc.suppressed(None, "x = \"/Users/m/x\""));
    }

    #[test]
    fn scope_verdicts_match_the_reference_branches() {
        let hit = Hit {
            path: "f".to_owned(),
            lineno: 1,
            col: 1,
            kind: "macOS home path",
        };
        assert_eq!(classify(&[hit], 1, false), ScopeVerdict::Violations);
        assert_eq!(classify(&[], 0, true), ScopeVerdict::NothingStaged);
        assert_eq!(classify(&[], 0, false), ScopeVerdict::NothingToCheck);
        assert_eq!(classify(&[], 3, false), ScopeVerdict::Clean);
    }
}
