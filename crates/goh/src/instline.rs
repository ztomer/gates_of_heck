//! No plan keeps the installed version by hand (`goh installed-version`, GOH.INSTLINE).
//!
//! A line saying which version is INSTALLED is true the day it is written and false at the next
//! release: koffee's `FEATURE_PLAN` said v2.12.1 build 36 while the Mac ran 2.13.0 build 40
//! (KB.HYG3, `koffee_big` `ff18e06`), and three more repos carried a "Shipped and installed: vX"
//! line. What a machine runs is read from the machine; a release is recorded by its tag. The rule
//! was koffee's citation gate's; it lives here so every repo runs ONE copy of it.
//!
//! The subject is a PLAN: a tracked Markdown file whose name has a `ROADMAP`, `PLAN`, `BACKLOG` or
//! `TODO` word (split on `_`, `-` and `.`, so `EXPLANATION.md` is not one). A CHANGELOG or a
//! decision record is dated history -- "the installed v0.43.1 bundle" there is evidence of that
//! day, and stays true -- so it is not read. A structural step in every repo, at both scopes.

use goh_tui::{ERR, OK};
use regex::Regex;
use std::fmt::Write as _;
use std::path::Path;
use std::sync::LazyLock;

const TAG: &str = "[installed_version]";
const PLAN_WORDS: [&str; 4] = ["roadmap", "plan", "backlog", "todo"];

/// koffee's rule, verbatim: `installed` within 20 characters (no sentence end) of a version.
static INSTALLED: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\binstalled\b[^.\n]{0,20}\b(?:v\d+\.\d+|build \d+|versionCode \d+)")
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});

/// A tracked Markdown file named as a plan.
#[must_use]
pub fn is_plan(rel: &str) -> bool {
    let name = rel.rsplit('/').next().unwrap_or(rel).to_ascii_lowercase();
    let Some(stem) = name.strip_suffix(".md") else {
        return false;
    };
    stem.split(['_', '-', '.']).any(|w| PLAN_WORDS.contains(&w))
}

/// The 1-based numbers of the lines of `text` that state an installed version.
#[must_use]
pub fn hits(text: &str) -> Vec<usize> {
    text.lines()
        .enumerate()
        .filter(|(_, l)| INSTALLED.is_match(l))
        .map(|(i, _)| i + 1)
        .collect()
}

/// `(exit code, stdout, stderr)` over `files`.
fn judge(root: &Path, files: &[String], staged: bool) -> (i32, String, String) {
    let scope = if staged { "staged" } else { "tracked" };
    let mut plans = 0usize;
    let mut listing = String::new();
    let mut n = 0usize;
    for rel in files.iter().filter(|r| is_plan(r)) {
        let Some(blob) = crate::gitutil::content_bytes(root, rel, staged) else {
            continue;
        };
        plans += 1;
        for line in hits(&String::from_utf8_lossy(&blob)) {
            n += 1;
            let _ = writeln!(listing, "    {rel}:{line}: states an installed version");
        }
    }
    if n == 0 {
        let ok =
            format!("{OK} {TAG} OK — {plans} {scope} plan file(s) keep no installed version\n");
        return (0, ok, String::new());
    }
    let mut err = format!("{ERR} {TAG} {n} hand-kept installed version line(s) ({scope}):\n");
    err.push_str(&listing);
    err.push_str(
        "    A line saying what is installed rots at the next release. Read it from the machine\n    \
         (`--version`, the app's Info.plist, `adb shell dumpsys package`) and record releases by\n    \
         their tags: delete the line, or point at the releases page.\n",
    );
    (1, String::new(), err)
}

/// `goh installed-version [--staged]`.
#[must_use]
pub fn run_command(staged: bool) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        goh_tui::eoutln!("{ERR} {TAG} not a git repository");
        return 2;
    };
    let files = match crate::gitutil::listed_files(&root, staged) {
        Ok(f) => f,
        Err(m) => {
            goh_tui::eoutln!("{ERR} {TAG} {m}");
            return 2;
        }
    };
    let (code, out, err) = judge(&root, &files, staged);
    goh_tui::out(&out);
    goh_tui::eout(&err);
    code
}

/// The structural step, in every repo.
#[must_use]
pub fn step(repo: &Path, files: &[String], staged: bool) -> Option<i32> {
    let label = if staged {
        "no plan keeps the installed version by hand (staged)"
    } else {
        "no plan keeps the installed version by hand"
    };
    let start = crate::step_report::begin(label);
    match judge(repo, files, staged) {
        (0, _, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, _, err) => {
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported("crates/goh/src/instline.rs"),
                &err,
                start,
            );
            Some(code)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{hits, is_plan};

    #[test]
    fn plans_are_named_by_a_whole_word() {
        for p in [
            "ROADMAP.md",
            "docs/ROADMAP.md",
            "FEATURE_PLAN.md",
            "x/backlog.md",
            "TODO.md",
        ] {
            assert!(is_plan(p), "{p}");
        }
        for p in [
            "EXPLANATION.md",
            "CHANGELOG.md",
            "ROADMAP.txt",
            "docs/D-0248.md",
        ] {
            assert!(!is_plan(p), "{p}");
        }
    }

    #[test]
    fn koffees_rule_reads_a_version_near_installed_only() {
        let text = "**Installed:** v2.12.1 build 36\nthe installed app reads its plist\n\
                    installed. Then v1.2\n- Shipped and installed: v1.6.0\n";
        assert_eq!(hits(text), vec![1, 4]);
    }
}
