//! Which behind lock entries the CHANGE being judged locked (GOH.CRATESDRIFT,
//! `docs/decisions/D-0001-crates-behind-judges-the-change.md`).
//!
//! `goh crates-behind` asks the live registry which entries are behind, and the live registry
//! moves on its own: a release on 2026-11-09 turned four consumers red that had not moved. So a
//! behind entry is RED only when both hold:
//!
//! * the change locked it: its `(name, version)` is in the judged lock and not in the BASE's
//!   (the commit the change started from, `base_rev`); a downgrade is a new version, so a move
//!   further behind is the change's too;
//! * a newer release of its compatibility class was out BEFORE the change was committed (the
//!   sparse index's `pubtime`, against HEAD's committer time; now, for an uncommitted lock). A
//!   release after the commit is not the commit's doing, so a CI re-run and a late push of one
//!   commit are judged alike.
//!
//! Every other behind entry is DRIFT: warned on every run until `cargo update`. `--live` makes
//! every behind entry red (the owner's sweep). No base readable (a shallow clone, a gitignored
//! lock) says so and warns: it never reads as a change that locked nothing.

use crate::crates_behind::Move;
use crate::deps::semver::{compat_class, parse_version};
use crate::estate_seed::Kind;
use goh_tui::WARN;
use serde_json::Value;
use std::collections::BTreeSet;
use std::fmt::Write as _;
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::Duration;

/// The registry's sparse index.
pub const DEFAULT_INDEX: &str = "https://index.crates.io";

/// The commit a change started from and what it is called; `Ok(None)` for a root commit.
pub type BaseRev = Result<Option<(String, String)>, String>;

/// `(name, version)` of every package a lock holds.
pub type Entries = BTreeSet<(String, String)>;

/// The `(name, version)` pairs of a `Cargo.lock`'s text; `None` when it is no lock.
#[must_use]
pub fn lock_entries(text: &str) -> Option<Entries> {
    let doc: toml::Table = text.parse().ok()?;
    let mut out = Entries::new();
    let pkgs = doc.get("package").and_then(toml::Value::as_array);
    for p in pkgs.into_iter().flatten() {
        let field = |k: &str| p.get(k).and_then(toml::Value::as_str);
        if let (Some(n), Some(v)) = (field("name"), field("version")) {
            out.insert((n.to_owned(), v.to_owned()));
        }
    }
    Some(out)
}

fn git(root: &Path, args: &[&str]) -> Option<String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).into_owned())
}

fn commit_of(root: &Path, rev: &str) -> Option<String> {
    git(
        root,
        &[
            "rev-parse",
            "--verify",
            "--quiet",
            &format!("{rev}^{{commit}}"),
        ],
    )
    .map(|s| s.trim().to_owned())
}

/// The commit the change started from, and what it is called.
///
/// `GOH_CHANGE_BASE` (the push gate names the remote's tip, so the whole pushed range is the
/// change), else the merge base with the branch's upstream, else HEAD's first parent. `Ok(None)`:
/// HEAD is a root commit (or there is no HEAD), so the whole lock is the change.
///
/// # Errors
///
/// A named base that is not a commit here; a shallow clone whose HEAD's parent is not fetched.
pub fn base_rev(root: &Path) -> BaseRev {
    if let Some(b) = std::env::var("GOH_CHANGE_BASE")
        .ok()
        .filter(|b| !b.trim().is_empty())
    {
        return commit_of(root, b.trim())
            .map(|c| Some((c, "GOH_CHANGE_BASE".to_owned())))
            .ok_or_else(|| format!("GOH_CHANGE_BASE {b} is not a commit in this clone"));
    }
    if commit_of(root, "@{upstream}").is_some() {
        if let Some(mb) = git(root, &["merge-base", "HEAD", "@{upstream}"]) {
            return Ok(Some((mb.trim().to_owned(), "the upstream".to_owned())));
        }
    }
    if let Some(p) = commit_of(root, "HEAD^") {
        return Ok(Some((p, "HEAD's parent".to_owned())));
    }
    if commit_of(root, "HEAD").is_some() && crate::gitutil::is_shallow(root) {
        return Err("a shallow clone: HEAD's parent is not here".to_owned());
    }
    Ok(None)
}

/// What the change started from, for one lock.
#[derive(Debug, PartialEq, Eq)]
pub enum Base {
    /// The base's entries (none: the lock is new in the change).
    Known(Entries),
    /// No entry can be told the change's, and why.
    Unknown(String),
}

/// The base of the lock at repo-relative `rel`.
#[must_use]
pub fn base_of(root: &Path, rel: &str, rev: &BaseRev) -> Base {
    if !crate::gitutil::is_tracked(root, rel) {
        return Base::Unknown(
            "the lock is not tracked, so no change of the repo's locks it".into(),
        );
    }
    match rev {
        Err(why) => Base::Unknown(why.clone()),
        Ok(None) => Base::Known(Entries::new()),
        // No lock at the base: the change added the whole lock.
        Ok(Some((c, _))) => git(root, &["show", &format!("{c}:{rel}")]).map_or_else(
            || Base::Known(Entries::new()),
            |text| {
                lock_entries(&text).map_or_else(
                    || {
                        Base::Unknown(format!(
                            "the lock at {} is not a lock",
                            &c[..c.len().min(12)]
                        ))
                    },
                    Base::Known,
                )
            },
        ),
    }
}

/// When the judged lock was committed (seconds since the epoch): HEAD's committer time when the
/// lock on disk is HEAD's, else now (an uncommitted lock is judged as of the moment).
#[must_use]
pub fn cutoff(root: &Path, rel: &str) -> u64 {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs());
    // Not a lock taken for a read (tests/test_git_deadline_class.py): `--no-optional-locks`.
    let clean = git(
        root,
        &["--no-optional-locks", "diff", "--quiet", "HEAD", "--", rel],
    )
    .is_some();
    if !clean {
        return now;
    }
    git(root, &["log", "-1", "--format=%ct", "HEAD"])
        .and_then(|t| t.trim().parse().ok())
        .unwrap_or(now)
}

/// Seconds since the epoch of an RFC 3339 UTC stamp (`2026-11-09T01:42:29Z`).
#[must_use]
pub fn parse_utc(s: &str) -> Option<u64> {
    let s = s.strip_suffix('Z')?;
    let (date, time) = s.split_once('T')?;
    let mut ymd = date.split('-').map(str::parse::<i64>);
    let (year, month, day) = (ymd.next()?.ok()?, ymd.next()?.ok()?, ymd.next()?.ok()?);
    let mut hms = time
        .split(':')
        .map(|part| part.split('.').next().unwrap_or(part).parse::<i64>());
    let (hour, minute, second) = (hms.next()?.ok()?, hms.next()?.ok()?, hms.next()?.ok()?);
    // Howard Hinnant's days_from_civil.
    let year = if month <= 2 { year - 1 } else { year };
    let era = year.div_euclid(400);
    let yoe = year - era * 400;
    let mp = (month + 9) % 12;
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    u64::try_from(days * 86_400 + hour * 3_600 + minute * 60 + second).ok()
}

/// A crate's file under the sparse index (the registry's layout rule).
#[must_use]
pub fn index_path(name: &str) -> String {
    let n = name.to_lowercase();
    match n.len() {
        0..=2 => format!("{}/{n}", n.len()),
        3 => format!("3/{}/{n}", &n[..1]),
        _ => format!("{}/{}/{n}", &n[..2], &n[2..4]),
    }
}

/// Whether a release newer than `from` was out before `cutoff`, by the sparse-index text `index`.
///
/// Only a release of `from`'s compatibility class counts, never a yanked one or a prerelease. A
/// version with no `pubtime` predates the field, so it is old. `None`: `from` is no version.
#[must_use]
pub fn out_before(index: &str, from: &str, cutoff: u64) -> Option<bool> {
    let f = parse_version(from)?;
    let class = compat_class(&f);
    Some(
        index
            .lines()
            .filter_map(|l| serde_json::from_str::<Value>(l).ok())
            .any(|e| {
                let Some(v) = e.get("vers").and_then(Value::as_str) else {
                    return false;
                };
                if e.get("yanked").and_then(Value::as_bool) == Some(true) || v.contains('-') {
                    return false;
                }
                let Some(k) = parse_version(v) else {
                    return false;
                };
                let when = e
                    .get("pubtime")
                    .and_then(Value::as_str)
                    .map_or(0, |s| parse_utc(s).unwrap_or(0));
                k > f && compat_class(&k) == class && when < cutoff
            }),
    )
}

/// A crate's sparse-index file from `index`, or `None` (unreachable, timed out, no such crate).
#[must_use]
pub fn fetch_index(index: &str, name: &str, timeout: Duration) -> Option<String> {
    let url = format!("{}/{}", index.trim_end_matches('/'), index_path(name));
    let mut cmd = Command::new("curl");
    cmd.args(["-sfL", "--max-time", &timeout.as_secs().to_string(), &url]);
    let ran = crate::bounded::run(&mut cmd, None, timeout + Duration::from_secs(5)).ok()?;
    (ran.code == Some(0) && !ran.timed_out)
        .then(|| String::from_utf8_lossy(&ran.stdout).into_owned())
}

/// One lock's moves, split: `(red, drift, notes)`. `fetch` reads a crate's index file.
pub fn classify(
    moves: Vec<Move>,
    base: &Base,
    cutoff: u64,
    live: bool,
    fetch: &dyn Fn(&str) -> Option<String>,
) -> (Vec<Move>, Vec<Move>, Vec<String>) {
    let (mut red, mut drift, mut notes) = (Vec::new(), Vec::new(), Vec::new());
    if live {
        return (moves, drift, notes);
    }
    let Base::Known(entries) = base else {
        if let Base::Unknown(why) = base {
            if !moves.is_empty() {
                notes.push(format!(
                    "no base to tell this change's entries from older ones ({why}): every behind entry warns"
                ));
            }
        }
        return (red, moves, notes);
    };
    for m in moves {
        let version = m.from.trim_start_matches('v');
        let (version, git_source) = version
            .split_once(" (")
            .map_or((version, false), |(v, _)| (v, true));
        if entries.contains(&(m.name.clone(), version.to_owned())) {
            drift.push(m);
            continue;
        }
        // A git source has no release dates: the live verdict stands.
        if git_source {
            red.push(m);
            continue;
        }
        match fetch(&m.name).and_then(|ix| out_before(&ix, version, cutoff)) {
            Some(true) => red.push(m),
            Some(false) => drift.push(m),
            None => {
                notes.push(format!(
                    "{}: release dates unreadable from the index: judged against the live registry",
                    m.name
                ));
                red.push(m);
            }
        }
    }
    (red, drift, notes)
}

/// A retired seed still standing, named to delete: the repository's own file, or entries under
/// its root in the private store.
#[must_use]
pub fn retired_seed(root: &Path, kind: Kind, tag: &str) -> String {
    let mut out = String::new();
    if root.join(kind.file).is_file() {
        let _ = writeln!(
            out,
            "{WARN} {tag} {} is retired (D-0001, GOH.CRATESDRIFT): no count is kept now -- delete it",
            kind.file
        );
    }
    let own = crate::gitutil::root_commit(root).unwrap_or_default();
    if let Some(file) = crate::estate_seed::store_dir().map(|d| d.join(kind.file)) {
        let text = std::fs::read_to_string(&file).unwrap_or_default();
        if !own.is_empty() && text.contains(&own) {
            let _ = writeln!(
                out,
                "{WARN} {tag} {} holds this repository's entries, and the seed is retired (D-0001): delete them",
                file.display()
            );
        }
    }
    out
}

/// Up to `n` moves as `a 1 -> 2`, then how many more.
#[must_use]
pub fn listed(moves: &[Move], n: usize) -> String {
    let mut s = moves
        .iter()
        .take(n)
        .map(|m| format!("{} {} -> {}", m.name, m.from, m.to))
        .collect::<Vec<_>>()
        .join(", ");
    if moves.len() > n {
        let _ = write!(s, ", +{} more", moves.len() - n);
    }
    s
}

/// `n` crates behind, as a header says it.
#[must_use]
pub fn plural(n: usize) -> String {
    if n == 1 {
        "1 crate behind its newest compatible release".to_owned()
    } else {
        format!("{n} crates behind their newest compatible release")
    }
}

#[cfg(test)]
#[path = "crates_change_tests.rs"]
mod tests;
