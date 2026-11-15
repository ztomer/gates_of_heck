//! The finder: a shell `echo`/`printf` or a Python `print` whose text OPENS with a Kare mark -- the
//! line `tui/lib.sh` / `tui.lib` print for it (`info ok err warn die`).
//!
//! NOT AN ANSI ESCAPE (GOH.CLASS.SHAPE, 2026-11-15). The first version also opened on `\033[`, and
//! `goh status-markers` judges every such escape in every source language, so one print line was
//! counted by two scanners. One shape has one owner, and the shape table (`crate::shapes`) refuses a
//! scanner that judges a token another row owns: the escape is status-markers'.
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

/// The marks `tui/stylerc` owns (start, step, ok, err, warn). An ANSI escape is not one of them: it
/// is `goh status-markers`' token (see the header).
const OPENER: &str = r"(?:→|·|✓|✗|⚠)";

/// The per-line exemption: `# tui-mark-ok: <reason>`.
pub const MARKER: &str = "tui-mark-ok:";

/// Whether `line` carries the escape with a reason.
fn escaped(line: &str) -> bool {
    line.split_once(MARKER)
        .is_some_and(|(before, why)| before.contains('#') && !why.trim().is_empty())
}

/// One finding: its line, and the mark it opens with.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub line: usize,
    pub mark: String,
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
            // A command position, an optional `builtin`/`command`, the options, an optional quote
            // (`$'...'` too), blanks, the mark.
            let shell = format!(
                r#"(?:^|[\s;&|({{`!])(?:builtin\s+|command\s+)?(?:echo|printf)\s+(?:-[A-Za-z-]+\s+)*(?:\$?["'])?\s*(?P<m>{OPENER})"#
            );
            // `print(`, an optional string prefix, any quote (triple too), blanks, the mark.
            let python =
                format!(r#"\bprint\(\s*(?:[rRbBuUfF]{{1,2}})?(?:"""|'''|["'])\s*(?P<m>{OPENER})"#);
            Ok((rx(&shell)?, rx(&python)?))
        })
        .as_ref()
        .map_err(Clone::clone)
}

/// Every line this scanner judges in `text`, the escaped ones included.
///
/// An opening mark on a line that is not a whole-line comment; exempting a line is a verdict on
/// it. What the differential in `crate::shapes` counts as judged.
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
            rx.captures(line)
                .and_then(|c| c.name("m"))
                .map(|m| Finding {
                    line: i + 1,
                    mark: m.as_str().to_string(),
                })
        })
        .collect::<Vec<_>>())
}
