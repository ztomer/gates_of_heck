//! Rule 5: a tracked file that cites a roadmap id the roadmap no longer holds (GOH.ROADMAPIDS).
//!
//! A pruned entry leaves its citations behind: a workflow saying it closes a roadmap id, a code
//! comment citing a backlog number, each pointing at nothing once the entry went to git and the
//! CHANGELOG. A private Rust app's own test caught six in one pass (a glob that never read
//! `.github/`, and an upper-case-only match); this is that check for every repo.
//!
//! A CITATION is the word `roadmap`, or the declared plan file's stem (`BACKLOG`,
//! `FEATURE_PLAN`), in any case and optionally as the file name (`ROADMAP.md`), followed on the
//! same line by an id (`roadmap` then `item` or `entry` is read too). An id is what
//! the roadmap's own entries open with -- a capital, letters and digits with a digit or a dot
//! somewhere, or a dotted number -- so `the roadmap Rule` and `ROADMAP Phase` cite nothing.
//!
//! An id the roadmap HOLDS is one that opens a heading, a list item, a table cell or a bold
//! paragraph anywhere in it (a ruling `R1` counts, not only a phase's entries). The history is not
//! read: the CHANGELOG and its fragments cite the ids they closed, which is what history is for;
//! nor is vendored code, a binary, the roadmap itself or its seed. A line that says
//! `roadmap-ok: <reason>` is not read either: a fixture quoting a dead id on purpose.
//!
//! Judged from its own grace's end, [`OVER_FROM`]: until then a repo sees the sites as a warning
//! and seeds them, its `<file>#dead-id` key rising against HEAD's seed for that week only.

use regex::Regex;
use std::collections::BTreeSet;
use std::sync::LazyLock;

/// The escape a line carries, with its reason.
static JUSTIFIED: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"roadmap-ok:\s*[^\s>-]").unwrap_or_else(|e| unreachable!("a constant: {e}"))
});

/// 2026-12-01T00:00:00Z: a week for the estate to fix or seed what the rule finds.
pub const OVER_FROM: u64 = 1_796_083_200;
/// The seed key's suffix.
pub const KEY: &str = "dead-id";

/// Directories whose files are another project's, or history.
const SKIPPED_DIRS: [&str; 5] = [
    "vendor",
    "third_party",
    "node_modules",
    "changelog",
    "changes",
];

/// Is the rule past its grace?
#[must_use]
pub fn over() -> bool {
    crate::grace::over_from(OVER_FROM)
}

/// The words a citation opens with, for `roadmap` at `rel`: `roadmap`, and the plan file's stem.
#[must_use]
pub fn words(roadmap: &str) -> Vec<String> {
    let name = roadmap.rsplit('/').next().unwrap_or(roadmap);
    let stem = name
        .rsplit_once('.')
        .map_or(name, |(s, _)| s)
        .to_ascii_lowercase();
    let mut w = vec!["roadmap".to_owned()];
    if !stem.is_empty() && stem != "roadmap" {
        w.push(stem);
    }
    w
}

/// Is `rel` read for citations? Not the roadmap, its seed, the history or another project's code.
#[must_use]
pub fn read_here(rel: &str, roadmap: &str) -> bool {
    if rel == roadmap || rel == super::SEED_FILE {
        return false;
    }
    let mut parts: Vec<&str> = rel.split('/').collect();
    let name = parts.pop().unwrap_or_default().to_ascii_lowercase();
    if name.starts_with("changelog") {
        return false;
    }
    !parts
        .iter()
        .any(|d| SKIPPED_DIRS.contains(&d.to_ascii_lowercase().as_str()))
}

/// The citation pattern for these words.
#[must_use]
pub fn pattern(words: &[String]) -> Regex {
    let alt = words
        .iter()
        .map(|w| regex::escape(w))
        .collect::<Vec<_>>()
        .join("|");
    let src = format!(
        r"(?i:\b(?:{alt})\b)(?:\.md)?`?[ \t]+(?:(?i:item|entry)[ \t]+)?`?([A-Z][A-Za-z0-9]*(?:[.\-][A-Za-z0-9]+)*|\d+(?:\.\d+)+)\b"
    );
    Regex::new(&src).unwrap_or_else(|e| unreachable!("escaped words compile: {e}"))
}

/// Does `id` look like a roadmap id: a digit or a dot somewhere?
fn id_like(id: &str) -> bool {
    id.contains('.') || id.bytes().any(|b| b.is_ascii_digit())
}

/// Every citation in `text`: (1-based line, the words cited, the id).
#[must_use]
pub fn citations(text: &str, re: &Regex) -> Vec<(usize, String, String)> {
    let mut out = Vec::new();
    for (i, line) in text.lines().enumerate() {
        if JUSTIFIED.is_match(line) {
            continue;
        }
        for c in re.captures_iter(line) {
            // group 0 is the match and group 1 is not optional in `pattern`: both always exist
            let (all, id) = (&c[0], &c[1]);
            if id_like(id) {
                let said = all.replace('`', "");
                let said = said.split_whitespace().collect::<Vec<_>>().join(" ");
                out.push((i + 1, said, id.to_owned()));
            }
        }
    }
    out
}

/// The ids a roadmap HOLDS: every id opening a heading, list item, table cell or bold paragraph.
#[must_use]
pub fn held(text: &str) -> BTreeSet<String> {
    super::scan::opening_ids(text)
}

/// The text files whose content holds any of `words`, any case, as `git grep -I` lists them.
///
/// The index at `staged`, else the worktree with its untracked, unignored files (the tree
/// `crate::gitutil::listed_files` reads at full). No match is an empty list; a git that fails is
/// an error.
///
/// # Errors
///
/// As `crate::gitutil::listed_files`: a failing git is loud, never an empty list.
pub fn grep_files(
    root: &std::path::Path,
    words: &[&str],
    staged: bool,
) -> Result<Vec<String>, String> {
    grep_with(std::process::Command::new("git"), root, words, staged)
}

/// [`grep_files`] with the command that runs git given: a test hands one that cannot start.
///
/// # Errors
///
/// As [`grep_files`], and when `git` cannot be started.
pub fn grep_with(
    mut git: std::process::Command,
    root: &std::path::Path,
    words: &[&str],
    staged: bool,
) -> Result<Vec<String>, String> {
    let mut args = vec!["grep", "-z", "-l", "-I", "-i", "-F"];
    args.push(if staged { "--cached" } else { "--untracked" });
    for w in words {
        args.extend(["-e", w]);
    }
    let out = git
        .arg("-C")
        .arg(root)
        .args(&args)
        .env("GIT_OPTIONAL_LOCKS", "0")
        .output()
        .map_err(|e| format!("git grep failed: {e}"))?;
    // exit 1 with nothing said is git grep's "nothing matched"
    if out.status.code() == Some(1) && out.stderr.is_empty() {
        return Ok(Vec::new());
    }
    if !out.status.success() {
        return Err(format!(
            "git grep failed (exit {}): {}",
            out.status.code().unwrap_or(-1),
            String::from_utf8_lossy(&out.stderr)
                .trim()
                .chars()
                .take(200)
                .collect::<String>()
        ));
    }
    Ok(out
        .stdout
        .split(|b| *b == 0)
        .filter(|chunk| !chunk.is_empty())
        .map(|chunk| String::from_utf8_lossy(chunk).into_owned())
        .collect())
}
