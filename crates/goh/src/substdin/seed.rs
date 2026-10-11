//! `goh subprocess-stdin --seed`: write the ratchet allowlist a repo adopts the gate with.
//!
//! A gate turned on over a repo with debt is red in every file at once, and a gate that is red
//! everywhere on its first day is a gate that gets switched off. So adoption is a SEED: today's
//! findings written as entries, each `unreviewed`, which the passing line counts as debt until a
//! person re-earns it (BACKLOG 3.2). Three rules keep a gate that seeds itself from being a gate
//! that cannot fail:
//!
//!   * an entry a person already wrote is KEPT as written -- its reason, its status -- while it
//!     still matches a finding, so a reseed never launders a reviewed entry back to debt or a
//!     debt entry into a reviewed one;
//!   * an entry nothing matches any more is DROPPED and counted, never carried, so a reseed cannot
//!     leave headroom for the defect to come back;
//!   * the seed refuses while tracked Python has uncommitted edits: a seed and a code change in
//!     one commit is how a new violation gets exempted by the same commit that adds it.
//!
//! Entries are one JSON object per line, so a file holds `SHARD` of them under the line cap; past
//! that the seed writes `subprocess_stdin_allow.d/seed-NN.json` shards instead of one file.

use goh_tui::{ERR, OK, START};
use std::fmt::Write as _;
use std::path::Path;

use super::allow::{self, Entry};
use super::calls::Finding;
use super::TAG;

/// Entries per allowlist file: one per line, plus the two bracket lines, under a 500-line cap.
pub const SHARD: usize = 400;
/// The reason a seeded entry carries until a person writes a real one.
pub const SEED_REASON: &str =
    "seeded when this repo adopted the gate; not yet reviewed -- fix the call or say why it stands";

/// One entry as the seed writes it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Seeded {
    pub path: String,
    pub call: String,
    pub line: String,
    pub reason: String,
    pub unreviewed: bool,
}

/// The seed over `findings` given today's `entries`: (what to write, how many kept, how many
/// added, how many dropped). Pure, so every direction is a unit test.
#[must_use]
pub fn plan(
    findings: &[(String, Finding)],
    entries: &[Entry],
) -> (Vec<Seeded>, usize, usize, usize) {
    let hits = allow::assign(findings, entries);
    let kept = hits.iter().flatten().count();
    let out = findings
        .iter()
        .zip(&hits)
        .map(|((path, f), hit)| {
            hit.map_or_else(
                || Seeded {
                    path: path.clone(),
                    call: f.call.clone(),
                    line: f.text.clone(),
                    reason: SEED_REASON.to_owned(),
                    unreviewed: true,
                },
                |i| {
                    let e = &entries[i];
                    Seeded {
                        path: e.path.clone(),
                        call: e.call.clone(),
                        line: e.line.clone(),
                        reason: e.reason.clone(),
                        unreviewed: e.unreviewed,
                    }
                },
            )
        })
        .collect();
    (out, kept, findings.len() - kept, entries.len() - kept)
}

/// One allowlist file's text: `{"entries": [` then one object per line.
#[must_use]
pub fn file_text(entries: &[Seeded]) -> String {
    let mut s = String::from("{\"entries\": [\n");
    for (i, e) in entries.iter().enumerate() {
        let obj = serde_json::json!({
            "path": e.path,
            "call": e.call,
            "line": e.line,
            "reason": e.reason,
            "status": if e.unreviewed { "unreviewed" } else { "legitimate" },
        });
        let comma = if i + 1 < entries.len() { "," } else { "" };
        let _ = writeln!(s, "  {obj}{comma}");
    }
    s.push_str("]}\n");
    s
}

/// Replace every allowlist file at `root` with `entries`: one file up to `SHARD`, shards past it.
fn write(root: &Path, entries: &[Seeded]) -> Result<Vec<String>, String> {
    let single = root.join(allow::ALLOW_FILE);
    let dir = root.join(allow::ALLOW_DIR);
    if let Ok(read) = std::fs::read_dir(&dir) {
        for shard in read.filter_map(Result::ok).map(|e| e.path()) {
            if shard.extension().is_some_and(|x| x == "json") {
                std::fs::remove_file(&shard).map_err(|e| format!("{}: {e}", shard.display()))?;
            }
        }
    }
    if entries.len() <= SHARD {
        std::fs::write(&single, file_text(entries))
            .map_err(|e| format!("{}: {e}", allow::ALLOW_FILE))?;
        return Ok(vec![allow::ALLOW_FILE.to_owned()]);
    }
    if single.is_file() {
        std::fs::remove_file(&single).map_err(|e| format!("{}: {e}", allow::ALLOW_FILE))?;
    }
    std::fs::create_dir_all(&dir).map_err(|e| format!("{}: {e}", allow::ALLOW_DIR))?;
    let mut written = Vec::new();
    for (n, chunk) in entries.chunks(SHARD).enumerate() {
        let rel = format!("{}/seed-{:02}.json", allow::ALLOW_DIR, n + 1);
        std::fs::write(root.join(&rel), file_text(chunk)).map_err(|e| format!("{rel}: {e}"))?;
        written.push(rel);
    }
    Ok(written)
}

/// `goh subprocess-stdin --seed`, over the whole tree: (exit code, report).
#[must_use]
pub fn run(root: &Path, listed: &[String], files: &[String]) -> (i32, String) {
    match crate::gitutil::uncommitted(root, &["*.py", "*.PY"]) {
        Err(m) => return (2, format!("{ERR} {TAG} {m}\n")),
        Ok(dirty) if !dirty.is_empty() => {
            return (
                1,
                format!(
                    "{ERR} {TAG} refusing to seed over uncommitted Python ({}): commit the code first, \
                     then seed in a commit of its own, so no change is exempted by the commit that \
                     makes it\n",
                    dirty.join(", ")
                ),
            );
        }
        Ok(_) => {}
    }
    let (entries, problems) = allow::load(root);
    if !problems.is_empty() {
        let mut s = String::new();
        for p in &problems {
            let _ = writeln!(s, "{ERR} {TAG} {p}");
        }
        let _ = writeln!(s, "{START} fix the allowlist first: a seed over one it cannot read would lose its reviewed entries");
        return (1, s);
    }
    let outcome = super::scan(root, listed, files, false);
    if let Some((path, why)) = outcome.unreadable.first() {
        return (
            1,
            format!("{ERR} {TAG} {path} is not Python this check can read: {why}\n"),
        );
    }
    let (seeded, kept, added, dropped) = plan(&outcome.findings, &entries);
    match write(root, &seeded) {
        Err(m) => (2, format!("{ERR} {TAG} cannot write the allowlist: {m}\n")),
        Ok(files) => (
            0,
            format!(
                "{OK} {TAG} seeded {}: {} entr(ies) -- {kept} kept as written, {added} new and \
                 'unreviewed', {dropped} dropped as stale; {} process call(s) seen in {} file(s)\n",
                files.join(", "),
                seeded.len(),
                outcome.seen,
                outcome.examined
            ),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::substdin::calls::Why;

    fn found(path: &str, text: &str) -> (String, Finding) {
        (
            path.to_owned(),
            Finding {
                line: 1,
                call: "subprocess.run".to_owned(),
                why: Why::Inherits,
                text: text.to_owned(),
            },
        )
    }

    fn entry(path: &str, line: &str, reason: &str, unreviewed: bool) -> Entry {
        Entry {
            path: path.to_owned(),
            call: "subprocess.run".to_owned(),
            line: line.to_owned(),
            reason: reason.to_owned(),
            unreviewed,
            origin: allow::ALLOW_FILE.to_owned(),
        }
    }

    #[test]
    fn a_new_finding_is_seeded_unreviewed() {
        let (out, kept, added, dropped) = plan(&[found("a.py", "subprocess.run(x)")], &[]);
        assert_eq!((kept, added, dropped), (0, 1, 0));
        assert!(out[0].unreviewed);
        assert_eq!(out[0].reason, SEED_REASON);
    }

    #[test]
    fn a_reviewed_entry_keeps_its_reason_and_status() {
        let e = entry("a.py", "subprocess.run(x)", "the child reads a pipe", false);
        let (out, kept, added, _) = plan(&[found("a.py", "subprocess.run(x)")], &[e]);
        assert_eq!((kept, added), (1, 0));
        assert!(!out[0].unreviewed);
        assert_eq!(out[0].reason, "the child reads a pipe");
    }

    #[test]
    fn an_entry_nothing_matches_is_dropped_and_counted() {
        let e = entry("a.py", "subprocess.run(gone)", "r", true);
        let (out, _, _, dropped) = plan(&[], &[e]);
        assert_eq!(out, Vec::<Seeded>::new());
        assert_eq!(dropped, 1);
    }

    #[test]
    fn two_identical_calls_need_two_entries() {
        let e = entry("a.py", "subprocess.run(x)", "kept", false);
        let both = [
            found("a.py", "subprocess.run(x)"),
            found("a.py", "subprocess.run(x)"),
        ];
        let (out, kept, added, _) = plan(&both, &[e]);
        assert_eq!((kept, added), (1, 1));
        assert!(out[1].unreviewed);
    }

    #[test]
    fn the_written_file_reads_back_as_the_same_entries() {
        let seeded = vec![Seeded {
            path: "a.py".to_owned(),
            call: "subprocess.run".to_owned(),
            line: "subprocess.run(\"x\")".to_owned(),
            reason: SEED_REASON.to_owned(),
            unreviewed: true,
        }];
        let dir = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
        std::fs::write(dir.path().join(allow::ALLOW_FILE), file_text(&seeded))
            .unwrap_or_else(|e| panic!("{e}"));
        let (entries, problems) = allow::load(dir.path());
        assert_eq!(problems, Vec::<String>::new());
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].line, "subprocess.run(\"x\")");
        assert!(entries[0].unreviewed);
    }
}
