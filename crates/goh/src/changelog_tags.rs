//! The whole changelog agrees with the tags (`goh changelog-tags`, GOH.CHANGELOGALL).
//!
//! Lifted from a private consumer's `tools/check_changelog_tags.py` (ZN.CLHEAD, a private consumer at 421acfb): a private consumer's v5.11.11
//! stanza lost its `## v5.11.11` heading, the line read ` — a socket appears only once it listens
//! ... (2026-11-08)`, so its entries sat inside v5.12.0's body and nothing said so.
//! `tools/release-kit/changelog_cut.py check` asks only about the stanza being tagged, at release
//! time; the whole file is this check's. Three findings:
//!
//! * a heading TAIL with no heading: a line `— title (YYYY-MM-DD)` (or `-- `, or `_(date)_`)
//!   outside a `## ` line, the shape a lost heading leaves behind;
//! * a release TAG (`vN[.N...]`) with no `## vN` (or `## N`, `## [N]`) heading;
//! * a version heading, at or above the oldest tag, that no tag names -- except the CURRENT
//!   version's, whose tag is cut after the release commit exists: the `VERSION` file's when the
//!   repo tracks one, else the newest heading when it is above every tag (a stanza cut ahead of
//!   its tag; a second one above every tag is a finding).
//!
//! NOT JUDGED (GOH.TAGLESSCI): a shallow clone, or one with no `v*` tag while its changelog has
//! version headings, holds a tag set that is not the repository's (a CI checkout fetches one or
//! none), so the run names why and how to fetch them, exits 0 and calls no seed entry stale.
//!
//! REACH (GOH.TAGSREACH): only the tags reachable from `HEAD` are judged (`git tag --merged HEAD`;
//! at `--staged`, `HEAD` is the staged commit's parent), so a branch cut before a release does not
//! fail for a tag, and a heading, that only later main commits have.
//!
//! SCOPE: the repo's `CHANGELOG.md`, else `docs/CHANGELOG.md`; a repo with neither has nothing to
//! judge and says so. Both scopes: the file and the reachable tags are read whole either way (the index's
//! copy of the changelog at `--staged`).
//!
//! WIRING: a structural step in EVERY repo with a changelog. The estate's findings on the day it
//! landed are seeded in `changelog_tags_seed.json` (`crate::estate_seed`), one entry per changelog
//! with its count: at or under it warns, anything else is red.

use crate::estate_seed::{self, Kind, Report, Sites};
use regex::Regex;
use std::path::Path;
use std::sync::LazyLock;

const TAG: &str = "[changelog_tags]";
/// The step's label, the same line as in `structural_steps.txt`.
pub const LABEL: &str = "every tag has its changelog heading";

/// The seed of the estate's findings on the day the check landed.
pub const KIND: Kind = Kind {
    file: "changelog_tags_seed.json",
    unit: "changelog finding",
    name: "changelog-tags seed",
};

static HEAD: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^## \[?v?(\d+(?:\.\d+)+)\]?(?:\s|$)")
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});
static ORPHAN_TAIL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^\s*(?:—|--)\s+\S.*\(\d{4}-\d{2}-\d{2}\)_?\s*$")
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});
static TAGNAME: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^v(\d+(?:\.\d+)*)$").unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});

const FIX: &str = "    Every release tag has its `## vX` heading, every entry sits under one, and a heading names a\n    \
     tag (a private consumer's v5.11.11 lost its heading and its entries read as v5.12.0's, ZN.CLHEAD). Restore\n    \
     the `## vX` on an orphan tail; add the stanza a tag lacks, or name the version a heading\n    \
     wrongly carries.\n";

/// A version as numbers, so v5.11.10 sorts after v5.11.9.
fn key(v: &str) -> Vec<u64> {
    v.split('.').map(|p| p.parse().unwrap_or(0)).collect()
}

/// Each way the changelog `text` (at `file`) and the `tags` disagree, one listing line each.
/// `current` is the version whose heading may precede its tag (`None`: the newest heading above
/// every tag).
#[must_use]
pub fn findings(file: &str, text: &str, tags: &[String], current: Option<&str>) -> Vec<String> {
    let mut out = Vec::new();
    let mut heads: Vec<(String, usize)> = Vec::new();
    for (n, line) in text.lines().enumerate() {
        if let Some(c) = HEAD.captures(line) {
            let v = c[1].to_string();
            if !heads.iter().any(|(h, _)| *h == v) {
                heads.push((v, n + 1));
            }
        } else if ORPHAN_TAIL.is_match(line) {
            out.push(format!(
                "    {file}:{}: {}  -- a heading tail with no `## vX` heading",
                n + 1,
                estate_seed::clip(line)
            ));
        }
    }
    let mut vers: Vec<String> = tags
        .iter()
        .filter_map(|t| TAGNAME.captures(t).map(|c| c[1].to_string()))
        .collect();
    if vers.is_empty() {
        return out;
    }
    vers.sort_by_key(|v| key(v));
    let (floor, top) = (key(&vers[0]), key(&vers[vers.len() - 1]));
    let pending = current.map_or_else(
        || {
            heads
                .iter()
                .map(|(h, _)| h)
                .max_by_key(|h| key(h))
                .filter(|h| key(h) > top)
                .cloned()
        },
        |c| Some(c.trim_start_matches('v').to_string()),
    );
    for v in &vers {
        if !heads.iter().any(|(h, _)| key(h) == key(v)) {
            out.push(format!("    {file}: tag v{v} has no `## v{v}` heading"));
        }
    }
    for (h, n) in &heads {
        let named = vers.iter().any(|v| key(v) == key(h));
        if key(h) >= floor && !named && pending.as_deref() != Some(h.as_str()) {
            out.push(format!("    {file}:{n}: `## v{h}` names no tag"));
        }
    }
    out
}

/// Why the clone's tags cannot be compared with its changelog (GOH.TAGLESSCI), or `None`.
///
/// A shallow clone holds the tags its fetch carried, not the repository's: `actions/checkout` at
/// its default depth fetches none on a branch push and the ONE pushed on a tag push, and on
/// divoom-control 331be031 the runner read that partial set as a clean changelog and failed the
/// seed as stale (run 43136219614) where the full clone found 16 findings. A clone with no `v*` tag
/// while its changelog has version headings was cloned without them (`--no-tags`) or never
/// tagged; either way nothing it could say about the tags would be a verdict.
#[must_use]
fn untrusted(shallow: bool, tags: &[String], text: &str) -> Option<String> {
    const FETCH: &str = "fetch the whole history and its tags to judge it (actions/checkout `fetch-depth: 0`, or `git fetch --tags --unshallow`)";
    if shallow {
        return Some(format!(
            "a shallow clone: its tags are the ones its fetch carried, not the repository's; {FETCH}"
        ));
    }
    let tagged = tags.iter().any(|t| TAGNAME.is_match(t));
    let headed = text.lines().any(|l| HEAD.is_match(l));
    (!tagged && headed).then(|| {
        format!(
            "no v* tag in this clone, and the changelog has version headings to compare; {FETCH}"
        )
    })
}

/// The repo's changelog among `files`: `CHANGELOG.md`, else `docs/CHANGELOG.md`.
fn changelog_of(files: &[String]) -> Option<&'static str> {
    ["CHANGELOG.md", "docs/CHANGELOG.md"]
        .into_iter()
        .find(|c| files.iter().any(|f| f == c))
}

/// The repo's `v*` tags REACHABLE from `HEAD` (GOH.TAGSREACH): the judged commit is `HEAD`, or its
/// staged child, so the tags of its own history are the ones its changelog can answer for. A tag on
/// a later main commit names a heading a branch cut before that release cannot have yet. A repo
/// with no commit has no tag.
fn tags(root: &Path) -> Result<Vec<String>, String> {
    let git = |args: &[&str]| {
        std::process::Command::new("git")
            .arg("-C")
            .arg(root)
            .args(args)
            .stdin(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .output()
            .map_err(|e| format!("git {}: {e}", args[0]))
    };
    if !git(&["rev-parse", "--verify", "-q", "HEAD"])?
        .status
        .success()
    {
        return Ok(Vec::new());
    }
    let out = git(&["tag", "-l", "--merged", "HEAD", "v*"])?;
    if !out.status.success() {
        return Err("git tag -l --merged HEAD v* failed".to_string());
    }
    Ok(String::from_utf8_lossy(&out.stdout)
        .split_whitespace()
        .map(str::to_string)
        .collect())
}

/// The verdict over the repo at `root`: `(exit code, stdout, stderr)`. `files` is every tracked
/// path (the changelog is looked up among them, whatever the scope).
fn judge(root: &Path, files: &[String], staged: bool) -> (i32, String, String) {
    use goh_tui::{ERR, OK};
    let Some(file) = changelog_of(files) else {
        return (
            0,
            format!("{OK} {TAG} OK — no CHANGELOG.md or docs/CHANGELOG.md: nothing to compare\n"),
            String::new(),
        );
    };
    let seed = match estate_seed::load(root, KIND, staged) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let Some(text) =
        crate::gitutil::content_bytes(root, file, staged).and_then(|b| String::from_utf8(b).ok())
    else {
        return (
            2,
            String::new(),
            format!("{ERR} {TAG} {file} cannot be read as UTF-8\n"),
        );
    };
    let tags = match tags(root) {
        Ok(t) => t,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    if let Some(why) = untrusted(crate::gitutil::is_shallow(root), &tags, &text) {
        let line = format!("{} {TAG} NOT JUDGED — {why}\n", goh_tui::WARN);
        return (0, String::new(), line);
    }
    let current = files
        .iter()
        .any(|f| f == "VERSION")
        .then(|| crate::gitutil::content_bytes(root, "VERSION", staged))
        .flatten()
        .map(|b| String::from_utf8_lossy(&b).trim().to_string())
        .filter(|v| !v.is_empty());
    let found = findings(file, &text, &tags, current.as_deref());
    let mut sites = Sites::new();
    if !found.is_empty() {
        sites.insert(file.to_string(), found);
    }
    let report = Report {
        tag: TAG,
        item: "GOH.CHANGELOGALL",
        what: (
            "way the changelog and the tags disagree",
            "ways the changelog and the tags disagree",
        ),
        fix: FIX,
        ok: format!(
            "{file}: every one of {} has its heading, no orphan entry",
            goh_tui::counted(tags.len(), "tag")
        ),
    };
    // Whole-file at both scopes, so a staged run judges a stale entry too -- unless no `v*` tag was
    // there to compare (a CI checkout fetches none): then nothing was read, and nothing is stale.
    // At a commit the whole changelog was read, so that is what its run settles (GOH.SEEDSTALE).
    let unread = !tags.iter().any(|t| TAGNAME.is_match(t));
    let scope = estate_seed::Scope {
        commit: staged,
        full: !unread,
        read: Some(if unread {
            std::collections::BTreeSet::new()
        } else {
            std::collections::BTreeSet::from([file.to_string()])
        }),
    };
    estate_seed::verdict_in(root, scope, KIND, &seed, &sites, &report)
}

/// `goh changelog-tags [--staged]`.
#[must_use]
pub fn run_command(staged: bool) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        goh_tui::eoutln!("{} {TAG} not a git repository", goh_tui::ERR);
        return 2;
    };
    match crate::gitutil::index_files(&root) {
        Ok(files) => estate_seed::print(judge(&root, &files, staged)),
        Err(m) => {
            goh_tui::eoutln!("{} {TAG} {m}", goh_tui::ERR);
            2
        }
    }
}

/// The structural step, in every repo.
///
/// Announced always, so a repo with no changelog says it has none rather than skipping silently. The worktree is looked at first, so that repo spends
/// no git spawn on the step (`tests/test_goh_git_spawns.py`).
#[must_use]
pub fn step(repo: &Path, staged: bool) -> Option<i32> {
    let on_disk = ["CHANGELOG.md", "docs/CHANGELOG.md"]
        .iter()
        .any(|c| repo.join(c).is_file());
    let files = if on_disk {
        crate::gitutil::index_files(repo).unwrap_or_default()
    } else {
        Vec::new()
    };
    estate_seed::step(
        LABEL,
        "crates/goh/src/changelog_tags.rs",
        judge(repo, &files, staged),
    )
}

#[cfg(test)]
#[path = "changelog_tags_tests.rs"]
mod tests;
