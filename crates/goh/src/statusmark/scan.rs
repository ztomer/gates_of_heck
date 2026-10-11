//! The finder: a bracketed status marker in a string, or a hand-rolled ANSI colour escape.
//!
//! The house style is `tui/lib.sh` / `tui.lib` (`info`/`ok`/`err`/`warn`), which renders the Kare
//! icons, restrains its colours and honours `NO_COLOR` and a non-tty. A script that prints
//! `[ Ok  ]` or writes `\033[32m` itself does none of that, and drifts from every other script.
//!
//! Line-based on purpose: the subject is every source language a repo carries, and both shapes
//! are lexical. A marker counts only INSIDE a quoted string with whitespace on both sides of its
//! word (`"[ PASS ]"`), so a Swift array type `[Error]` or a list `[OK]` is not one (`==>` and
//! `-->` need no padding). A whole-line comment is not a use (one quoting the forbidden style to forbid
//! it is the commonest), and a doc is not source. What the lexing misses -- a marker assembled from
//! pieces, an escape built with `chr(27)` -- is invisible here, and said so.

use std::sync::OnceLock;

use regex::Regex;

/// One finding: its line, and what it is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub line: usize,
    pub what: String,
}

/// The status words a hand-rolled marker spells, in the case variants the estate used.
const MARKER: &str = r"\[\s*(?:==>|-->)\s*\]|\[\s+(?i:ok|pass|passed|fail|failed|err|error|wrn|warn|warning|info|skip|done)\s+\]";
/// The spellings of ESC followed by `[` (a CSI) as they appear in source text.
const ESCAPE: &str = r"\\(?:033|x1[bB]|e|u001[bB]|u\{0*1[bB]\})\[";

fn patterns() -> Result<&'static (Regex, Regex), String> {
    static COMPILED: OnceLock<Result<(Regex, Regex), String>> = OnceLock::new();
    COMPILED
        .get_or_init(|| {
            let rx =
                |p: &str| Regex::new(p).map_err(|e| format!("status-marker pattern {p:?}: {e}"));
            Ok((rx(MARKER)?, rx(ESCAPE)?))
        })
        .as_ref()
        .map_err(Clone::clone)
}

/// The whole-line comment openers of the languages in the subject.
const COMMENT_OPENERS: [&str; 3] = ["#", "//", "/*"];

fn is_comment(line: &str) -> bool {
    let t = line.trim_start();
    !t.starts_with("#!") && COMMENT_OPENERS.iter().any(|c| t.starts_with(c))
        || t.starts_with("* ")
        || t == "*"
}

/// Whether byte offset `at` of `line` sits inside a `"` or `'` string: an odd count of unescaped
/// quotes of either kind before it.
fn in_string(line: &str, at: usize) -> bool {
    let (mut double, mut single, mut escaped) = (false, false, false);
    for c in line[..at].chars() {
        match c {
            _ if escaped => escaped = false,
            '\\' => escaped = true,
            '"' if !single => double = !double,
            '\'' if !double => single = !single,
            _ => {}
        }
    }
    double || single
}

/// Every marker and escape in `source`, in line order.
///
/// # Errors
///
/// A pattern that does not compile, which is this file's defect and never a clean scan.
pub fn findings(source: &str) -> Result<Vec<Finding>, String> {
    let (marker, escape) = patterns()?;
    let mut out = Vec::new();
    for (i, line) in source.lines().enumerate() {
        if is_comment(line) {
            continue;
        }
        if let Some(m) = marker.find_iter(line).find(|m| in_string(line, m.start())) {
            out.push(Finding {
                line: i + 1,
                what: format!("a hand-rolled status marker `{}`", m.as_str()),
            });
        } else if let Some(m) = escape.find(line) {
            out.push(Finding {
                line: i + 1,
                what: format!("a hand-rolled colour escape `{}`", m.as_str()),
            });
        }
    }
    Ok(out)
}
