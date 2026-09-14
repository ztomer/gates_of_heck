//! File-length cap — Rust port of `checks/check_file_length.py`.
//!
//! Only source suffixes are measured; data, docs, and lockfiles are
//! legitimately long. The line definition is the shared one in `gitutil`
//! (a trailing newline terminates, it does not begin another). Unlike the
//! markers scan, binary blobs are NOT skipped here — the reference counts
//! whatever bytes a source-suffixed file holds.

/// Source suffixes the cap applies to. Mirrors `SOURCE_SUFFIXES`.
pub const SOURCE_SUFFIXES: &[&str] = &[
    ".rs", ".py", ".swift", ".c", ".h", ".cpp", ".hpp", ".cc", ".m", ".mm", ".kt", ".java", ".go",
    ".ts", ".tsx", ".js", ".jsx", ".sh", ".bash", ".rb",
];

/// One file over the cap.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OverCap {
    /// Repo-relative path.
    pub path: String,
    /// Line count.
    pub lines: usize,
}

/// True when the cap polices `path` (source suffix, not excluded).
#[must_use]
pub fn is_measured(path: &str, exclude: Option<&regex::Regex>) -> bool {
    if !SOURCE_SUFFIXES.iter().any(|suffix| path.ends_with(suffix)) {
        return false;
    }
    exclude.is_none_or(|rx| !rx.is_match(path))
}

/// Scan every in-scope file under `root`, returning files over `max` sorted
/// longest-first, plus the measured file count.
///
/// # Errors
///
/// Returns a message when git lists files and fails.
pub fn scan_root(
    root: &std::path::Path,
    max: usize,
    exclude: Option<&regex::Regex>,
    staged: bool,
) -> Result<(Vec<OverCap>, usize), String> {
    let mut over = Vec::new();
    let mut checked = 0;
    for path in crate::gitutil::listed_files(root, staged)? {
        if !is_measured(&path, exclude) {
            continue;
        }
        if let Some(blob) = crate::gitutil::content_bytes(root, &path, staged) {
            checked += 1;
            let n = crate::gitutil::line_count(&blob);
            if n > max {
                over.push(OverCap { path, lines: n });
            }
        }
    }
    over.sort_by_key(|hit| std::cmp::Reverse(hit.lines));
    Ok((over, checked))
}

/// Full violation block. Shared by the `length` subcommand and the
/// structural pipeline so both print one text.
#[must_use]
pub fn format_report(over: &[OverCap], max: usize) -> String {
    let mut shown = String::new();
    for hit in over {
        let line = format!(
            "    {:>6} lines  {}  (+{})\n",
            hit.lines,
            hit.path,
            hit.lines - max
        );
        shown.push_str(&line);
    }
    format!(
        "✗ [file_length] {} file(s) over the {max}-line cap:\n{shown}\n  Split them. If a file genuinely cannot be split (vendored or\n  generated), add it to GOH_LINE_EXCLUDE in .gatesrc — with a reason.\n",
        over.len()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_source_suffixes_are_measured() {
        assert!(is_measured("a.py", None));
        assert!(is_measured("b.sh", None));
        assert!(!is_measured("c.md", None));
        assert!(!is_measured("Cargo.lock", None));
        assert!(!is_measured("notes.txt", None));
    }

    #[test]
    fn exclusion_is_a_search() {
        let rx = regex::Regex::new("third_party/|\\.generated\\.").unwrap();
        assert!(!is_measured("third_party/a.py", Some(&rx)));
        assert!(!is_measured("x.generated.py", Some(&rx)));
        assert!(is_measured("src/a.py", Some(&rx)));
    }

    #[test]
    fn boundary_is_strictly_greater() {
        // 500 lines with and without trailing newline: both are 500, both pass.
        let terminated = "x\n".repeat(500);
        let unterminated = "x\n".repeat(499) + "x";
        assert_eq!(crate::gitutil::line_count(terminated.as_bytes()), 500);
        assert_eq!(crate::gitutil::line_count(unterminated.as_bytes()), 500);
        let over = "x\n".repeat(501);
        assert_eq!(crate::gitutil::line_count(over.as_bytes()), 501);
    }
}
