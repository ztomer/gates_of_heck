//! A tracked file the repo's own `.gitignore` matches: the rules and the tree disagree.
//!
//! One of the two is wrong. Either the rule is stale (`koffee_big` listed `build.sh` and `run.sh`
//! as "local only" while AGENTS.md documented both as the way to build) or the file should never
//! have been committed (a Kotlin daemon crash log under an ignored build directory, found the
//! same day, 2026-10-08). Git never reports either: a tracked path is not ignored, so
//! `git status` is clean and `git check-ignore` says nothing without `--no-index`.
//!
//! Only the in-tree `.gitignore` files are rules here. `.git/info/exclude` and
//! `core.excludesFile` are one machine's, and a verdict that changes with the machine that runs
//! it is not a gate. A file that is meant to be tracked says so where the rule is, `!<path>`.
//!
//! At `--staged` the step judges what the commit brings: the files it adds or changes, or every
//! tracked file when it stages a `.gitignore` (a new rule can ignore a file committed long ago).
//! A disagreement already committed is the full gate's: a pre-commit red on it would block
//! every unrelated commit until someone fixed it.

use std::collections::BTreeSet;
use std::path::Path;
use std::process::Command;

use crate::step_report;

const LABEL: &str = "no tracked file its .gitignore ignores";
const SOURCE: &str = "crates/goh/src/structural/trackedignored.rs";
const RULES_FILE: &str = ".gitignore";

/// One tracked file and the rule that matches it.
struct Hit {
    path: String,
    source: String,
    line: String,
    pattern: String,
}

/// `git <args>` with NUL-separated output. `ok_codes` are the exits that are answers rather than
/// failures (`check-ignore` exits 1 for "nothing matched").
fn git_z(
    repo: &Path,
    args: &[&str],
    stdin: Option<&[u8]>,
    ok_codes: &[i32],
) -> Result<Vec<String>, String> {
    use std::io::Write as _;
    let mut child = Command::new("git")
        .args(args)
        .current_dir(repo)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("git {}: {e}", args.join(" ")))?;
    if let Some(mut pipe) = child.stdin.take() {
        pipe.write_all(stdin.unwrap_or_default())
            .map_err(|e| format!("git {}: {e}", args.join(" ")))?;
    }
    let out = child
        .wait_with_output()
        .map_err(|e| format!("git {}: {e}", args.join(" ")))?;
    if !out
        .status
        .code()
        .is_some_and(|code| ok_codes.contains(&code))
    {
        return Err(format!(
            "git {} failed: {}",
            args.join(" "),
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(out
        .stdout
        .split(|b| *b == 0)
        .filter(|field| !field.is_empty())
        .map(|field| String::from_utf8_lossy(field).into_owned())
        .collect())
}

/// Every tracked path an in-tree `.gitignore` matches.
fn tracked_ignored(repo: &Path) -> Result<Vec<String>, String> {
    let per_directory = format!("--exclude-per-directory={RULES_FILE}");
    git_z(
        repo,
        &["ls-files", "-z", "--cached", "--ignored", &per_directory],
        None,
        &[0],
    )
}

/// The rule matching each path: `check-ignore -v`, which must be told to look past the index
/// (`--no-index`) because a tracked path is never ignored otherwise.
fn rules_for(repo: &Path, paths: &[String]) -> Result<Vec<Hit>, String> {
    let input: Vec<u8> = paths.iter().flat_map(|p| p.bytes().chain([0])).collect();
    let fields = git_z(
        repo,
        &["check-ignore", "-v", "-z", "--no-index", "--stdin"],
        Some(&input),
        &[0, 1],
    )?;
    let mut hits: Vec<Hit> = fields
        .chunks_exact(4)
        .map(|f| Hit {
            source: f[0].clone(),
            line: f[1].clone(),
            pattern: f[2].clone(),
            path: f[3].clone(),
        })
        .collect();
    // A path the listing found and `check-ignore` did not name is still a finding: dropping it
    // would undercount, and a smaller report reads as a cleaner tree.
    let named: BTreeSet<String> = hits.iter().map(|h| h.path.clone()).collect();
    for path in paths.iter().filter(|p| !named.contains(*p)) {
        hits.push(Hit {
            path: path.clone(),
            source: RULES_FILE.to_owned(),
            line: "?".to_owned(),
            pattern: "?".to_owned(),
        });
    }
    Ok(hits)
}

/// The negation that keeps `path` tracked, written relative to the `.gitignore` it goes in.
fn negation(hit: &Hit) -> String {
    let dir = hit.source.strip_suffix(RULES_FILE).unwrap_or("");
    format!("!{}", hit.path.strip_prefix(dir).unwrap_or(&hit.path))
}

fn report(hits: &[Hit]) -> String {
    use std::fmt::Write as _;
    let mut text = format!(
        "✗ [tracked_ignored] {} tracked file(s) match this repo's own .gitignore -- the rules and \
         the tree disagree, and one of them is wrong. A file that should not be committed: \
         `git rm --cached -- <path>`. A file that should: say so under its rule with `!<path>`.\n",
        hits.len()
    );
    for hit in hits {
        let _ = writeln!(
            text,
            "  {}  matched by {}:{} `{}`  (keep it: {})",
            hit.path,
            hit.source,
            hit.line,
            hit.pattern,
            negation(hit)
        );
    }
    text
}

/// The paths this run judges: every tracked one, or at `--staged` what the commit brings.
fn in_scope(listed: Vec<String>, files: &[String], staged: bool) -> Vec<String> {
    let stages_rules = files
        .iter()
        .any(|f| Path::new(f).file_name().is_some_and(|n| n == RULES_FILE));
    if !staged || stages_rules {
        return listed;
    }
    let staged_set: BTreeSet<&String> = files.iter().collect();
    listed
        .into_iter()
        .filter(|p| staged_set.contains(p))
        .collect()
}

/// The structural step. `files` is the pipeline's shared enumeration: the worktree in full mode,
/// the staged additions and changes at `--staged`.
#[must_use]
pub fn step(repo: &Path, files: &[String], staged: bool) -> Option<i32> {
    let start = step_report::begin(LABEL);
    let how = step_report::ported(SOURCE);
    if !staged && files.is_empty() {
        let text = "✗ [tracked_ignored] nothing to check (no files) -- refusing to report clean over zero files\n";
        return Some(step_report::fail(LABEL, &how, text, start));
    }
    let judged = match tracked_ignored(repo).map(|listed| in_scope(listed, files, staged)) {
        Ok(paths) => paths,
        Err(message) => {
            return Some(step_report::fail(
                LABEL,
                &how,
                &format!("{message}\n"),
                start,
            ))
        }
    };
    if judged.is_empty() {
        step_report::ok(LABEL, start);
        return None;
    }
    match rules_for(repo, &judged) {
        Ok(hits) => Some(step_report::fail(LABEL, &how, &report(&hits), start)),
        Err(message) => Some(step_report::fail(
            LABEL,
            &how,
            &format!("{message}\n"),
            start,
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::{in_scope, negation, Hit};

    fn hit(path: &str, source: &str) -> Hit {
        Hit {
            path: path.to_owned(),
            source: source.to_owned(),
            line: "1".to_owned(),
            pattern: "*".to_owned(),
        }
    }

    #[test]
    fn a_negation_is_relative_to_its_rules_file() {
        assert_eq!(negation(&hit("AGENTS.md", ".gitignore")), "!AGENTS.md");
        assert_eq!(negation(&hit("sub/a b.txt", "sub/.gitignore")), "!a b.txt");
    }

    #[test]
    fn staged_scope_is_the_commit_unless_it_stages_rules() {
        let listed = || vec!["old.md".to_owned(), "new.md".to_owned()];
        let new = ["new.md".to_owned()];
        assert_eq!(in_scope(listed(), &new, true), ["new.md"]);
        let with_rules = ["sub/.gitignore".to_owned()];
        assert_eq!(in_scope(listed(), &with_rules, true), listed());
        assert_eq!(in_scope(listed(), &[], false), listed());
    }
}
