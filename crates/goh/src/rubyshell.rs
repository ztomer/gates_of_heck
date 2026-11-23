//! The shell a Ruby source hands to a shell (GOH.LOCALERUBY).
//!
//! A Homebrew cask's `uninstall` script is a heredoc run by `/bin/sh -c`, and a formula's one-string `system "..."` goes through
//! `/bin/sh` too. A scanner that reads shell sources only never saw them (homebrew-tap's
//! `Casks/divoom-control.rb`, three `LC_ALL=C` prefixes inside one heredoc).
//!
//! WHAT IS SHELL: the body of a heredoc (`<<~TAG`, `<<-TAG`, `<<TAG`, quoted or not) that a shell
//! runs -- its opening line passes it after `"-c"` or to `system`, or the variable it is assigned
//! to is passed after `"-c"` elsewhere in the file -- and the one string of a `system "..."` call
//! (more than one argument is an argv, which no shell reads). Any other heredoc is a caveat, a
//! description or data: prose, never read.

use regex::Regex;
use std::sync::LazyLock;

fn rx(p: &str) -> Regex {
    Regex::new(p).unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
}

static OPEN: LazyLock<Regex> =
    LazyLock::new(|| rx(r#"(?:\b([a-z_]\w*)\s*=\s*)?<<[~-]?(['"]?)([A-Za-z_]\w*)['"]?"#));
static RUNS: LazyLock<Regex> = LazyLock::new(|| rx(r#"["']-c["']\s*,|\bsystem\b"#));
static SYSTEM: LazyLock<Regex> =
    LazyLock::new(|| rx(r#"\bsystem\s*\(?\s*"((?:[^"\\]|\\.)*)"\s*\)?\s*(?:#.*)?$"#));

/// `(1-based line, shell text)` for each line of `text` a shell runs.
#[must_use]
pub fn shell_lines(text: &str) -> Vec<(usize, String)> {
    let rows: Vec<&str> = text.lines().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i < rows.len() {
        let row = rows[i];
        if let Some(m) = SYSTEM.captures(row) {
            out.push((i + 1, m[1].to_owned()));
        }
        let Some(open) = OPEN.captures(row) else {
            i += 1;
            continue;
        };
        let tag = &open[3];
        let end = (i + 1..rows.len())
            .find(|j| rows[*j].trim() == tag)
            .unwrap_or(rows.len());
        let by_name = open.get(1).is_some_and(|var| {
            rx(&format!(
                r#"["']-c["']\s*,\s*{}\b"#,
                regex::escape(var.as_str())
            ))
            .is_match(text)
        });
        if RUNS.is_match(row) || by_name {
            out.extend((i + 1..end).map(|j| (j + 1, rows[j].to_owned())));
        }
        i = end + 1;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lines(text: &str) -> Vec<usize> {
        shell_lines(text).into_iter().map(|(n, _)| n).collect()
    }

    #[test]
    fn a_heredoc_a_shell_runs_is_shell() {
        let cask = "cask \"x\" do\n  stop = <<~'STOP'\n    live=$(ps -p 1)\n  STOP\n\n  \
                    uninstall script: { executable: \"/bin/sh\", args: [\"-c\", stop] }\nend\n";
        assert_eq!(lines(cask), vec![3]);
        let inline = "  system \"/bin/sh\", \"-c\", <<~EOS\n    sort x\n  EOS\n";
        assert_eq!(lines(inline), vec![2]);
    }

    #[test]
    fn prose_heredocs_and_argv_calls_are_not_shell() {
        let formula = "  def caveats\n    <<~EOS\n      run LC_ALL=C sort\n    EOS\n  end\n  \
                       system \"cargo\", \"install\"\n";
        let got = lines(formula);
        assert!(got.is_empty(), "expected no rows: {got:?}");
        let unused = "  stop = <<~STOP\n    x\n  STOP\n  other(\"-c\", stopper)\n";
        let got = lines(unused);
        assert!(got.is_empty(), "expected no rows: {got:?}");
    }

    #[test]
    fn a_one_string_system_is_shell() {
        let got = shell_lines("    system \"LC_ALL=C make all\"  # build\n");
        assert_eq!(got, vec![(1, "LC_ALL=C make all".to_owned())]);
    }
}
