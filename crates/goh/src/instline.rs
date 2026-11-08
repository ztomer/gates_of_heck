//! No doc keeps the installed version by hand (`goh installed-version`, GOH.INSTLINE).
//!
//! A line saying which version is INSTALLED is true the day it is written and false at the next
//! release: koffee's `FEATURE_PLAN` said v2.12.1 build 36 while the Mac ran 2.13.0 build 40
//! (KB.HYG3, `koffee_big` `ff18e06`), and three more repos carried a "Shipped and installed: vX"
//! line. What a machine runs is read from the machine; a release is recorded by its tag. The rule
//! was koffee's citation gate's; it lives here so every repo runs ONE copy of it.
//!
//! The subject is every tracked Markdown file but DATED HISTORY. A CHANGELOG or a decision record
//! is history -- "the installed v0.43.1 bundle" there is evidence of that day, and stays true -- so
//! it is not read: a file whose name has a `CHANGELOG`, `HISTORY`, `DECISIONS` or `NEWS` word
//! (split on `_`, `-` and `.`), a numbered record (`D-0248-...`, `ADR-0003-...`), or anything under a
//! `decisions/`, `adr/`, `changelog/`, `releases/` or `release-notes/` directory. Until GOH.VENDOR
//! (2026-11-08) it read only PLANS (a `ROADMAP`, `PLAN`, `BACKLOG` or `TODO` word), while koffee's
//! own copy read every `.md`: a README or AGENTS.md that states what is installed rots exactly as a
//! plan does, and over the 44-repo estate that day every hit outside a plan was in a CHANGELOG or a
//! `docs/decisions/D-*` record -- so the wider subject turned no repo red, and koffee's copy went.
//! The count read is floored (`GOH_INSTALLED_VERSION_MIN_FILES`, `crate::subject`). A structural
//! step in every repo, at both scopes.

use goh_tui::{ERR, OK};
use regex::Regex;
use std::fmt::Write as _;
use std::path::Path;
use std::sync::LazyLock;

const TAG: &str = "[installed_version]";
const HISTORY_WORDS: [&str; 4] = ["changelog", "history", "decisions", "news"];
const HISTORY_DIRS: [&str; 5] = ["decisions", "adr", "changelog", "releases", "release-notes"];

/// koffee's rule, verbatim: `installed` within 20 characters (no sentence end) of a version.
static INSTALLED: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\binstalled\b[^.\n]{0,20}\b(?:v\d+\.\d+|build \d+|versionCode \d+)")
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});

/// A tracked Markdown file that is not dated history: the subject.
#[must_use]
pub fn is_subject(rel: &str) -> bool {
    let lower = rel.to_ascii_lowercase();
    let mut parts: Vec<&str> = lower.split('/').collect();
    let name = parts.pop().unwrap_or_default();
    let Some(stem) = name.strip_suffix(".md") else {
        return false;
    };
    if parts.iter().any(|d| HISTORY_DIRS.contains(d)) {
        return false;
    }
    let words: Vec<&str> = stem.split(['_', '-', '.']).collect();
    if words.iter().any(|w| HISTORY_WORDS.contains(w)) {
        return false;
    }
    // A numbered record: `D-0248-...`, `ADR-0003-...`.
    let numbered = matches!(words.as_slice(), [p, n, ..]
        if (*p == "d" || *p == "adr") && !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()));
    !numbered
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
    let mut docs = 0usize;
    let mut listing = String::new();
    let mut n = 0usize;
    for rel in files.iter().filter(|r| is_subject(r)) {
        let Some(blob) = crate::gitutil::content_bytes(root, rel, staged) else {
            continue;
        };
        docs += 1;
        for line in hits(&String::from_utf8_lossy(&blob)) {
            n += 1;
            let _ = writeln!(listing, "    {rel}:{line}: states an installed version");
        }
    }
    if !staged {
        crate::subject::record(&crate::subject::INSTALLED_VERSION, docs);
    }
    if n == 0 {
        let ok =
            format!("{OK} {TAG} OK — {docs} {scope} Markdown file(s) keep no installed version\n");
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
        "no doc keeps the installed version by hand (staged)"
    } else {
        "no doc keeps the installed version by hand"
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
    use super::{hits, is_subject};

    #[test]
    fn every_doc_but_dated_history_is_the_subject() {
        for p in [
            "ROADMAP.md",
            "README.md",
            "AGENTS.md",
            "docs/EXPLANATION.md",
            "x/backlog.md",
            "docs/D-notes.md",
        ] {
            assert!(is_subject(p), "{p}");
        }
        for p in [
            "CHANGELOG.md",
            "HISTORY.md",
            "DECISIONS.md",
            "docs/decisions/anything.md",
            "docs/adr/0003-signing.md",
            "release-notes/v1.2.md",
            "docs/D-0248-local-installs.md",
            "ADR-0003.md",
            "ROADMAP.txt",
        ] {
            assert!(!is_subject(p), "{p}");
        }
    }

    #[test]
    fn koffees_rule_reads_a_version_near_installed_only() {
        let text = "**Installed:** v2.12.1 build 36\nthe installed app reads its plist\n\
                    installed. Then v1.2\n- Shipped and installed: v1.6.0\n";
        assert_eq!(hits(text), vec![1, 4]);
    }
}
