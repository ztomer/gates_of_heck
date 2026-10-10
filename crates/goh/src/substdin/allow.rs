//! The ratchet allowlist for `goh subprocess-stdin`: `subprocess_stdin_allow.json` at the
//! repo root, and `subprocess_stdin_allow.d/*.json` beside it.
//!
//! THIS IS A RATCHET, NOT A SUPPRESSION LIST, and the difference is the whole design. It
//! was seeded on 2026-10-08 with every violation the estate had that day -- 511 in this
//! repo alone -- because landing the gate un-seeded turns every repo's pre-commit red at
//! once, and a gate that goes red in twenty places on the day it lands is a gate that gets
//! disabled (the reason `GOH_CLAIM_DERIVATION` is opt-in). A ratchet goes the other way
//! from then on: an entry is one somebody can DELETE as they fix their repo, and the gate
//! says so out loud until they do.
//!
//! SO WHY DOES A STALE ENTRY FAIL? Because the failure mode an allowlist exists to prevent
//! is not "a violation someone approved"; it is permission nobody re-earns. An entry that
//! outlives its finding is a blank cheque that keeps passing while the code it described is
//! gone -- and the commonest way that happens is invisible: the call is fixed, the entry
//! stays, and a future edit re-introduces the defect with a clean gate. Three ways an entry
//! goes stale, all three refused here:
//!
//!   * the finding it named is GONE (the call was fixed or deleted) -- the commonest, and
//!     the one the sweep is driving towards on purpose;
//!   * its FILE is gone (a rename, a delete) -- judged at every scope, because an entry for
//!     a path that does not exist judges nothing;
//!   * its `line` text no longer matches, which is what makes reflowing a call revoke it.
//!
//! AND WHY IS THE KEY THE LINE'S TEXT, NOT ITS NUMBER? A line-number key punishes every
//! unrelated edit above it: three lines added to a function and a correct exemption starts
//! failing, which teaches a repo to distrust the gate. Keying on the call's own text (with
//! whitespace collapsed, so `ruff format` cannot revoke a judgement) survives a move and
//! still dies when the call is deleted. `kill_by_name_allow.json` keys the same way.
//!
//! ONE ENTRY PERMITS EXACTLY ONE CALL SITE. A second identical call needs a second entry,
//! and that is deliberate: a repeated violation is two things to fix, and an allowlist
//! entry that silently covers a call site its author never saw is the "permission nobody
//! re-earns" defect wearing a different hat.

use std::fmt::Write as _;
use std::path::Path;

/// The single-file form, at the repo root.
pub const ALLOW_FILE: &str = "subprocess_stdin_allow.json";

/// The sharded form, beside it.
///
/// 511 seeded entries do not fit under `GOH_MAX_LINES=500`, and the cap is a hard rule
/// ("split, never exempt"), so a repo with more debt than one file may hold splits rather
/// than grows past it. A repo with a handful of entries uses the single file and never
/// creates the directory.
pub const ALLOW_DIR: &str = "subprocess_stdin_allow.d";

/// The permitted `status` values.
///
/// `unreviewed` is what a seeded entry carries: found when the gate was seeded, not yet
/// decided by a person. The passing report counts them, so the debt stays visible rather
/// than filed.
pub const STATUSES: [&str; 2] = ["legitimate", "unreviewed"];

/// One exemption.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    /// Repo-relative path the call is in.
    pub path: String,
    /// The callee as the scanner resolved it, e.g. `subprocess.run`.
    pub call: String,
    /// The call's own source line, whitespace-insensitive.
    pub line: String,
    /// `unreviewed` counts as debt in the passing report.
    pub unreviewed: bool,
    /// Which allowlist file carried it, so a stale entry names its own home.
    pub origin: String,
}

/// Whitespace-collapsed, so a `ruff format` cannot revoke an exemption.
#[must_use]
pub fn key(line: &str) -> String {
    line.split_whitespace().collect()
}

fn nonempty(value: Option<&serde_json::Value>) -> Option<String> {
    value
        .and_then(serde_json::Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
}

/// Parse one allowlist file's `{"entries": [...]}` into `(entries, problems)`.
fn parse(text: &str, origin: &str) -> (Vec<Entry>, Vec<String>) {
    let bad = |why: String| (Vec::new(), vec![format!("{origin}: {why}")]);
    let data: serde_json::Value = match serde_json::from_str(text) {
        Ok(v) => v,
        Err(e) => return bad(format!("is not valid JSON: {e}")),
    };
    let Some(items) = data.get("entries").and_then(serde_json::Value::as_array) else {
        return bad("must be {\"entries\": [...]}".to_owned());
    };
    let mut entries = Vec::new();
    let mut problems = Vec::new();
    for (i, e) in items.iter().enumerate() {
        let fields = (
            nonempty(e.get("path")),
            nonempty(e.get("call")),
            nonempty(e.get("line")),
            nonempty(e.get("reason")),
        );
        let status = e.get("status").and_then(serde_json::Value::as_str);
        if !status.is_none_or(|s| STATUSES.contains(&s)) {
            problems.push(format!(
                "{origin}: entry {i} ({}) has status {status:?}; it must be one of {}",
                fields.0.as_deref().unwrap_or("<no path>"),
                STATUSES.join(", ")
            ));
        }
        let (Some(path), Some(call), Some(line), Some(_)) = fields else {
            problems.push(format!(
                "{origin}: entry {i} needs a non-empty path, call, line and reason"
            ));
            continue;
        };
        entries.push(Entry {
            path,
            call,
            line,
            unreviewed: status.is_none_or(|s| s == "unreviewed"),
            origin: origin.to_owned(),
        });
    }
    (entries, problems)
}

/// Every allowlist file that exists, single file first then shards in name order, so a
/// violation's message and the seeded order are both reproducible.
fn sources(root: &Path) -> Vec<std::path::PathBuf> {
    let mut out = Vec::new();
    let single = root.join(ALLOW_FILE);
    if single.is_file() {
        out.push(single);
    }
    if let Ok(dir) = std::fs::read_dir(root.join(ALLOW_DIR)) {
        let mut shards: Vec<std::path::PathBuf> = dir
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|p| {
                p.extension().is_some_and(|x| x == "json")
                    && p.file_name()
                        .is_some_and(|n| !n.to_string_lossy().starts_with('.'))
            })
            .collect();
        shards.sort();
        out.extend(shards);
    }
    out
}

/// Load every allowlist file at `root`, as `(entries, problems)`.
///
/// A file that cannot be read is a PROBLEM, never an empty list: an allowlist that fails to
/// parse and then reports nothing is a gate that turned itself off.
#[must_use]
pub fn load(root: &Path) -> (Vec<Entry>, Vec<String>) {
    let mut entries = Vec::new();
    let mut problems = Vec::new();
    for path in sources(root) {
        let rel = path
            .strip_prefix(root)
            .unwrap_or(&path)
            .display()
            .to_string();
        match std::fs::read_to_string(&path) {
            Err(e) => problems.push(format!("{rel}: unreadable: {e}")),
            Ok(text) => {
                let (mut here, mut trouble) = parse(&text, &rel);
                entries.append(&mut here);
                problems.append(&mut trouble);
            }
        }
    }
    (entries, problems)
}

/// One finding that has not been exempted: `(repo-relative path, index into the findings)`.
pub type Unallowed = (String, usize);

/// Split `findings` (repo-relative path, in source order) into the exempted and the left,
/// and find the entries nothing used.
///
/// ONE ENTRY, ONE CALL SITE: the search skips entries an earlier hit already consumed, so
/// two identical calls need two entries.
///
/// AND WHAT COUNTS AS STALE. An unused entry is stale when the file it names was IN THIS
/// RUN'S SCOPE -- that is the commit that fixed the call while the exemption stayed, and
/// refusing it there is the whole point. It is also stale when the file does not exist at
/// all, which is judged at EVERY scope, because an entry for a deleted file judges nothing
/// anywhere. It is deliberately NOT stale when the file exists but is out of this run's
/// scope: at `--staged` that is every unstaged file, and a pre-commit that demanded the
/// whole allowlist be current would redden on every commit that touched one helper.
#[must_use]
pub fn partition(
    findings: &[(String, crate::substdin::calls::Finding)],
    entries: &[Entry],
    listed: &[String],
    root: &Path,
) -> (Vec<Unallowed>, usize, Vec<usize>) {
    let mut used = vec![false; entries.len()];
    let mut left = Vec::new();
    let mut allowed = 0usize;
    for (n, (path, finding)) in findings.iter().enumerate() {
        let hit = entries.iter().enumerate().position(|(i, e)| {
            !used[i]
                && e.path == *path
                && e.call == finding.call
                && key(&e.line) == key(&finding.text)
        });
        match hit {
            Some(i) => {
                used[i] = true;
                allowed += 1;
            }
            None => left.push((path.clone(), n)),
        }
    }
    let stale: Vec<usize> = entries
        .iter()
        .enumerate()
        .filter(|(i, e)| !used[*i] && stale_now(root, &e.path, listed))
        .map(|(i, _)| i)
        .collect();
    (left, allowed, stale)
}

/// Is an unused entry stale? See [`partition`] for the two cases and why the third is not.
fn stale_now(root: &Path, path: &str, listed: &[String]) -> bool {
    listed.iter().any(|f| f == path) || !root.join(path).exists()
}

/// The stale-entry report block: `path: call -- line`, naming the file that carried it.
#[must_use]
pub fn describe_stale(entries: &[Entry], stale: &[usize]) -> String {
    let mut s = String::new();
    for i in stale {
        let e = &entries[*i];
        let _ = writeln!(s, "    {}: {} -- {}", e.path, e.call, e.line);
        let _ = writeln!(s, "      ({}, unreviewed)", e.origin);
    }
    s
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::substdin::calls::{Finding, Why};

    fn finding(call: &str, text: &str) -> Finding {
        Finding {
            line: 2,
            call: call.to_owned(),
            why: Why::Inherits,
            text: text.to_owned(),
        }
    }

    fn entry(path: &str, call: &str, line: &str) -> Entry {
        Entry {
            path: path.to_owned(),
            call: call.to_owned(),
            line: line.to_owned(),
            unreviewed: true,
            origin: ALLOW_FILE.to_owned(),
        }
    }

    /// A root with `a.py` on disk, so an entry naming it is never stale for a vanished
    /// file -- only for a vanished finding, which is the case under test.
    fn tree() -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap_or_else(|e| panic!("tempdir: {e}"));
        std::fs::write(dir.path().join("a.py"), "x = 1\n").unwrap_or_else(|e| panic!("{e}"));
        dir
    }

    fn listed() -> Vec<String> {
        vec!["a.py".to_owned()]
    }

    #[test]
    fn one_entry_permits_exactly_one_call_site() {
        let dir = tree();
        let found = vec![
            ("a.py".to_owned(), finding("subprocess.run", "run()")),
            ("a.py".to_owned(), finding("subprocess.run", "run()")),
        ];
        // Same text, two call sites, ONE entry: the second is left, because an entry that
        // silently covered a site nobody saw is the defect this gate exists to prevent.
        let (left, allowed, stale) = partition(
            &found,
            &[entry("a.py", "subprocess.run", "run()")],
            &listed(),
            dir.path(),
        );
        assert_eq!(allowed, 1);
        assert_eq!(left.len(), 1);
        assert_eq!(left[0].1, 1);
        assert_eq!(stale.len(), 0);
        let (left, allowed, stale) = partition(
            &found,
            &[
                entry("a.py", "subprocess.run", "run()"),
                entry("a.py", "subprocess.run", "run()"),
            ],
            &listed(),
            dir.path(),
        );
        assert_eq!((allowed, left.len()), (2, 0), "{stale:?}");
    }

    #[test]
    fn whitespace_does_not_revoke_an_exemption_and_a_different_call_does() {
        let dir = tree();
        let found = vec![(
            "a.py".to_owned(),
            finding("subprocess.run", "p = subprocess.run(  x )"),
        )];
        let allowed = |call: &str, line: &str| {
            partition(&found, &[entry("a.py", call, line)], &listed(), dir.path()).1
        };
        assert_eq!(allowed("subprocess.run", "p = subprocess.run( x )"), 1);
        assert_eq!(allowed("subprocess.Popen", "p = subprocess.run( x )"), 0);
        assert_eq!(allowed("subprocess.run", "p = subprocess.run(y)"), 0);
    }

    #[test]
    fn a_finding_that_is_gone_leaves_its_entry_stale() {
        let dir = tree();
        let (left, allowed, stale) = partition(
            &[],
            &[entry("a.py", "subprocess.run", "run()")],
            &listed(),
            dir.path(),
        );
        assert_eq!(left.len(), 0);
        assert_eq!(allowed, 0);
        assert_eq!(stale, [0], "the call was fixed and the exemption stayed");
    }

    #[test]
    fn an_entry_for_a_file_that_does_not_exist_is_stale() {
        let dir = tree();
        let (left, allowed, stale) = partition(
            &[],
            &[entry("gone.py", "subprocess.run", "run()")],
            &listed(),
            dir.path(),
        );
        assert_eq!(left.len(), 0);
        assert_eq!(allowed, 0);
        assert_eq!(
            stale,
            [0],
            "an entry for a vanished file judges nothing at any scope"
        );
    }

    #[test]
    fn an_unused_entry_is_stale_when_its_file_was_in_scope_or_is_gone() {
        let dir = tree();
        // In scope, so "the commit fixed the call and the exemption stayed" is caught...
        assert!(stale_now(dir.path(), "a.py", &listed()));
        // ...and an entry naming a file that does not exist is caught at EVERY scope.
        assert!(stale_now(dir.path(), "gone.py", &listed()));
        assert!(stale_now(dir.path(), "gone.py", &[]));
        // Out of scope but present -- every unstaged file at --staged -- is NOT stale, or a
        // pre-commit would demand the whole allowlist be current on every commit.
        std::fs::write(dir.path().join("other.py"), "x = 1\n").unwrap_or_else(|e| panic!("{e}"));
        assert!(!stale_now(dir.path(), "other.py", &listed()));
    }

    #[test]
    fn a_malformed_allowlist_is_a_problem_not_an_empty_one() {
        let bad = [
            ("not json", "is not valid JSON"),
            ("{}", "must be"),
            ("{\"entries\": [{}]}", "needs a non-empty path"),
            (
                "{\"entries\": [{\"path\": \"a.py\", \"call\": \"c\", \"line\": \"l\", \
                 \"reason\": \"r\", \"status\": \"maybe\"}]}",
                "has status",
            ),
        ];
        for (text, want) in bad {
            let (_entries, problems) = parse(text, ALLOW_FILE);
            let joined = problems.join("; ");
            assert!(joined.contains(want), "{text} -> {joined}");
        }
        // A shape error admits no entry at all: a half-read entry that matched nothing
        // would be reported as STALE, naming a defect the author never wrote.
        for (text, _) in bad.iter().take(3) {
            assert_eq!(parse(text, ALLOW_FILE).0.len(), 0, "{text}");
        }
        // A missing `status` is legal and counts as debt, not as a problem.
        let (entries, problems) = parse(
            "{\"entries\": [{\"path\": \"a.py\", \"call\": \"c\", \"line\": \"l\", \
             \"reason\": \"r\"}]}",
            ALLOW_FILE,
        );
        assert!(problems.is_empty(), "{problems:?}");
        assert!(entries[0].unreviewed);
    }

    #[test]
    fn shards_and_the_single_file_are_both_read_and_ordered() {
        let dir = tempfile::tempdir().unwrap_or_else(|e| panic!("tempdir: {e}"));
        let root = dir.path();
        std::fs::write(root.join(ALLOW_FILE), r#"{"entries": []}"#)
            .unwrap_or_else(|e| panic!("{e}"));
        let shards = root.join(ALLOW_DIR);
        std::fs::create_dir_all(&shards).unwrap_or_else(|e| panic!("{e}"));
        for (name, call) in [("02.json", "os.system"), ("01.json", "subprocess.run")] {
            let body = format!(
                "{{\"entries\": [{{\"path\": \"a.py\", \"call\": \"{call}\", \"line\": \"x\", \
                 \"reason\": \"seeded\"}}]}}"
            );
            std::fs::write(shards.join(name), body).unwrap_or_else(|e| panic!("{e}"));
        }
        let (entries, problems) = load(root);
        assert!(problems.is_empty(), "{problems:?}");
        let calls: Vec<&str> = entries.iter().map(|e| e.call.as_str()).collect();
        assert_eq!(calls, ["subprocess.run", "os.system"], "sorted shard order");
        // The single file is read FIRST, so an entry order does not depend on the
        // filesystem's readdir order; only its two entries show, because the single file
        // itself holds none.
        let origins: Vec<&str> = entries.iter().map(|e| e.origin.as_str()).collect();
        assert_eq!(
            origins,
            [
                "subprocess_stdin_allow.d/01.json",
                "subprocess_stdin_allow.d/02.json"
            ]
        );
    }

    #[test]
    fn the_real_scanners_own_output_keys_the_allowlist_matches() {
        // The end-to-end shape: scan, key the finding's own text into an entry, and the
        // partition comes back clean. A field renamed in either direction goes red here.
        let dir = tree();
        let body = "import subprocess\nsubprocess.run(['cat'])\n";
        std::fs::write(dir.path().join("a.py"), body).unwrap_or_else(|e| panic!("{e}"));
        let (found, _) =
            crate::substdin::calls::scan_source(body).unwrap_or_else(|e| panic!("{e}"));
        let list = vec![("a.py".to_owned(), found[0].clone())];
        let made = vec![entry("a.py", &found[0].call, &found[0].text)];
        let (left, allowed, stale) = partition(&list, &made, &listed(), dir.path());
        assert_eq!((left.len(), allowed, stale.len()), (0, 1, 0));
        assert_eq!(partition(&list, &[], &listed(), dir.path()).0.len(), 1);
    }
}
