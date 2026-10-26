//! A tracked file that RUNS a backgrounded busy loop or a busy-wait -- the house has ONE way to make load.
//!
//! THE CLASS (2026-10-21). The house had three ways to make load and one way to make a LEAK:
//! `tools/load.py` (registers its spinners, kills exactly those, and reaps itself), a launchd
//! backstop (kills REGISTERED pids whose parent is 1), and a Claude Code guard that refused a
//! Bash command spawning a backgrounded busy loop. And yet, at 13:00 on 2026-10-21, twenty-four
//! orphaned spinners were burning four cores each -- `/bin/zsh /private/var/.../opencode/load.sh`,
//! 78 minutes of CPU apiece, four and a half hours old, parent 1, their owner long dead. The
//! script was a hand-written FILE:
//!
//!     for i in $(seq 1 24); do (while :; do :; done) & done
//!
//! The guard could not see it (it judged COMMANDS, not the file that holds the loop), and no gate
//! looked at the files a repository tracks, so nothing said so at the commit that added it.
//!
//! THE RULE. A busy loop is a `while`/`until` whose body is nothing but `:`/`true`/`false` (or
//! empty): a loop that does no work, and therefore a spin. A loop that sleeps, waits or computes
//! is not a spinner. TEXT is not CODE: a loop inside a string, a heredoc body or a comment is
//! text, and `echo 'while :; do :; done'` writes a loop, it does not run one. A loop RUNS when
//! the line carrying it also carries a background operator: a foreground busy loop hangs the
//! shell that runs it (a different defect), while a backgrounded one outlives it, re-parented to
//! launchd, burning a core for hours.
//!
//! THE BUSY-WAIT (G17, 2026-10-26). The same class one step wider: a `while`/`until` of ANY
//! condition whose body does nothing (`until [ -f ready ]; do :; done`). It re-tests the condition
//! as fast as the CPU allows, pinning a core until the condition holds and hanging the shell, with
//! no orphan needed -- so it is a finding foreground or background, `read` drains excepted
//! (`while read -r l; do :; done < f` blocks on its input). G16 taught the Claude Code guard this
//! shape for commands; the guard's Write/Edit path and this check judge it too.
//!
//! ONE SHAPE, not two copies. The patterns live in `lib/busyloop_shapes.json`, which
//! `lib/busyloop.py` loads with Python's `re` and this module `include_str!`s and compiles with
//! fancy-regex (the busy-wait pattern needs a lookahead the plain `regex` crate refuses). A pattern
//! changed there changes the guard, this check and the backstop together
//! (`tests/test_check_busy_loop.py::test_the_shape_has_one_definition`).
//!
//! THE WAY. `python3 "$GOH_DIR/tools/load.py" N -- <command>` -- the one way to make load, which
//! registers the pids it starts, kills exactly those on the way out, refuses a second experiment
//! while one holds the slot, and leaves a registry a launchd backstop reads when even that cannot
//! run. A deliberate background busy loop says so on the line (or on the comment lines directly
//! above it): `# load-ok: <reason>`.
//!
//! THE SCOPE. The TRACKED set (`git ls-files`), or the INDEX at `--staged`, and among it the
//! shell sources (`crate::shellsrc`, the house's one definition of what a shell script is). The
//! scope is printed on every run, so a scan that stopped reading its subject reads like a clean
//! tree instead of one.
//!
//! THE FLOOR. `GOH_BUSY_LOOP_MIN_FILES`: a scan that read fewer shell sources than the floor is
//! RED, because a scan that stopped seeing its subject reports clean. Full scope only, like every
//! other floor (`crate::subject`).
//!
//! WIRING: a structural step in EVERY repo, a hard gate at both scopes. No path exemption: a
//! busy loop is not a style, so `GOH_EXCLUDE` does not reach it -- the one escape is the reasoned
//! per-line marker, like `goh secrets`'.

use fancy_regex::Regex;
use goh_tui::{ERR, OK};
use std::fmt::Write as _;
use std::path::Path;

/// The shapes, as `lib/busyloop_shapes.json` spells them: the ONE definition both languages read.
const SHAPES: &str = include_str!("../../../lib/busyloop_shapes.json");

/// The per-line escape: `# load-ok: <reason>`, on the line or on the comment lines above it.
pub const MARKER: &str = "load-ok:";

/// The house's way to make load, named in every refusal.
pub const THE_WAY: &str = r#"python3 "$GOH_DIR/tools/load.py" 8 -- <command>"#;

/// Why a backgrounded spinner is a finding.
const WHY_SPINNER: &str = "a backgrounded busy loop has nothing to reap it";

/// Why a busy-wait is a finding.
const WHY_WAIT: &str =
    "a busy-wait pins a core and hangs the shell: it re-tests its condition with no pause";

/// One finding: the line, and the line's text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    /// 1-based line.
    pub line: usize,
    /// The line's text, trimmed.
    pub text: String,
    /// Why this is a finding.
    pub why: &'static str,
}

/// The compiled rules.
pub struct Rules {
    loop_re: Regex,
    wait_re: Regex,
    read_re: Regex,
}

impl Rules {
    /// # Errors
    /// The shared shape file does not parse, or a pattern in it does not compile (a bug).
    pub fn new() -> Result<Self, String> {
        let shapes: serde_json::Value = serde_json::from_str(SHAPES)
            .map_err(|e| format!("lib/busyloop_shapes.json does not parse: {e}"))?;
        let compile = |key: &str| -> Result<Regex, String> {
            let pattern = shapes
                .get(key)
                .and_then(serde_json::Value::as_str)
                .ok_or_else(|| format!("lib/busyloop_shapes.json has no `{key}` pattern"))?;
            Regex::new(pattern).map_err(|e| format!("busy-loop pattern {key} {pattern:?}: {e}"))
        };
        Ok(Self {
            loop_re: compile("busy_loop")?,
            wait_re: compile("busy_wait")?,
            read_re: compile("read_condition")?,
        })
    }
}

/// Is there a background operator in this text?
///
/// `&&` is a conjunction and `>&` is a redirect: neither starts a background job, and both are
/// common enough that reading them as one would refuse ordinary commands.
fn backgrounded(text: &str) -> bool {
    let bytes: Vec<char> = text.chars().collect();
    for index in 0..bytes.len() {
        if bytes[index] != '&' {
            continue;
        }
        let before = if index == 0 { ' ' } else { bytes[index - 1] };
        let after = bytes.get(index + 1).copied().unwrap_or(' ');
        if before == '&' || before == '>' || after == '&' {
            continue;
        }
        return true;
    }
    false
}

/// The line carrying `offset` in `text`: `(start, end)`.
fn line_of(text: &str, offset: usize) -> (usize, usize) {
    let start = text[..offset].rfind('\n').map_or(0, |i| i + 1);
    let end = text[offset..].find('\n').map_or(text.len(), |i| offset + i);
    (start, end)
}

/// The 1-based line of `offset` in the masked text (the mask keeps every newline).
fn number_of(masked: &str, offset: usize) -> usize {
    masked[..offset].matches('\n').count() + 1
}

/// The text's CODE: strings filled, comments and heredoc bodies blanked, through the house's own
/// shell mask (`earlypipe::lex::mask_top`), so a loop inside any of them is text and is not judged.
fn code_of(text: &str) -> String {
    let (masked, _top) = crate::earlypipe::lex::mask_top(text);
    masked.into_iter().collect()
}

/// The busy loops this text RUNS: in code, and backgrounded.
#[must_use]
pub fn spinning(text: &str, rules: &Rules) -> Vec<Finding> {
    let masked = code_of(text);
    let mut out = Vec::new();
    for found in rules.loop_re.find_iter(&masked).flatten() {
        let (start, end) = line_of(&masked, found.start());
        if !backgrounded(&masked[start..end]) {
            continue;
        }
        let number = number_of(&masked, found.start());
        let raw = text.lines().nth(number - 1).unwrap_or("").trim().to_owned();
        out.push(Finding {
            line: number,
            text: raw,
            why: WHY_SPINNER,
        });
    }
    out
}

/// The busy-waits this text RUNS (G17): in code, of any condition, foreground or background.
/// A `read` condition drains its input rather than spinning and is not one.
#[must_use]
pub fn waiting(text: &str, rules: &Rules) -> Vec<Finding> {
    let masked = code_of(text);
    let mut out = Vec::new();
    for caps in rules.wait_re.captures_iter(&masked).flatten() {
        let (Some(whole), Some(cond)) = (caps.get(0), caps.name("cond")) else {
            continue;
        };
        if rules.read_re.is_match(cond.as_str()).unwrap_or(false) {
            continue;
        }
        let number = number_of(&masked, whole.start());
        let raw = text.lines().nth(number - 1).unwrap_or("").trim().to_owned();
        out.push(Finding {
            line: number,
            text: raw,
            why: WHY_WAIT,
        });
    }
    out
}

/// The markers this text carries: `(line, reason)` for each `# load-ok:` comment.
///
/// A marker is a COMMENT: the text before the `#` is not part of it, so a line that merely
/// mentions `load-ok:` in code is not an escape.
#[must_use]
pub fn markers(text: &str) -> Vec<(usize, String)> {
    let mut out = Vec::new();
    for (index, line) in text.lines().enumerate() {
        let Some(hash) = line.find('#') else {
            continue;
        };
        let Some(at) = line[hash..].find(MARKER) else {
            continue;
        };
        let start = hash + at + MARKER.len();
        out.push((index + 1, line[start..].trim().to_owned()));
    }
    out
}

/// The reason on this line's marker: `None` when the line has none, `Some("")` when the marker
/// has no reason (which exempts nothing).
fn marker_reason(line: &str) -> Option<String> {
    let hash = line.find('#')?;
    let at = line[hash..].find(MARKER)?;
    Some(line[hash + at + MARKER.len()..].trim().to_owned())
}

/// The marker that excuses the finding at `line`: its line, or the comment lines directly above.
///
/// `Some(marker's line)` when a marker with a reason covers the finding, `None` when none does.
/// The house's convention for a per-line escape (`goh secrets`, `goh moving-tags`).
#[must_use]
pub fn excusing_marker(text: &str, line: usize) -> Option<usize> {
    let lines: Vec<&str> = text.lines().collect();
    let own = lines.get(line.wrapping_sub(1)).copied().unwrap_or("");
    if let Some(reason) = marker_reason(own) {
        return if reason.is_empty() { None } else { Some(line) };
    }
    // The comment lines DIRECTLY above the finding: a marker further up, past a line of code, is
    // a marker about something else.
    let mut index = line;
    while index >= 2 {
        let current = lines[index - 2].trim();
        if !current.starts_with('#') {
            return None;
        }
        if let Some(reason) = marker_reason(current) {
            return if reason.is_empty() {
                None
            } else {
                Some(index - 1)
            };
        }
        index -= 1;
    }
    None
}

/// Every finding in one shell source: the loops it runs, and the markers that excuse nothing.
///
/// A line carrying both shapes (`while :; do :; done &` is both) is ONE finding, the spinner's.
#[must_use]
pub fn findings(text: &str, rules: &Rules) -> Vec<Finding> {
    let mut out = Vec::new();
    let mut loops = spinning(text, rules);
    for wait in waiting(text, rules) {
        if !loops.iter().any(|found| found.line == wait.line) {
            loops.push(wait);
        }
    }
    let mut used: Vec<usize> = Vec::new();
    for finding in &loops {
        match excusing_marker(text, finding.line) {
            Some(marker_line) => used.push(marker_line),
            None => out.push(finding.clone()),
        }
    }
    // A marker with no reason exempts nothing, and a marker nothing needs is a marker that will
    // outlive the defect it was written for: both are findings, so a stale escape cannot hide
    // behind the file it was written in.
    for (line, reason) in markers(text) {
        let text_of_line = text.lines().nth(line - 1).unwrap_or("").trim().to_owned();
        if reason.is_empty() {
            out.push(Finding {
                line,
                text: text_of_line,
                why: "`load-ok` with no reason exempts nothing",
            });
            continue;
        }
        if !used.contains(&line) {
            out.push(Finding {
                line,
                text: text_of_line,
                why:
                    "`load-ok` excuses nothing: no backgrounded busy loop or busy-wait is under it",
            });
        }
    }
    out.sort_by_key(|finding| finding.line);
    out
}

/// The findings over every shell source among `files`.
#[must_use]
pub fn scan(
    root: &Path,
    files: &[String],
    exclude: Option<&crate::pathfilter::PathFilter>,
    staged: bool,
) -> (Vec<(String, Finding)>, usize) {
    let Ok(rules) = Rules::new() else {
        return (Vec::new(), 0);
    };
    let sources = crate::shellsrc::sources(root, files, exclude, staged);
    let mut out = Vec::new();
    for (rel, text) in &sources {
        for finding in findings(text, &rules) {
            out.push((rel.clone(), finding));
        }
    }
    (out, sources.len())
}

/// The report: every finding, and the way forward.
#[must_use]
pub fn format_report(hits: &[(String, Finding)], read: usize, staged: bool) -> String {
    let scope = if staged { "staged" } else { "tracked" };
    let mut out = String::new();
    let _ = writeln!(
        out,
        "    {scope} files only: {read} shell source(s) read, {} finding(s):",
        hits.len()
    );
    for (rel, finding) in hits {
        let _ = writeln!(out, "    {rel}:{}: {}", finding.line, finding.text);
        let _ = writeln!(out, "      -- {}", finding.why);
    }
    let _ = writeln!(
        out,
        "    A backgrounded busy loop has nothing to reap it: when the shell that owns it is\n    \
         killed, the spinner is re-parented to launchd and keeps burning a core for hours -- 24 of\n    \
         them for 3h12m at a load average of 206, then 12 for 26m, then 8 for 28m inside the gates\n    \
         themselves, three times in one day, each blamed on \"other agents\". Make the load the\n    \
         house way, which reaps itself: {THE_WAY} -- it registers the pids it starts, kills\n    \
         exactly those on the way out, refuses a second experiment while one holds the slot, and a\n    \
         launchd backstop kills any orphan that outlives its owner."
    );
    let _ = writeln!(
        out,
        "    A busy-wait (`until <cond>; do :; done`) re-tests its condition as fast as the CPU allows, so\n    \
         it pins a core until the condition holds -- forever, if it never does -- and hangs the shell\n    \
         meanwhile. Wait the house way: a loop that sleeps (`until <cond>; do sleep 2; done`)."
    );
    let _ = writeln!(
        out,
        "    A deliberate busy loop or busy-wait says so on the line (or on the comment lines directly\n    \
         above it): `# load-ok: <reason>`."
    );
    out
}

/// The pass line: the scope, printed, so a scan that read nothing says so.
#[must_use]
pub fn format_ok(read: usize, staged: bool) -> String {
    let scope = if staged { "staged" } else { "tracked" };
    format!("no busy loop or busy-wait in the {scope} files ({read} shell source(s) read)")
}

/// `goh busy-loop`: the check on its own, over the tracked files or the index.
#[must_use]
pub fn run(root: &Path, exclude: Option<&crate::pathfilter::PathFilter>, staged: bool) -> i32 {
    let files = match crate::gitutil::listed_files(root, staged) {
        Ok(files) => files,
        Err(message) => {
            goh_tui::eoutln!("{ERR} [busy_loop] {message}");
            return 2;
        }
    };
    if files.is_empty() {
        goh_tui::eoutln!("{ERR} [busy_loop] no files to judge -- nothing was read");
        return 2;
    }
    let (hits, read) = scan(root, &files, exclude, staged);
    crate::subject::record(&crate::subject::BUSY_LOOP, read);
    if hits.is_empty() {
        goh_tui::outln!("{OK} [busy_loop] {}", format_ok(read, staged));
        return 0;
    }
    goh_tui::out(&format_report(&hits, read, staged));
    1
}

/// `goh busy-loop [--staged]`: the check on its own, over the tracked files or the index.
///
/// No path exclusion: a busy loop is not a style, it is a leak, and the one escape is the
/// reasoned per-line marker (`# load-ok: <reason>`), like `goh secrets`.
#[must_use]
pub fn run_command(staged: bool) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        goh_tui::eoutln!("{ERR} [busy_loop] not a git repository");
        return 2;
    };
    run(&root, None, staged)
}

/// The structural step: the same check, over the files the pipeline already listed.
#[must_use]
pub fn step(repo: &Path, files: &[String], staged: bool) -> Option<i32> {
    const LABEL: &str = "no busy loop or busy-wait in a tracked file";
    let start = crate::step_report::begin(LABEL);
    let (hits, read) = scan(repo, files, None, staged);
    crate::subject::record(&crate::subject::BUSY_LOOP, read);
    if hits.is_empty() {
        crate::step_report::ok(LABEL, start);
        return None;
    }
    Some(crate::step_report::fail(
        LABEL,
        "native",
        &format_report(&hits, read, staged),
        start,
    ))
}
