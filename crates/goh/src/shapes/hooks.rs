//! Each declared scanner's JUDGED hook: the lines it reads a verdict on, through the scanner's own
//! selection and finder (never a copy of either, or the table would vouch for the copy).

/// `goh moving-tags`: every line of every image reference it read, whatever the verdict.
///
/// # Errors
///
/// None; the signature is the table's.
pub fn moving_tags(rel: &str, text: &str) -> Result<Vec<usize>, String> {
    Ok(crate::movingtag::scan::judged_lines(rel, text))
}

/// `goh status-markers`: its findings in a source file.
///
/// # Errors
///
/// A finder pattern that does not compile.
pub fn status_markers(rel: &str, text: &str) -> Result<Vec<usize>, String> {
    if !crate::statusmark::is_source(rel) {
        return Ok(Vec::new());
    }
    Ok(crate::statusmark::scan::findings(text)?
        .into_iter()
        .map(|f| f.line)
        .collect())
}

/// `goh tui-marks`: every opening mark it judges, escaped lines included.
///
/// An exemption is a verdict. Its exempt files (the house lib, a git hook) are judged too, by
/// rule, and so are status-markers' escape and marker opening a print: its fallback, as a repo
/// that does not run status-markers judges them (GOH.ESCAPEOWNER).
///
/// # Errors
///
/// A finder pattern that does not compile.
pub fn tui_marks(rel: &str, text: &str) -> Result<Vec<usize>, String> {
    crate::tuimarks::lang_of(rel, text).map_or_else(
        || Ok(Vec::new()),
        |lang| crate::tuimarks::scan::judged(text, lang),
    )
}

/// `goh run-paths`: its findings in a shell, Python or Rust source.
///
/// # Errors
///
/// None; the signature is the table's.
pub fn run_paths(rel: &str, text: &str) -> Result<Vec<usize>, String> {
    Ok(crate::runpaths::judged_lines(rel, text))
}

/// `goh goh-pins`: the goh pins it judges in a workflow or composite action.
///
/// # Errors
///
/// None; the signature is the table's.
pub fn goh_pins(rel: &str, text: &str) -> Result<Vec<usize>, String> {
    Ok(crate::gohpin::scan::judged_lines(rel, text))
}

/// `goh locale-prefix`: its findings in a bash source.
///
/// # Errors
///
/// None; the signature is the table's.
pub fn locale_prefix(rel: &str, text: &str) -> Result<Vec<usize>, String> {
    Ok(crate::localeprefix::judged_lines(rel, text))
}

/// `goh dead-port`: its findings in a test source.
///
/// # Errors
///
/// None; the signature is the table's.
pub fn dead_port(rel: &str, text: &str) -> Result<Vec<usize>, String> {
    Ok(crate::deadport::judged_lines(rel, text))
}
