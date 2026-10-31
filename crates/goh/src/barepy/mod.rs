//! No bare `python3` in a hook or a gate script (BACKLOG 6.2).
//!
//! A bare `python3` in `.githooks/*` or `tools/gate.sh` is whichever interpreter Homebrew linked
//! last, run with whatever `PYTHONPATH` the committing shell exported. Homebrew moved `python3` to
//! 3.15 on 2026-10-10 and three repos' gates broke three ways at once (a lost `spacy`, a moved
//! coverage hook, `No module named 'tui'` unless `PYTHONPATH` was set) -- none naming the
//! interpreter. `gates/python.sh` is the one way a hook runs Python: the repo's pinned version,
//! read back, with the house libraries on its path. This step refuses the other way.
//!
//! What is judged: an interpreter NAME at command position -- `python`, `python3`, `python3.N` --
//! read off the masked text (`earlypipe::lex::mask`: comments and heredoc bodies blank, strings
//! filled, but `$( )` inside double quotes still code). A path (`.venv/bin/python`,
//! `/usr/bin/python3`) is not ambient and not a finding; nor is the name as an argument
//! (`command -v python3`, `need python3 "..."`).
//!
//! Widened per repo by `GOH_BARE_PYTHON_SCOPE` (a path regex, `docs/config.md`): a repo's local CI
//! and build scripts are the same class, and only the repo knows which they are.
//!
//! WIRING: a structural step in every repo, a hard gate at both scopes. The fix is mechanical
//! (`python3 X` becomes `bash "$GOH/gates/python.sh" X`), and the class broke three repos in a day.

use goh_tui::{ERR, OK};
use std::fmt::Write as _;
use std::path::Path;

const TAG: &str = "[no_bare_python]";

/// Words after which the next word is a command.
const LEADERS: [&str; 12] = [
    "then", "do", "else", "elif", "if", "while", "until", "exec", "command", "time", "nohup", "!",
];

/// Is `rel` a hook or a gate script -- the files a commit or a push runs?
#[must_use]
pub fn in_scope(rel: &str) -> bool {
    rel == "tools/gate.sh"
        || (rel.starts_with(".githooks/") && !rel.starts_with(".githooks/.goh-installed/"))
}

fn is_interpreter(word: &str) -> bool {
    let Some(rest) = word.strip_prefix("python") else {
        return false;
    };
    rest.is_empty()
        || rest == "3"
        || rest
            .strip_prefix("3.")
            .is_some_and(|n| !n.is_empty() && n.chars().all(|c| c.is_ascii_digit()))
}

fn word_char(c: char) -> bool {
    c.is_alphanumeric() || matches!(c, '_' | '.' | '-' | '/' | '$' | '=' | '!')
}

/// Is the word ending just before `i` (blanks skipped) a place a command may start after?
fn command_position(m: &[char], mut i: usize) -> bool {
    loop {
        while i > 0 && matches!(m[i - 1], ' ' | '\t') {
            i -= 1;
        }
        if i == 0 {
            return true;
        }
        match m[i - 1] {
            '\n' | ';' | '|' | '&' | '(' | ')' | '`' | '{' => return true,
            c if !word_char(c) => return false,
            _ => {}
        }
        let end = i;
        while i > 0 && word_char(m[i - 1]) {
            i -= 1;
        }
        let word: String = m[i..end].iter().collect();
        // `env python3`, `/usr/bin/env python3`: the ambient interpreter, found on PATH all the same.
        if LEADERS.contains(&word.as_str()) || word == "env" || word.ends_with("/env") {
            return true;
        }
        // An assignment prefix (`X=y python3 ...`): the command is still to come.
        let assignment = word.split_once('=').is_some_and(|(name, _)| {
            !name.is_empty() && name.chars().all(|c| c.is_alphanumeric() || c == '_')
        });
        if !assignment {
            return false;
        }
    }
}

/// `(1-based line, the interpreter name)` of each bare interpreter at command position in `src`.
#[must_use]
pub fn scan_text(src: &str) -> Vec<(usize, String)> {
    let m = crate::earlypipe::lex::mask(src);
    let mut out = Vec::new();
    let mut i = 0usize;
    while i < m.len() {
        if !word_char(m[i]) || (i > 0 && word_char(m[i - 1])) {
            i += 1;
            continue;
        }
        let start = i;
        while i < m.len() && word_char(m[i]) {
            i += 1;
        }
        let word: String = m[start..i].iter().collect();
        if is_interpreter(&word) && command_position(&m, start) {
            let line = 1 + m[..start].iter().filter(|c| **c == '\n').count();
            out.push((line, word));
        }
    }
    out
}

/// The verdict over `files`: `(exit code, stdout, stderr)`.
///
/// `scope` is the repo's `GOH_BARE_PYTHON_SCOPE`: a regex on repo-relative paths ADDED to the
/// hooks and the gate. Local CI and build scripts are the same class as a hook -- a person or a
/// gate runs them, and a bare `python3` there is Homebrew's last link all the same -- but which
/// of a repo's scripts those are is the repo's to say, so the default stays the files every repo
/// has. `GOH_EXCLUDE` still wins: a vendored script is not re-styled because a scope names it.
fn judge(
    root: &Path,
    files: &[String],
    exclude: &str,
    scope: &str,
    staged: bool,
) -> (i32, String, String) {
    let exclude = match crate::steps::compile_exclude(exclude) {
        Ok(x) => x,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let widened = if scope.is_empty() {
        None
    } else {
        match crate::pathfilter::PathFilter::new(scope) {
            Ok(x) => Some(x),
            Err(e) => {
                let m = format!("bad GOH_BARE_PYTHON_SCOPE regex '{scope}': {e}");
                return (2, String::new(), format!("{ERR} {TAG} {m}\n"));
            }
        }
    };
    let hooks: Vec<String> = files
        .iter()
        .filter(|f| in_scope(f) || widened.as_ref().is_some_and(|w| w.is_match(f)))
        .cloned()
        .collect();
    let sources = crate::shellsrc::sources(root, &hooks, exclude.as_ref(), staged);
    crate::subject::record(&crate::subject::BARE_PYTHON, sources.len());
    let scope = if staged { "staged" } else { "tracked" };
    let mut listing = String::new();
    let mut n = 0usize;
    for (rel, text) in &sources {
        for (line, name) in scan_text(text) {
            n += 1;
            let _ = writeln!(listing, "    {rel}:{line}: {name}");
        }
    }
    if n == 0 {
        let ok = format!(
            "{OK} {TAG} OK — {} {scope} hook/gate script(s), no bare python\n",
            sources.len()
        );
        return (0, ok, String::new());
    }
    let mut err =
        format!("{ERR} {TAG} {n} bare python interpreter(s) in a hook or gate script ({scope}):\n");
    err.push_str(&listing);
    err.push_str(
        "    A bare `python3` is whichever interpreter Homebrew linked last, with the committing\n    \
         shell's PYTHONPATH. Run it through the house seam, which runs the repo's pinned version\n    \
         (GOH_PYTHON_VERSION or a committed .python-version) with the house libraries on its path:\n    \
         \x20   bash \"$GOH/gates/python.sh\" tools/x.py ...\n",
    );
    (1, String::new(), err)
}

/// The structural step, in every repo.
#[must_use]
pub fn step(
    repo: &Path,
    files: &[String],
    cfg: &crate::gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    let label = if staged {
        "no bare python in a hook or gate (staged)"
    } else {
        "no bare python in a hook or gate"
    };
    let start = crate::step_report::begin(label);
    match judge(repo, files, &cfg.exclude, &cfg.bare_python_scope, staged) {
        (0, _, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, _, err) => {
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported("crates/goh/src/barepy/mod.rs"),
                &err,
                start,
            );
            Some(code)
        }
    }
}

#[cfg(test)]
mod tests;
