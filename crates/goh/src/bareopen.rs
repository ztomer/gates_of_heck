//! A bare `open` hands the launched app the CALLER's environment (`goh bare-open`, GOH.CLEANOPEN).
//!
//! macOS `open` passes its own environment to the application it launches, and the app outlives
//! the script (PPID 1). Probed 2026-11-13 with a throwaway app that writes its environ to a file,
//! launched with a sentinel set in the caller: `open -a`, `open -n -a`, `open --env`,
//! `osascript ... launch` and `launchctl asuser` all carried the sentinel and the caller's
//! `GOH_DIR`; only `env -i /usr/bin/open` (and the Finder) gave launchd's login environment. So
//! `ZoneWM`'s installer, run by a ship, started `ZoneWM` with `GOH_SHIP_ITEM` and a `GOH_DIR` naming a
//! transient gate export with no `bin/`, and every app `ZoneWM` relaunched (`Claude.app` among them)
//! handed it to every agent of that session. The one launch is `goh launch-app PATH`
//! (`crate::launchapp`), which clears the environment first.
//!
//! WHAT IS JUDGED: every `open` (or `/usr/bin/open`) at a command position in a shell source
//! (`crate::shellsrc`): the start of a line, or after `;`, `&`, `|`, `(`, `{`, `` ` ``, `!`, or
//! one of `then do else exec nohup time sudo`. A word inside an argument (`info "launch: open
//! -a X"`, `command -v open`) is not a command. Comment lines are not read.
//!
//! WIRING: a structural step in EVERY repo. The estate's sites on the day it landed are seeded in
//! `bare_open_seed.json` (`crate::estate_seed`): seeded files warn, anything else is red.

use crate::estate_seed::{self, Kind, Report, Sites};
use std::path::Path;

const TAG: &str = "[bare_open]";
/// The step's label, the same line as in `structural_steps.txt`.
pub const LABEL: &str = "no bare open of an app";

/// The seed of the estate's sites on the day the check landed.
pub const KIND: Kind = Kind {
    file: "bare_open_seed.json",
    unit: "bare open",
    name: "bare-open seed",
};

const FIX: &str = "    `open` hands the app it launches THIS shell's environment, and the app outlives the script:\n    \
     a ship's GOH_DIR (a transient gate export) and GOH_SHIP_ITEM reached ZoneWM and every app it\n    \
     relaunched (GOH.CLEANOPEN). Launch with launchd's login environment instead:\n      \
     \"$GOH_DIR/bin/goh\" launch-app \"$APP\"                 (foreground)\n      \
     \"$GOH_DIR/bin/goh\" launch-app --background \"$APP\"    (open -g)\n";

/// The words after which the next word is a command.
const LEADERS: [&str; 7] = ["then", "do", "else", "exec", "nohup", "time", "sudo"];

/// The command-position `open`s in one line: the byte offset of each.
#[must_use]
pub fn scan_line(line: &str) -> Vec<usize> {
    if line.trim_start().starts_with('#') {
        return Vec::new();
    }
    let mut out = Vec::new();
    let mut from = 0;
    while let Some(k) = line[from..].find("open") {
        let at = from + k;
        from = at + "open".len();
        let after = line[from..].chars().next();
        if after.is_some_and(|c| !c.is_whitespace() && c != ';' && c != ')') {
            continue;
        }
        let start = line[..at].strip_suffix("/usr/bin/").map_or(at, str::len);
        if quoted(&line[..start]) {
            continue;
        }
        let before = line[..start].trim_end();
        if line[..start].ends_with(|c: char| c.is_alphanumeric() || "_-./$\"'".contains(c)) {
            continue;
        }
        let leads = before.is_empty()
            || before.ends_with([';', '&', '|', '(', '{', '`', '!'])
            || LEADERS.iter().any(|w| {
                before.strip_suffix(w).is_some_and(|rest| {
                    rest.chars()
                        .next_back()
                        .is_none_or(|c| c.is_whitespace() || ";&|(".contains(c))
                })
            });
        if leads {
            out.push(start);
        }
    }
    out
}

/// Whether the end of `head` sits inside a quoted string (`'...'` or `"..."`, `\\` escapes outside
/// single quotes): a word there is an argument, never the command.
fn quoted(head: &str) -> bool {
    let (mut single, mut double, mut escaped) = (false, false, false);
    for c in head.chars() {
        if escaped {
            escaped = false;
        } else if c == '\\' && !single {
            escaped = true;
        } else if c == '\'' && !double {
            single = !single;
        } else if c == '"' && !single {
            double = !double;
        }
    }
    single || double
}

/// The heredoc a line opens (`<<WORD`, `<<-WORD`, `<< 'WORD'`, `<<"WORD"`; never `<<<`): its
/// terminator.
fn heredoc_opens(line: &str) -> Option<String> {
    let mut from = 0;
    while let Some(k) = line[from..].find("<<") {
        let at = from + k;
        from = at + 2;
        let rest = &line[from..];
        if rest.starts_with('<') || quoted(&line[..at]) {
            from += 1;
            continue;
        }
        let rest = rest.strip_prefix('-').unwrap_or(rest).trim_start();
        let word: String = rest
            .trim_start_matches(['\'', '"', '\\'])
            .chars()
            .take_while(|c| c.is_alphanumeric() || *c == '_')
            .collect();
        // `$((1<<2))` is a shift, not a heredoc: a terminator starts with a letter or `_`.
        if word.starts_with(|c: char| c.is_alphabetic() || c == '_') {
            return Some(word);
        }
    }
    None
}

/// `(line number, line)` of every command-position `open` in a shell source, heredoc bodies
/// (an `AppleScript`'s `open`, a here-document's prose) not read.
#[must_use]
pub fn scan_text(text: &str) -> Vec<(usize, &str)> {
    let mut out = Vec::new();
    let mut until: Option<String> = None;
    for (n, line) in text.lines().enumerate() {
        if let Some(end) = &until {
            if line.trim() == end {
                until = None;
            }
            continue;
        }
        if !scan_line(line).is_empty() {
            out.push((n + 1, line));
        }
        until = heredoc_opens(line);
    }
    out
}

/// The sites in the shell sources among `files`.
fn sites(root: &Path, files: &[String], exclude: &str, staged: bool) -> Result<Sites, String> {
    let exclude = crate::steps::compile_exclude(exclude)?;
    let sources = crate::shellsrc::sources(root, files, exclude.as_ref(), staged);
    crate::subject::record(&crate::subject::BARE_OPEN, sources.len());
    let mut out = Sites::new();
    for (rel, text) in &sources {
        let found: Vec<String> = scan_text(text)
            .into_iter()
            .map(|(n, line)| {
                format!(
                    "    {rel}:{n}: {}  -- the app gets this shell's environment",
                    estate_seed::clip(line)
                )
            })
            .collect();
        if !found.is_empty() {
            out.insert(rel.clone(), found);
        }
    }
    Ok(out)
}

/// The verdict over `files`: `(exit code, stdout, stderr)`.
fn judge(root: &Path, files: &[String], exclude: &str, staged: bool) -> (i32, String, String) {
    use goh_tui::ERR;
    let seed = match estate_seed::load(root, KIND, staged) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let found = match sites(root, files, exclude, staged) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let report = Report {
        tag: TAG,
        item: "GOH.CLEANOPEN",
        what: (
            "bare open, which hands the app the caller's environment",
            "bare opens, which hand the app the caller's environment",
        ),
        fix: FIX,
        ok: "every app is launched with the login environment".to_string(),
    };
    estate_seed::verdict(root, staged, KIND, &seed, &found, &report)
}

/// `goh bare-open [--staged] [--exclude RE]`.
#[must_use]
pub fn run_command(staged: bool, exclude: &str) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        goh_tui::eoutln!("{} {TAG} not a git repository", goh_tui::ERR);
        return 2;
    };
    match crate::gitutil::listed_files(&root, staged) {
        Ok(files) => estate_seed::print(judge(&root, &files, exclude, staged)),
        Err(m) => {
            goh_tui::eoutln!("{} {TAG} {m}", goh_tui::ERR);
            2
        }
    }
}

/// The structural step, in every repo.
#[must_use]
pub fn step(
    repo: &Path,
    files: &[String],
    cfg: &crate::gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    estate_seed::step(
        LABEL,
        "crates/goh/src/bareopen.rs",
        judge(repo, files, &cfg.exclude, staged),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn todays_estate_sites_are_found() {
        for bad in [
            r#"    open -a "$DEST""#,
            r#"open "$APP""#,
            r#"  open "$DEST""#,
            r#"    open "$MENUBAR_APP""#,
            r#"        open "$BUNDLE_DIR""#,
            r#"[ "$was_running" = 1 ] && open -a "$DEST""#,
            r#"if x; then open "$APP"; fi"#,
            r#"/usr/bin/open -n "$APP""#,
            r#"( open "$APP" )"#,
            r#"exec open "$APP""#,
            "x || open -g Foo.app",
        ] {
            assert_eq!(scan_line(bad).len(), 1, "{bad}");
        }
    }

    #[test]
    fn a_word_that_is_not_the_command_passes() {
        for ok in [
            r#"info "launch:    open -a Divoom""#,
            "if [ -t 0 ] && command -v open &>/dev/null; then",
            "# open -a \"$DEST\"",
            "reopen \"$APP\"",
            "open_app \"$APP\"",
            "xdg-open \"$URL\"",
            "\"$GOH_DIR/bin/goh\" launch-app \"$APP\"",
            "echo opened",
            "f=$(gopen x)",
            "x.open y",
            r#"grep -qiE 'what is next|open work|the plan' <<<"$p" || continue"#,
            r#"die "export failed: open the trace in Instruments (open \"$T\")""#,
            r#"err "relaunch failed: open $DEST failed twice""#,
            r#"3) ok "installed (not running; open it once and it opens at login)" ;;"#,
        ] {
            assert!(scan_line(ok).is_empty(), "{ok}");
        }
    }

    #[test]
    fn a_heredoc_body_is_not_read_and_the_line_after_it_is() {
        let text = "osascript << APPLESCRIPT 2>/dev/null || true\n  tell application \"Finder\"\n    open\n  end tell\nAPPLESCRIPT\nopen \"$APP\"\ncat <<-'EOF'\nopen x\n\tEOF\nn=$((1<<2))\nopen y\n";
        assert_eq!(scan_text(text), vec![(6, "open \"$APP\""), (11, "open y")]);
    }
}
