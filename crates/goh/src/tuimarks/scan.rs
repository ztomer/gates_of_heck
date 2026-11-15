//! The finder: a shell `echo`/`printf` or a Python `print` whose text OPENS with a Kare mark -- the
//! line `tui/lib.sh` / `tui.lib` print for it (`info ok err warn die`).
//!
//! THE FALLBACK (GOH.ESCAPEOWNER, 2026-11-15). An ANSI escape (`\033[`) or a bracketed marker
//! (`[ OK ]`) is `goh status-markers`' token (`crate::shapes`: one owner per token), and
//! status-markers is an OPT-IN, so where a repo does not run it nothing judged those. This finder
//! opens on them too, as a [`Finding::fallback`]; the step (`super`) keeps such a finding only
//! where status-markers does not count that line, so a site is reported once, by one of the two.
//! The two patterns are status-markers' own (`crate::statusmark::scan::MARKER` and `ESCAPE`).
//!
//! Line-based, as `crate::statusmark::scan` is: the shapes are lexical, and the subject is every
//! shell and Python source. A whole-line comment is not read (one quoting the forbidden shape to
//! forbid it is the commonest). NOT SEEN: a mark held in a variable (`echo "$OK done"`), a mark
//! after other text (`printf '%s ✓\n'`), a `sys.stdout.write`, and a print whose string opens on
//! the next line.
//!
//! THE ESCAPE, the house's reasoned per-line shape: `# tui-mark-ok: <reason>` on the line, for a
//! fallback that runs where the lib cannot be loaded (`tools/gate.sh` when `tui/lib.sh` is absent).

use regex::Regex;
use std::sync::OnceLock;

/// The marks `tui/stylerc` owns (start, step, ok, err, warn): this scanner's own token.
const OPENER: &str = r"(?:→|·|✓|✗|⚠)";

/// The opener, named: `m` a Kare mark, `s` status-markers' token (the fallback, see the header).
fn opener() -> String {
    use crate::statusmark::scan::{ESCAPE, MARKER};
    format!("(?:(?P<m>{OPENER})|(?P<s>{MARKER}|{ESCAPE}))")
}

/// The per-line exemption: `# tui-mark-ok: <reason>`.
pub const MARKER: &str = "tui-mark-ok:";

/// Whether `line` carries the escape with a reason.
fn escaped(line: &str) -> bool {
    line.split_once(MARKER)
        .is_some_and(|(before, why)| before.contains('#') && !why.trim().is_empty())
}

/// One finding: its line, the mark it opens with, and whether that mark is `goh status-markers`'
/// token (an escape or a bracketed marker), judged here only where status-markers does not.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub line: usize,
    pub mark: String,
    pub fallback: bool,
}

/// Which language a source is lexed as.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lang {
    Shell,
    Python,
}

fn patterns() -> Result<&'static (Regex, Regex), String> {
    static COMPILED: OnceLock<Result<(Regex, Regex), String>> = OnceLock::new();
    COMPILED
        .get_or_init(|| {
            let rx = |p: &str| Regex::new(p).map_err(|e| format!("tui-marks pattern {p:?}: {e}"));
            let open = opener();
            // A command position, an optional `builtin`/`command`, the options, an optional quote
            // (`$'...'` too), blanks, the mark.
            let shell = format!(
                r#"(?:^|[\s;&|({{`!])(?:builtin\s+|command\s+)?(?:echo|printf)\s+(?:-[A-Za-z-]+\s+)*(?:\$?["'])?\s*{open}"#
            );
            // `print(` not in backticks (Python has none: a docstring quoting the shape, as goh's
            // tests/test_status_markers.py does), an optional string prefix, any quote (triple
            // too), blanks, the mark.
            let python = format!(
                r#"(?:^|[^`\w])print\(\s*(?:[rRbBuUfF]{{1,2}})?(?:"""|'''|["'])\s*{open}"#
            );
            Ok((rx(&shell)?, rx(&python)?))
        })
        .as_ref()
        .map_err(Clone::clone)
}

/// Every line this scanner judges in `text`, the escaped ones included.
///
/// An opening mark on a line that is not a whole-line comment; exempting a line is a verdict on
/// it. What the differential in `crate::shapes` counts as judged: the fallback openers too, as a
/// repo that does not run status-markers judges them.
///
/// # Errors
///
/// A pattern that does not compile.
pub fn judged(text: &str, lang: Lang) -> Result<Vec<usize>, String> {
    Ok(opening(text, lang)?.map(|(i, _)| i + 1).collect())
}

/// `(index, line)` of each line that is not a whole-line comment and opens a print with a mark.
fn opening(text: &str, lang: Lang) -> Result<impl Iterator<Item = (usize, &str)>, String> {
    let (shell, python) = patterns()?;
    let rx = match lang {
        Lang::Shell => shell,
        Lang::Python => python,
    };
    Ok(text
        .lines()
        .enumerate()
        .filter(|(_, line)| !line.trim_start().starts_with('#'))
        .filter(move |(_, line)| rx.is_match(line)))
}

/// Every opening mark in `text`, one per line, in line order.
///
/// # Errors
///
/// A pattern that does not compile, which is this file's defect and never a clean scan.
pub fn findings(text: &str, lang: Lang) -> Result<Vec<Finding>, String> {
    let (shell, python) = patterns()?;
    let rx = match lang {
        Lang::Shell => shell,
        Lang::Python => python,
    };
    Ok(text
        .lines()
        .enumerate()
        .filter(|(_, line)| !line.trim_start().starts_with('#') && !escaped(line))
        .filter_map(|(i, line)| {
            let c = rx.captures(line)?;
            let (m, fallback) = c
                .name("m")
                .map(|m| (m, false))
                .or_else(|| c.name("s").map(|s| (s, true)))?;
            Some(Finding {
                line: i + 1,
                mark: m.as_str().to_string(),
                fallback,
            })
        })
        .collect::<Vec<_>>())
}
