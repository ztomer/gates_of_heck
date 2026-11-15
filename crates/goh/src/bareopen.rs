//! A bare `open` hands the launched app the CALLER's environment (`goh bare-open`, GOH.CLEANOPEN).
//!
//! macOS `open` passes its own environment to the application it launches, and the app outlives
//! the script (PPID 1). Probed 2026-11-13 with a throwaway app that writes its environ to a file,
//! launched with a sentinel set in the caller: `open -a`, `open -n -a`, `open --env`,
//! `osascript ... launch` and `launchctl asuser` all carried the sentinel and the caller's
//! `GOH_DIR`; only `env -i /usr/bin/open` (and the Finder) gave launchd's login environment. So
//! a private Swift app's installer, run by a ship, started the app with `GOH_SHIP_ITEM` and a
//! `GOH_DIR` naming a transient gate export with no `bin/`, and every app it relaunched
//! (`Claude.app` among them) handed it to every agent of that session. The one launch is `goh
//! launch-app PATH` (`crate::launchapp`), which clears the environment first.
//!
//! GOH.BAREOPEN2 widened the scan, from `crate::grace::OVER_FROM` (2026-11-23; before it the scan reads
//! as the last release did, so a consumer is not red on shapes no seed names yet): an `open` behind `env FOO=1` or `FOO=1` (an `env -i` is the clean
//! launch), an `open` in a `$( )` or backticks inside double quotes, and a Python subprocess that
//! runs `open` (`crate::bareopen_py`).
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

#[path = "bareopen_legacy.rs"]
mod legacy;

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
     a ship's GOH_DIR (a transient gate export) and GOH_SHIP_ITEM reached a private Swift app and every app it\n    \
     relaunched (GOH.CLEANOPEN). Launch with launchd's login environment instead:\n      \
     \"$GOH_DIR/bin/goh\" launch-app \"$APP\"                 (foreground)\n      \
     \"$GOH_DIR/bin/goh\" launch-app --background \"$APP\"    (open -g)\n      \
     \"$GOH_DIR/bin/goh\" launch-app -b com.example.App     (open -b)\n      \
     from tui.launch import launch_app                   (Python, PYTHONPATH=$GOH_DIR)\n";

/// The words after which the next word is a command.
pub(crate) const LEADERS: [&str; 7] = ["then", "do", "else", "exec", "nohup", "time", "sudo"];

/// The command-position `open`s in one line: the byte offset of each.
#[must_use]
pub fn scan_line(line: &str) -> Vec<usize> {
    scan_line_in(line, true)
}

/// `scan_line` as of the last release when `shapes` is false (`crate::grace`): no `env`/assignment
/// prefix, and a `$( )` inside double quotes still a string.
#[must_use]
pub fn scan_line_in(line: &str, shapes: bool) -> Vec<usize> {
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
        if if shapes {
            quoted(&line[..start])
        } else {
            legacy::quoted(&line[..start])
        } {
            continue;
        }
        let before = line[..start].trim_end();
        if line[..start].ends_with(|c: char| c.is_alphanumeric() || "_-./$\"'".contains(c)) {
            continue;
        }
        let leads = if shapes {
            carries_caller_env(before)
        } else {
            legacy::leads(before)
        };
        if leads {
            out.push(start);
        }
    }
    out
}

/// Whether the words in front of an `open` make it a command that hands the app THIS shell's
/// environment: it starts a command (after a separator or a leader word), optionally behind
/// variable assignments (`FOO=1 open`) or an `env` that sets variables and clears nothing
/// (`env FOO=1 open`; GOH.BAREOPEN2). `env -i` / `env --ignore-environment` is the clean launch,
/// the very thing `goh launch-app` does, so it is not a finding.
fn carries_caller_env(before: &str) -> bool {
    let seg = before
        .rfind([';', '&', '|', '(', '{', '`', '!'])
        .map_or(before, |i| &before[i + 1..]);
    let words = shell_words(seg);
    let mut words = words.iter().map(String::as_str).peekable();
    while words.next_if(|w| LEADERS.contains(w)).is_some() {}
    let rest: Vec<&str> = words.collect();
    let Some((first, args)) = rest.split_first() else {
        return true;
    };
    if first.rsplit('/').next() != Some("env") {
        return rest.iter().all(|w| is_assignment(w));
    }
    let mut args = args.iter();
    while let Some(w) = args.next() {
        if env_clears(w) {
            return false;
        }
        if env_takes_value(w) {
            args.next();
        } else if !(is_short_opt(w) || w.starts_with("--") || is_assignment(w)) {
            return false;
        }
    }
    true
}

/// An `env` option that clears the environment: the clean launch (`goh launch-app`'s mechanism).
pub(crate) fn env_clears(w: &str) -> bool {
    w == "--ignore-environment" || (is_short_opt(w) && w.contains('i')) || w == "-"
}

/// An `env` option whose value is the next word.
pub(crate) fn env_takes_value(w: &str) -> bool {
    matches!(w, "-u" | "-C" | "-S" | "-P" | "--unset" | "--chdir")
}

pub(crate) fn is_short_opt(w: &str) -> bool {
    w.len() > 1 && w.starts_with('-') && !w.starts_with("--")
}

/// `NAME=value`, the shell's assignment word.
pub(crate) fn is_assignment(w: &str) -> bool {
    w.split_once('=').is_some_and(|(name, _)| {
        name.starts_with(|c: char| c.is_alphabetic() || c == '_')
            && name.chars().all(|c| c.is_alphanumeric() || c == '_')
    })
}

/// `seg` split at blanks that sit outside quotes (`FOO="a b"` is one word).
fn shell_words(seg: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    for c in seg.chars() {
        cur.push(c);
        if c.is_whitespace() && !quoted(&cur) {
            cur.pop();
            if !cur.is_empty() {
                out.push(std::mem::take(&mut cur));
            }
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

/// What a shell reads the next character as.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Ctx {
    Single,
    Double,
    /// Inside `$( )` (its paren depth); code again, even within double quotes.
    Sub(usize),
    /// Inside backticks; code again.
    Tick,
}

/// Whether the end of `head` sits inside a quoted string (`'...'` or `"..."`, `\\` escapes outside
/// single quotes): a word there is an argument, never the command. A `$( )` or a pair of backticks
/// inside double quotes is code again (GOH.BAREOPEN2: `x="$(open -a Foo)"` launches the app).
fn quoted(head: &str) -> bool {
    let mut stack: Vec<Ctx> = Vec::new();
    let mut chars = head.chars().peekable();
    while let Some(c) = chars.next() {
        match stack.last().copied() {
            Some(Ctx::Single) => {
                if c == '\'' {
                    stack.pop();
                }
                continue;
            }
            Some(Ctx::Double) if c == '"' => {
                stack.pop();
                continue;
            }
            Some(Ctx::Double) => {
                match c {
                    '\\' => {
                        chars.next();
                    }
                    '$' if chars.next_if_eq(&'(').is_some() => stack.push(Ctx::Sub(0)),
                    '`' => stack.push(Ctx::Tick),
                    _ => {}
                }
                continue;
            }
            Some(Ctx::Tick) if c == '`' => {
                stack.pop();
                continue;
            }
            Some(Ctx::Sub(d)) if c == ')' => {
                stack.pop();
                if d > 0 {
                    stack.push(Ctx::Sub(d - 1));
                }
                continue;
            }
            Some(Ctx::Sub(d)) if c == '(' => {
                stack.pop();
                stack.push(Ctx::Sub(d + 1));
                continue;
            }
            _ => {}
        }
        match c {
            '\\' => {
                chars.next();
            }
            '\'' => stack.push(Ctx::Single),
            '"' => stack.push(Ctx::Double),
            _ => {}
        }
    }
    matches!(stack.last(), Some(Ctx::Single | Ctx::Double))
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
    scan_text_in(text, crate::grace::over())
}

/// `scan_text` with the new shapes on or off (`crate::grace`).
#[must_use]
pub fn scan_text_in(text: &str, shapes: bool) -> Vec<(usize, &str)> {
    let mut out = Vec::new();
    let mut until: Option<String> = None;
    for (n, line) in text.lines().enumerate() {
        if let Some(end) = &until {
            if line.trim() == end {
                until = None;
            }
            continue;
        }
        if !scan_line_in(line, shapes).is_empty() {
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
    let pysources = crate::bareopen_py::sources(root, files, exclude.as_ref(), staged);
    crate::subject::record(&crate::subject::BARE_OPEN, sources.len() + pysources.len());
    let mut out = Sites::new();
    // Read either way (the floor counts them), judged from `crate::grace::OVER_FROM`.
    for (rel, text) in pysources.iter().filter(|_| crate::grace::over()) {
        let rows: Vec<&str> = text.lines().collect();
        let found: Vec<String> = crate::bareopen_py::scan_text(text)
            .unwrap_or_default()
            .into_iter()
            .map(|n| {
                format!(
                    "    {rel}:{n}: {}  -- the app gets this process's environment",
                    estate_seed::clip(rows.get(n - 1).copied().unwrap_or(""))
                )
            })
            .collect();
        if !found.is_empty() {
            out.insert(rel.clone(), found);
        }
    }
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

    /// GOH.BAREOPEN2: the shapes the first scan walked past, one plant each.
    #[test]
    fn the_shapes_the_first_scan_missed_are_found() {
        for bad in [
            // an `env` that sets variables (and clears nothing) hands the app the rest
            r#"env FOO=1 open -a "$APP""#,
            r#"  env FOO=1 BAR=2 /usr/bin/open "$APP""#,
            r#"sudo env X=1 open "$APP""#,
            r#"x=$(env -u HOME open "$APP")"#,
            // a variable assignment before the command is the same launch
            r#"FOO=1 open -a "$APP""#,
            // a command substitution inside a double-quoted string is code
            r#"out="$(open -a "$APP")""#,
            r#"echo "started: $(open "$APP")""#,
            r#"out="`open -a Foo`""#,
            r#"echo "$(echo hi | open -f)""#,
        ] {
            assert_eq!(scan_line(bad).len(), 1, "{bad}");
        }
    }

    #[test]
    fn an_env_that_clears_the_environment_is_the_clean_launch() {
        for ok in [
            r#"env -i /usr/bin/open -a "$APP""#,
            r#"env -i HOME="$HOME" open "$APP""#,
            r#"env --ignore-environment open "$APP""#,
            r#"x="$(env -i open "$APP")""#,
            r#"echo "run env FOO=1 open -a X to launch""#,
            r#"x="$(echo open)""#,
            r#"msg="the file (open it) and $(date)""#,
        ] {
            assert!(scan_line(ok).is_empty(), "{ok}");
        }
    }

    #[test]
    fn within_the_grace_the_scan_reads_as_the_last_release_did() {
        for old in [
            r#"open "$APP""#,
            "x || open -g Foo.app",
            r#"( open "$APP" )"#,
        ] {
            assert_eq!(scan_line_in(old, false).len(), 1, "{old}");
        }
        for new in [
            r#"env FOO=1 open -a "$APP""#,
            r#"FOO=1 open -a "$APP""#,
            r#"out="$(open -a "$APP")""#,
        ] {
            assert_eq!(scan_line_in(new, false).len(), 0, "{new}");
            assert_eq!(scan_line_in(new, true).len(), 1, "{new}");
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
