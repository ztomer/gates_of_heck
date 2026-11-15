//! The finder: a shell `echo`/`printf` or a Python `print` whose text OPENS with a Kare mark or an
//! ANSI escape -- the line `tui/lib.sh` / `tui.lib` print for it (`info ok err warn die`).
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

/// The marks `tui/stylerc` owns (start, step, ok, err, warn), and the spellings of ESC `[` (a CSI)
/// as they appear in source text.
const OPENER: &str = r"(?:→|·|✓|✗|⚠|\\(?:033|x1[bB]|e|u001[bB])\[)";

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
