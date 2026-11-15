//! The changelog's Unreleased stanza is empty; each change's entry is its own file
//! (`GOH_CHANGELOG_FRAGMENTS=on`, GOH.CLASS.CHECKOUT part d).
//!
//! The class: one shared checkout, concurrent writers. Every goh lane that landed on 2026-11-14
//! wrote its entry into the same `## Unreleased` stanza of `CHANGELOG.md`, so every land after the
//! first conflicted on it, four times in one night. Two changes share no line when each writes its
//! OWN file, `changelog/<ID>.md` beside the changelog; the release step folds them into the stanza
//! it cuts (`tools/release-kit/changelog_cut.py cut --fragments`, `changelog_fragments.py`) and
//! its commit deletes them. This step is what makes the shared stanza unwritable rather than
//! discouraged: with the key on, a commit whose changelog holds a line under an Unreleased heading
//! is refused, naming the line and the fragment to write instead. A release commit passes the same
//! rule without being told apart -- its cut leaves Unreleased empty -- so no "is this a release"
//! guess is made, and none can be wrong.
//!
//! SCOPE: the repo's `CHANGELOG.md`, else `docs/CHANGELOG.md` (the index's copy at `--staged`). A
//! repo with the key on and neither file has nothing to judge, and says so. Unset or `off` skips
//! the step; any other value is an error naming the key.

use goh_tui::{ERR, OK};
use std::fmt::Write as _;
use std::path::Path;

const TAG: &str = "[changelog_fragments]";
/// The step's label, the same line as in `structural_steps.txt`.
pub const LABEL: &str = "no changelog entry under Unreleased";
const SOURCE: &str = "crates/goh/src/changelog_fragments.rs";

/// Is the step on? Unset or `off` is off; `on` is on.
///
/// # Errors
///
/// A message naming the key when the value is neither: a typo that silently turned the rule off
/// would reopen the stanza two lanes write.
pub fn enabled(raw: Option<&str>) -> Result<bool, String> {
    match raw.map_or("", str::trim) {
        "" | "off" => Ok(false),
        "on" => Ok(true),
        other => Err(format!(
            "GOH_CHANGELOG_FRAGMENTS is neither on nor off: {other:?}"
        )),
    }
}

/// Is `line` a heading of level 2 or deeper, and is it an Unreleased one?
fn heading(line: &str) -> Option<(usize, bool)> {
    let level = line.bytes().take_while(|b| *b == b'#').count();
    let rest = &line[level..];
    if level < 2 || !rest.starts_with([' ', '\t']) {
        return None;
    }
    let title = rest.trim().to_ascii_lowercase();
    let title = title.strip_prefix('[').unwrap_or(&title);
    let unreleased = title
        .strip_prefix("unreleased")
        .is_some_and(|t| t.is_empty() || t.starts_with([']', ' ', '\t']));
    Some((level, unreleased))
}

/// The 1-based line numbers of every non-blank line inside an Unreleased section of `text`: from
/// its heading to the next heading at its level or above.
#[must_use]
pub fn unreleased_lines(text: &str) -> Vec<usize> {
    let mut out = Vec::new();
    let mut open: Option<usize> = None;
    for (n, line) in text.lines().enumerate() {
        if let Some((level, unreleased)) = heading(line) {
            if open.is_none_or(|l| level <= l) {
                open = unreleased.then_some(level);
                continue;
            }
        }
        if open.is_some() && !line.trim().is_empty() {
            out.push(n + 1);
        }
    }
    out
}

/// The verdict over `repo`: `(exit code, stdout, stderr)`, as `estate_seed::step` reads it.
fn judge(repo: &Path, staged: bool) -> (i32, String, String) {
    let file = ["CHANGELOG.md", "docs/CHANGELOG.md"].into_iter().find(|c| {
        if staged {
            crate::gitutil::content_bytes(repo, c, true).is_some()
        } else {
            repo.join(c).is_file()
        }
    });
    let Some(file) = file else {
        let line = format!(
            "{OK} {TAG} OK — no CHANGELOG.md or docs/CHANGELOG.md: no Unreleased stanza to keep empty\n"
        );
        return (0, String::new(), line);
    };
    let blob = if staged {
        crate::gitutil::content_bytes(repo, file, true)
    } else {
        std::fs::read(repo.join(file)).ok()
    };
    let Some(text) = blob.and_then(|b| String::from_utf8(b).ok()) else {
        return (
            2,
            String::new(),
            format!("{ERR} {TAG} {file} cannot be read as UTF-8\n"),
        );
    };
    let lines = unreleased_lines(&text);
    if lines.is_empty() {
        let read = text.lines().count();
        return (
            0,
            String::new(),
            format!("{OK} {TAG} OK — {file}: Unreleased is empty ({read} lines read)\n"),
        );
    }
    let dir = if file.starts_with("docs/") {
        "docs/changelog"
    } else {
        "changelog"
    };
    let mut err = format!(
        "{ERR} {TAG} {file}: {} under Unreleased, which GOH_CHANGELOG_FRAGMENTS=on keeps empty:\n",
        goh_tui::counted(lines.len(), "line")
    );
    for n in lines.iter().take(5) {
        let shown = text.lines().nth(n - 1).unwrap_or_default();
        let _ = writeln!(err, "    {file}:{n}: {}", crate::estate_seed::clip(shown));
    }
    let _ = write!(
        err,
        "    Two lanes writing one stanza conflict at every land (GOH.CLASS.CHECKOUT). Move each entry\n    \
         into its own fragment, {dir}/<ITEM-ID>.md (`* ` entries, no heading), and leave Unreleased\n    \
         empty: the release step folds the fragments into the stanza it cuts.\n"
    );
    (1, String::new(), err)
}

/// The structural step: off unless the repo asked for it.
#[must_use]
pub fn step(repo: &Path, cfg: &crate::gatesrc::Gatesrc, staged: bool) -> Option<i32> {
    match enabled(cfg.changelog_fragments.as_deref()) {
        Ok(false) => None,
        Ok(true) => crate::estate_seed::step(LABEL, SOURCE, judge(repo, staged)),
        Err(m) => crate::estate_seed::step(
            LABEL,
            SOURCE,
            (2, String::new(), format!("{ERR} {TAG} {m}\n")),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_entry_under_unreleased_is_named_by_line() {
        let text =
            "# CHANGELOG\n\n## Unreleased\n\n* **x.** y\n  more\n\n## v1.0.0 -- one\n\n* a\n";
        assert_eq!(unreleased_lines(text), vec![5, 6]);
    }

    #[test]
    fn an_empty_unreleased_and_older_stanzas_pass() {
        let text = "# CHANGELOG\n\n## Unreleased\n\n## v1.0.0 -- one\n\n* a\n\n### Fixed\n\n* b\n";
        assert_eq!(unreleased_lines(text), Vec::<usize>::new());
    }

    #[test]
    fn a_sub_heading_inside_unreleased_stays_inside_it() {
        let text = "## [Unreleased]\n### Fixed\n- z\n## [1.0.0]\n- a\n";
        assert_eq!(unreleased_lines(text), vec![2, 3]);
    }

    #[test]
    fn a_word_that_starts_with_unreleased_is_not_the_heading() {
        assert_eq!(heading("## Unreleasedness"), Some((2, false)));
        assert_eq!(heading("## Unreleased -- next"), Some((2, true)));
        assert_eq!(heading("# Unreleased"), None);
        assert_eq!(heading("##nospace"), None);
    }

    #[test]
    fn the_key_is_on_off_or_an_error_naming_it() {
        assert_eq!(enabled(None), Ok(false));
        assert_eq!(enabled(Some("off")), Ok(false));
        assert_eq!(enabled(Some("on")), Ok(true));
        assert!(enabled(Some("yes")).is_err_and(|m| m.contains("GOH_CHANGELOG_FRAGMENTS")));
    }
}
