//! Merge-conflict marker scan — Rust port of `checks/check_no_conflict_markers.py`.
//!
//! Only a marker at the start of a line counts: `<<<<<<< ` / `>>>>>>> ` /
//! `||||||| ` (the diff3 base marker), each followed by whitespace or
//! end-of-line. `=======` alone is ordinary Markdown underlining, not a
//! marker. Binary blobs (NUL in the first 8000 bytes) are skipped.

/// One marker sighting.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Violation {
    /// Repo-relative path.
    pub path: String,
    /// 1-based line number.
    pub line_no: usize,
    /// The offending line, UTF-8 lossy, cut to 80 characters.
    pub text: String,
}

/// The three marker runs: ours, theirs, and the diff3 base marker.
const MARKER_RUNS: [&[u8]; 3] = [b"<<<<<<<", b">>>>>>>", b"|||||||"];

/// True for a single line starting a conflict marker.
#[must_use]
pub fn is_marker_line(line: &[u8]) -> bool {
    let Some(rest) = MARKER_RUNS.iter().find_map(|run| line.strip_prefix(*run)) else {
        return false;
    };
    matches!(
        rest.first(),
        None | Some(b' ' | b'\t' | b'\n' | b'\r' | b'\x0C' | b'\x0B')
    )
}

/// Truncate to 80 characters on a char boundary.
fn truncate80(text: &str) -> String {
    match text.char_indices().nth(80) {
        Some((idx, _)) => text[..idx].to_owned(),
        None => text.to_owned(),
    }
}

/// Scan one blob, returning every marker sighting with 1-based line numbers.
#[must_use]
pub fn scan_blob(path: &str, blob: &[u8]) -> Vec<Violation> {
    if blob[..blob.len().min(8000)].contains(&0) {
        return Vec::new();
    }
    blob.split(|b| *b == b'\n')
        .enumerate()
        .filter(|(_, line)| is_marker_line(line))
        .map(|(idx, line)| Violation {
            path: path.to_owned(),
            line_no: idx + 1,
            text: truncate80(&String::from_utf8_lossy(line)),
        })
        .collect()
}

/// Scan every in-scope file under `root`.
///
/// Staged checks police the index blob, full runs the worktree (see
/// `gitutil`). Absence is never compliance: callers handle the not-a-repo
/// case before calling.
///
/// # Errors
///
/// Returns a message when git lists files and fails.
pub fn scan_root(root: &std::path::Path, staged: bool) -> Result<Vec<Violation>, String> {
    let mut bad = Vec::new();
    for path in crate::gitutil::listed_files(root, staged)? {
        if let Some(blob) = crate::gitutil::content_bytes(root, &path, staged) {
            bad.extend(scan_blob(&path, &blob));
        }
    }
    Ok(bad)
}

/// Full violation block: header plus one line per hit. Shared by the
/// `markers` subcommand and the structural pipeline so both print one text.
#[must_use]
pub fn format_report(bad: &[Violation]) -> String {
    let mut shown = String::new();
    for hit in bad {
        let line = format!("    {}:{}: {}\n", hit.path, hit.line_no, hit.text);
        shown.push_str(&line);
    }
    format!("✗ [no_conflict_markers] merge conflict markers found:\n{shown}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn branch_markers_count() {
        assert!(is_marker_line(b"<<<<<<< ours"));
        assert!(is_marker_line(b">>>>>>> theirs"));
        assert!(is_marker_line(b"||||||| base"));
        assert!(is_marker_line(b"<<<<<<<"));
    }

    #[test]
    fn non_markers_pass() {
        // `=======` is Markdown underlining.
        assert!(!is_marker_line(b"======="));
        // Not at line start (shell redirection in prose).
        assert!(!is_marker_line(b"echo foo >>>>>>> bar"));
        // Marker glyphs without the trailing run of seven.
        assert!(!is_marker_line(b"<<<<<< six"));
        assert!(!is_marker_line(b">> two"));
        // Seven markers glued to text is not a marker.
        assert!(!is_marker_line(b"<<<<<<<glued"));
        assert!(!is_marker_line(b""));
    }

    #[test]
    fn blob_scan_numbers_lines_from_one() {
        let blob = b"clean\n<<<<<<< ours\nmiddle\n>>>>>>> theirs\n";
        let hits = scan_blob("f.txt", blob);
        assert_eq!(
            hits.iter().map(|v| v.line_no).collect::<Vec<_>>(),
            vec![2, 4]
        );
        assert_eq!(hits[0].text, "<<<<<<< ours");
    }

    #[test]
    fn binary_blobs_are_skipped() {
        let mut blob = vec![b'a'; 100];
        blob[50] = 0;
        blob.extend_from_slice(b"\n<<<<<<< ours\n");
        assert!(scan_blob("f.bin", &blob).is_empty());
    }
}
