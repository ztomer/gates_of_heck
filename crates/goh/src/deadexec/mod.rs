//! No code after `exec` (`goh dead-after-exec`).
//!
//! An `exec` that names a command replaces the shell, so a statement after it in the same block
//! never runs -- and nothing says so. `app_updates`' tools/gate.sh ran
//! `exec python3 tools/check_roadmap.py --self-test` and then `exec python3
//! tools/check_roadmap.py` in one case arm: the second never ran, so the full gate never judged
//! ROADMAP.md, only its self-test fixtures -- a gate that could not fail (fixed in 5a4b33c).
//! shellcheck 0.11.0 exits 0 on that arm and on a top-level `exec echo a; echo b` (measured
//! 2026-10-08), so this is a defect no linter in the gate could see.
//!
//! What is dead, and what is not, is `dead.rs`; how a source becomes statements is `parse.rs`.
//! Scope: every shell source (`crate::shellsrc`), the same set `goh early-exit-pipe` reads.
//!
//! WIRING: a structural step in EVERY repo, and a hard gate at both scopes -- not a ratchet. Dead
//! code after `exec` has one fix (delete it, or drop the first `exec`), it is rare, and the estate
//! sweep that preceded this check found too few sites to justify a migration period.

pub mod dead;
pub mod parse;

use std::fmt::Write as _;
use std::path::Path;

pub use dead::scan_text;

const TAG: &str = "[no_code_after_exec]";

/// A statement no path reaches: an `exec` before it replaced the shell.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    /// 1-based line of the dead statement.
    pub line: usize,
    /// The dead statement as written, whitespace collapsed.
    pub text: String,
    /// 1-based line of the `exec` that replaced the shell.
    pub exec_line: usize,
    /// That `exec`, as written.
    pub exec: String,
}

fn clip(s: &str) -> String {
    let mut out: String = s.chars().take(80).collect();
    if out.len() < s.len() {
        out.push_str("...");
    }
    out
}

/// The verdict over `files`: `(exit code, stdout, stderr)`.
fn judge(root: &Path, files: &[String], exclude: &str, staged: bool) -> (i32, String, String) {
    let exclude = match crate::steps::compile_exclude(exclude) {
        Ok(x) => x,
        Err(m) => return (2, String::new(), format!("✗ {TAG} {m}\n")),
    };
    let sources = crate::shellsrc::sources(root, files, exclude.as_ref(), staged);
    let scope = if staged { "staged" } else { "tracked" };
    let mut listing = String::new();
    let mut n = 0usize;
    for (rel, text) in &sources {
        for f in scan_text(text) {
            n += 1;
            let _ = writeln!(
                listing,
                "    {rel}:{}: {}  -- unreachable: `{}` at line {} replaced the shell",
                f.line,
                clip(&f.text),
                clip(&f.exec),
                f.exec_line
            );
        }
    }
    if n == 0 {
        let ok = format!(
            "✓ {TAG} OK — {} {scope} shell source(s), no code after exec\n",
            sources.len()
        );
        return (0, ok, String::new());
    }
    let mut err =
        format!("✗ {TAG} {n} statement(s) after an exec that replaced the shell ({scope}):\n");
    err.push_str(&listing);
    err.push_str(
        "    Nothing after an `exec CMD` runs in its block. Delete the dead line, or drop the\n    \
         `exec` before it if both were meant to run. (`exec >log` redirections, `exec CMD ||\n    \
         fallback`, and a bare `exit` after the exec are not findings.)\n",
    );
    (1, String::new(), err)
}

/// `goh dead-after-exec [--staged] [--exclude RE]`.
#[must_use]
pub fn run_command(staged: bool, exclude: &str) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        eprintln!("✗ {TAG} not a git repository");
        return 2;
    };
    let files = match crate::gitutil::listed_files(&root, staged) {
        Ok(f) => f,
        Err(m) => {
            eprintln!("✗ {TAG} {m}");
            return 2;
        }
    };
    let (code, out, err) = judge(&root, &files, exclude, staged);
    print!("{out}");
    eprint!("{err}");
    code
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
        "no code after exec (staged)"
    } else {
        "no code after exec"
    };
    let start = crate::step_report::begin(label);
    match judge(repo, files, &cfg.exclude, staged) {
        (0, _, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, _, err) => {
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported("crates/goh/src/deadexec/mod.rs"),
                &err,
                start,
            );
            Some(code)
        }
    }
}

#[cfg(test)]
mod tests;
