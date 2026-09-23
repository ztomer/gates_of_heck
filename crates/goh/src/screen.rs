//! Screen-presentation scan — Rust port of
//! `checks/check_no_screen_presentation.py`.
//!
//! A test that reaches the live display (real windows, real input, real
//! screen capture) flashes over the user's session and cannot run
//! headless. This scans TEST TARGETS — explicit paths or a `--scope`
//! glob — for per-language presentation APIs in `.swift`, `.m`/`.mm`
//! and `.py` files.
//!
//! Ports of note, each mirroring the reference exactly:
//!
//! - `mask_swift`: the one-pass comment/string masker (comments and
//!   literals become spaces, newlines survive, block comments nest,
//!   triple-quoted literals span lines). Character-indexed, like the
//!   reference's list-of-chars scan.
//! - Type-position exemption: a bare-identifier occurrence sitting in a
//!   Swift type shape (`[NSScreen]`, `-> NSScreen`, `is NSScreen`,
//!   `x: NSScreen`, `Foo<NSScreen>`) annotates a fake's protocol surface
//!   without touching the display — exempt, unless followed by `.`.
//! - `fnmatch` for `--scope`: translated to an anchored regex (`*` →
//!   `.*` crossing slashes, like `fnmatch`, which has no `**`
//!   specialness), because no glob crate is a dependency.
//! - The `regex` crate has no start-anchored `match`: the member-access
//!   probe (`\s*\.` at the match end) is compiled with `\A` instead.
//!
//! Line splitting is `split("\n")` on both the masked and original text
//! (never `lines()`/`splitlines()`: masking can erase a `\v` inside a
//! literal and desync the two lists — the reference's 1:1 contract).

use crate::screen_mask::mask_swift;
use crate::screen_shapes::{
    Shape, ALLOW_MARKER, MEMBER_ACCESS, OBJC_SHAPES, PYTHON_SHAPES, PY_EXECUTES, PY_GUARD,
    SWIFT_ANNOT_BEFORE, SWIFT_CAST_BEFORE, SWIFT_RETURN_BEFORE, SWIFT_SHAPES,
};

/// Compiled per-language scanners plus the shared probes.
pub struct Scanner {
    /// Swift shapes in order.
    swift: Vec<(regex::Regex, &'static str, bool)>,
    /// Objective-C shapes in order.
    objc: Vec<(regex::Regex, &'static str)>,
    /// Python shapes in order with their executor flags.
    python: Vec<(regex::Regex, &'static str, bool)>,
    /// Executor probe.
    py_executes: regex::Regex,
    /// Member-access probe.
    member_access: regex::Regex,
    /// Type-shape probes.
    cast_before: regex::Regex,
    return_before: regex::Regex,
    annot_before: regex::Regex,
    /// Headless-contract probe.
    py_guard: regex::Regex,
}

impl Scanner {
    /// Compile every built-in pattern.
    ///
    /// # Errors
    ///
    /// Returns a message only if a built-in pattern fails to compile,
    /// which is a programming error, never input-dependent.
    pub fn compile() -> Result<Self, String> {
        fn compile_all(
            shapes: &[Shape],
        ) -> Result<Vec<(regex::Regex, &'static str, bool)>, String> {
            shapes
                .iter()
                .map(|s| {
                    regex::Regex::new(s.pattern)
                        .map(|rx| (rx, s.why, s.type_exempt))
                        .map_err(|e| format!("built-in screen pattern failed: {e}"))
                })
                .collect()
        }
        fn one(pattern: &str) -> Result<regex::Regex, String> {
            regex::Regex::new(pattern).map_err(|e| format!("built-in screen probe failed: {e}"))
        }
        Ok(Self {
            swift: compile_all(SWIFT_SHAPES)?,
            objc: compile_all(OBJC_SHAPES)?
                .into_iter()
                .map(|(rx, why, _)| (rx, why))
                .collect(),
            python: PYTHON_SHAPES
                .iter()
                .map(|s| {
                    regex::Regex::new(s.pattern)
                        .map(|rx| (rx, s.why, s.needs_exec))
                        .map_err(|e| format!("built-in screen pattern failed: {e}"))
                })
                .collect::<Result<Vec<_>, _>>()?,
            py_executes: one(PY_EXECUTES)?,
            member_access: one(MEMBER_ACCESS)?,
            cast_before: one(SWIFT_CAST_BEFORE)?,
            return_before: one(SWIFT_RETURN_BEFORE)?,
            annot_before: one(SWIFT_ANNOT_BEFORE)?,
            py_guard: one(PY_GUARD)?,
        })
    }

    /// True when `prefix` ends inside an unclosed `<…>` argument list.
    /// Mirrors `_generic_context` (a `(`/`[` entered first means a
    /// call/literal region, not a generic).
    fn generic_context(prefix: &str) -> bool {
        let mut depth = 0;
        for ch in prefix.chars().rev() {
            match ch {
                '>' => depth += 1,
                '<' => {
                    if depth == 0 {
                        return true;
                    }
                    depth -= 1;
                }
                '(' | '[' => return false,
                _ => {}
            }
        }
        false
    }

    /// Is THIS occurrence a Swift type, not a live use? Mirrors
    /// `_in_swift_type_position` byte-for-byte in logic (match positions
    /// are byte offsets on both sides; patterns anchor on ASCII).
    fn in_type_position(&self, probe: &str, start: usize, end: usize) -> bool {
        if self.member_access.is_match(&probe[end..]) {
            return false;
        }
        let prefix = &probe[..start];
        self.cast_before.is_match(prefix)
            || self.return_before.is_match(prefix)
            || self.annot_before.is_match(prefix)
            || Self::generic_context(prefix)
    }
}

/// One presentation call.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Hit {
    /// 1-based line number.
    pub lineno: usize,
    /// Why this reaches the screen.
    pub why: &'static str,
}

/// Supported test-target suffixes. A plain function (slicing is not
/// const-compatible); the dispatch table below keeps it branch-shallow.
#[must_use]
pub fn language_of(rel: &str) -> Option<Language> {
    let bytes = rel.as_bytes();
    let mut i = bytes.len();
    while i > 0 {
        i -= 1;
        if bytes[i] == b'.' {
            break;
        }
        if bytes[i] == b'/' {
            return None;
        }
    }
    if i == 0 {
        return None;
    }
    match &bytes[i..] {
        b".swift" => Some(Language::Swift),
        b".m" | b".mm" => Some(Language::ObjC),
        b".py" => Some(Language::Python),
        _ => None,
    }
}

/// Test-target languages.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Language {
    /// Swift sources (masked scan + type exemption).
    Swift,
    /// Objective-C sources.
    ObjC,
    /// Python harnesses.
    Python,
}

/// `screen-ok:` on this line or the one above it. The marker is looked up
/// on the ORIGINAL lines (masking preserves them 1:1 separately).
fn has_marker(lines: &[&str], idx: usize) -> bool {
    lines[idx].contains(ALLOW_MARKER) || (idx > 0 && lines[idx - 1].contains(ALLOW_MARKER))
}

/// Comment-prefix lines explain the rule; they are never violations.
/// Mirrors the per-language prefix tables (`#` for Swift is in the
/// reference table — directives and all — so it stays).
fn is_comment_line(line: &str, language: Language) -> bool {
    let stripped = line.trim_start();
    match language {
        Language::Swift => stripped.starts_with('#') || stripped.starts_with("//"),
        Language::ObjC => stripped.starts_with("//"),
        Language::Python => stripped.starts_with('#'),
    }
}

/// Yield every unguarded presentation call in one decoded blob.
///
/// Mirrors `check_text`: unknown suffixes never reach here (the caller
/// filters), Python files declaring the headless contract return empty,
/// Swift patterns match masked lines while markers read original ones.
#[must_use]
pub fn check_text(scanner: &Scanner, rel: &str, text: &str) -> Vec<Hit> {
    let Some(language) = language_of(rel) else {
        return Vec::new();
    };
    if language == Language::Python && scanner.py_guard.is_match(text) {
        return Vec::new();
    }
    let masked;
    let scan_lines: Option<Vec<&str>> = if language == Language::Swift {
        masked = mask_swift(text);
        Some(masked.split('\n').collect())
    } else {
        None
    };
    // `split("\n")` on BOTH sides, never `lines()`: the mask preserves
    // newlines 1:1 and `lines()` would additionally break on `\v` etc.,
    // desyncing the two lists.
    let lines: Vec<&str> = text.split('\n').collect();
    let mut found = Vec::new();
    for (idx, line) in lines.iter().enumerate() {
        if is_comment_line(line, language) {
            continue;
        }
        if has_marker(&lines, idx) {
            continue;
        }
        let probe = scan_lines.as_ref().map_or(*line, |masked| masked[idx]);
        let matched = match language {
            Language::Swift => {
                let mut why = None;
                for (rx, w, type_exempt) in &scanner.swift {
                    let mut hits: Vec<_> = rx.find_iter(probe).collect();
                    if *type_exempt {
                        hits.retain(|m| !scanner.in_type_position(probe, m.start(), m.end()));
                    }
                    if !hits.is_empty() {
                        why = Some(*w);
                        break;
                    }
                }
                why
            }
            Language::ObjC => scanner
                .objc
                .iter()
                .find(|(rx, _)| rx.find_iter(probe).next().is_some())
                .map(|(_, w)| *w),
            Language::Python => {
                let mut why = None;
                for (rx, w, needs_exec) in &scanner.python {
                    if rx.find_iter(probe).next().is_none() {
                        continue;
                    }
                    if *needs_exec && !scanner.py_executes.is_match(line) {
                        continue;
                    }
                    why = Some(*w);
                    break;
                }
                why
            }
        };
        if let Some(why) = matched {
            found.push(Hit {
                lineno: idx + 1,
                why,
            });
        }
    }
    found
}

/// Translate an `fnmatch` glob to an anchored regex: `*` → `.*` (crossing
/// slashes — `fnmatch` has no `**` specialness), `?` → `.`, `[...]` →
/// a character class, everything else literal.
fn fnmatch_to_regex(pattern: &str) -> String {
    let mut out = String::from("\\A(?:");
    let mut chars = pattern.chars().peekable();
    while let Some(ch) = chars.next() {
        match ch {
            '*' => out.push_str(".*"),
            '?' => out.push('.'),
            '[' => {
                let mut cls = String::from("[");
                if chars.peek() == Some(&'!') {
                    cls.push('^');
                    chars.next();
                }
                if chars.peek() == Some(&']') {
                    cls.push(']');
                    chars.next();
                }
                for ch in chars.by_ref() {
                    cls.push(ch);
                    if ch == ']' {
                        break;
                    }
                }
                // An unterminated class is literal in fnmatch.
                if cls.ends_with(']') {
                    out.push_str(&cls);
                } else {
                    out.push_str("\\[");
                    out.push_str(&cls[1..]);
                }
            }
            _ => {
                out.push_str(&regex::escape(&ch.to_string()));
            }
        }
    }
    out.push_str(")\\z");
    out
}

/// Repo-root-relative candidates: explicit args (files as-is, directories
/// walked without `__pycache__`, sorted, deduped), else `--scope`
/// `fnmatch` matches over the listed files.
///
/// # Errors
///
/// Returns a message when the scope pattern fails to compile (a
/// programming error only for built patterns — user patterns come from
/// the CLI and are reported, never panicked on).
pub fn collect_targets(
    root: &std::path::Path,
    staged: bool,
    paths: &[String],
    scope: Option<&str>,
) -> Result<(Vec<String>, Vec<String>), String> {
    if !paths.is_empty() {
        let mut out = Vec::new();
        for p in paths {
            let abs = if std::path::Path::new(p).is_absolute() {
                std::path::PathBuf::from(p)
            } else {
                root.join(p)
            };
            let rel = abs
                .strip_prefix(root)
                .map_or_else(|_| p.clone(), |r| r.to_string_lossy().into_owned());
            if abs.is_dir() {
                let mut stack = vec![abs];
                while let Some(dir) = stack.pop() {
                    let mut entries: Vec<_> = std::fs::read_dir(&dir)
                        .map_err(|e| format!("cannot list {}: {e}", dir.display()))?
                        .filter_map(Result::ok)
                        .collect();
                    entries.sort_by_key(std::fs::DirEntry::file_name);
                    for entry in entries {
                        let path = entry.path();
                        // `os.walk` lists symlinked dirs but never
                        // descends them (`followlinks=False`); a symlinked
                        // file is still scanned through the link.
                        let is_link = path
                            .symlink_metadata()
                            .is_ok_and(|m| m.file_type().is_symlink());
                        if path.is_dir() && !is_link {
                            if path.file_name().is_some_and(|n| n != "__pycache__") {
                                stack.push(path);
                            }
                        } else {
                            out.push(path.strip_prefix(root).map_or_else(
                                |_| path.to_string_lossy().into_owned(),
                                |r| r.to_string_lossy().into_owned(),
                            ));
                        }
                    }
                }
            } else {
                out.push(rel);
            }
        }
        out.sort();
        out.dedup();
        return Ok((out, Vec::new()));
    }
    if let Some(pattern) = scope {
        let rx = regex::Regex::new(&fnmatch_to_regex(pattern))
            .map_err(|e| format!("bad --scope glob {pattern:?}: {e}"))?;
        let files = crate::gitutil::listed_files(root, staged)
            .map_err(|e| format!("cannot list files: {e}"))?;
        return Ok((
            Vec::new(),
            files.into_iter().filter(|f| rx.is_match(f)).collect(),
        ));
    }
    Ok((Vec::new(), Vec::new()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scanner() -> Scanner {
        Scanner::compile().expect("built-in patterns compile")
    }

    #[test]
    fn masker_preserves_lines_and_masks_strings() {
        let sc =
            mask_swift("let a = \"NSScreen.main\" // NSScreen\n/* NSScreen */\nNSScreen.main\n");
        let lines: Vec<&str> = sc.split('\n').collect();
        assert_eq!(lines.len(), 4);
        assert!(!lines[0].contains("NSScreen"));
        assert!(!lines[1].contains("NSScreen"));
        assert!(lines[2].contains("NSScreen.main"));
    }

    #[test]
    fn masker_handles_triple_quotes_and_escapes() {
        let text = "let s = \"\"\"NSScreen\nstill string\"\"\"\nlet t = \"a\\\"NSScreen\"\nNSScreen.main\n";
        let masked = mask_swift(text);
        assert_eq!(masked.split('\n').count(), text.split('\n').count());
        assert!(masked
            .lines()
            .last()
            .unwrap_or("")
            .contains("NSScreen.main"));
        assert!(!masked.split('\n').next().unwrap_or("").contains("NSScreen"));
    }

    #[test]
    fn type_positions_exempt_member_access_flags() {
        let sc = scanner();
        for line in [
            "let s: [NSScreen]",
            "let s: NSScreen?",
            "func f() -> NSScreen",
            "if x is NSScreen",
            "let d: Foo<NSScreen>",
        ] {
            assert!(
                check_text(&sc, "a.swift", &format!("{line}\n")).is_empty(),
                "{line}"
            );
        }
        assert_eq!(check_text(&sc, "a.swift", "NSScreen.main\n").len(), 1);
        assert_eq!(
            check_text(&sc, "a.swift", "let s = NSScreen.screens\n").len(),
            1
        );
    }

    #[test]
    fn markers_and_comment_lines_pass() {
        let sc = scanner();
        assert!(check_text(&sc, "a.swift", "NSScreen.main // screen-ok: fake\n").is_empty());
        assert!(check_text(&sc, "a.swift", "// screen-ok: whole file\nNSScreen.main\n").is_empty());
        assert!(check_text(&sc, "a.swift", "// NSScreen.main explains the rule\n").is_empty());
        assert_eq!(check_text(&sc, "a.swift", "NSScreen.main\n").len(), 1);
    }

    #[test]
    fn python_executor_rule() {
        let sc = scanner();
        // Prose naming a command executes nothing.
        assert!(check_text(&sc, "t.py", "# screencapture is banned\n").is_empty());
        assert_eq!(
            check_text(&sc, "t.py", "subprocess.run([\"screencapture\", \"-x\"])\n").len(),
            1
        );
        assert_eq!(check_text(&sc, "t.py", "import pyautogui\n").len(), 1);
        // Headless-contract files are out of scope entirely.
        assert!(check_text(
            &sc,
            "t.py",
            "GOH_HEADLESS = 1\nsubprocess.run([\"screencapture\"])\n"
        )
        .is_empty());
    }

    #[test]
    fn fnmatch_crosses_slashes_like_fnmatch() {
        // Expectations verified against `fnmatch.fnmatch` itself: `*`
        // crosses slashes (no `**` specialness), and `*/` still needs its
        // slash — `tests/**/*.swift` does NOT match `tests/x.swift`.
        let rx = regex::Regex::new(&fnmatch_to_regex("tests/**/*.swift")).expect("compiles");
        assert!(rx.is_match("tests/a/b.swift"));
        assert!(!rx.is_match("tests/x.swift"));
        assert!(!rx.is_match("tests/x.py"));
        let rx = regex::Regex::new(&fnmatch_to_regex("tests/*")).expect("compiles");
        assert!(rx.is_match("tests/deep/nested.swift"));
    }
}
