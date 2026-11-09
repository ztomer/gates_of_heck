//! A `Cargo.lock` behind its newest compatible releases fails the push (`goh crates-behind`,
//! GOH.CRATESBEHIND).
//!
//! House Rust rule 2 is "latest dependencies", and nothing read it: `routines`' lock had fallen 37
//! compatible updates behind (FAULTS.ROUTINES) while every gate was green. `goh deps` asks
//! crates.io about DIRECT dependencies and only reports drift; a transitive patch release is
//! invisible to it. Cargo already knows the answer for the whole graph: `cargo update --dry-run`
//! prints each lock entry it would move (`Updating itoa v1.0.18 -> v1.0.19`) and writes nothing.
//!
//! WHAT IS JUDGED: each lock the gate names (`--lock`, every tracked lock of the repo, the same
//! list `cargo audit` reads: `lib/rust_locks.py plan`). A lock entry cargo would update or
//! downgrade is one crate behind; `Adding`/`Removing` lines are the consequences of those moves
//! and are not counted. A major behind is not this check's business (`cargo update` never moves
//! one; `goh deps` reports them).
//!
//! NEVER A SILENT PASS. The dry run needs the registry index. Each cargo call is BOUNDED
//! (`--timeout`, default 120 s, its process group killed at the ceiling); a call that timed out or
//! failed on the network is SKIPPED with the reason quoted, and the pass line then says the bill is
//! not clean. `--offline` (the gate passes it for `GOH_DEPS_OFFLINE=1`) skips every lock by name.
//! Any other cargo failure cannot be judged and exits 2.
//!
//! DUPLICATE MAJORS WARN: `cargo tree -d` lists every crate the graph carries in two versions;
//! the ones in two compatibility classes (`syn 2` beside `syn 3`) are named as warnings. Two
//! majors are often upstream's doing, so it is a report, not a verdict.
//!
//! RED IS THE CHANGE'S, NEVER THE REGISTRY'S (GOH.CRATESDRIFT, 2026-11-09; the decision is
//! `docs/decisions/D-0001-crates-behind-judges-the-change.md`). The registry moves on its own, and a
//! count of entries behind it, ratcheted by a seed, turned four consumers red in one day that had
//! not moved. A behind entry is red only when the change being judged locked it and a newer
//! release was out before the change was committed (`crate::crates_change`); every other behind
//! entry is DRIFT, one warning per lock naming each, until `cargo update`. `--live` is the owner's
//! sweep: every behind entry red. The estate seed (`crates_behind_seed.json`) is retired with the
//! count it ratcheted: one still standing is named to delete, never read.

use crate::crates_change::{self, Base};
use crate::estate_seed::{self, Kind};
use goh_tui::{ERR, OK, WARN};
use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

const TAG: &str = "[crates_behind]";

/// The default bound on each cargo call, in seconds.
pub const DEFAULT_TIMEOUT_SECS: u64 = 120;

/// The retired seed of the estate's locks behind on the day the check landed (D-0001): named to
/// delete wherever it still stands, never read.
pub const KIND: Kind = Kind {
    file: "crates_behind_seed.json",
    unit: "crate behind",
    name: "crates-behind seed",
};

const FIX: &str = "    House Rust rule 2: latest dependencies. In the workspace of each lock named, run\n      \
     cargo update\n    \
     then the gate, and commit the lock. A crate that must stay held is pinned in the manifest\n    \
     (`=x.y.z`) with the reason beside it, never left behind in the lock.\n";

/// One lock entry `cargo update --dry-run` would move.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Move {
    pub name: String,
    pub from: String,
    pub to: String,
}

/// The entries a dry run would update or downgrade, in cargo's order.
///
/// Lines with no ` -> ` (the index fetch, a git checkout, `Locking N packages`), `Adding` and
/// `Removing`, and a path package's version (a lock out of step with its manifests, which
/// `--locked` reports) are not moves.
#[must_use]
pub fn parse_moves(stderr: &str) -> Vec<Move> {
    stderr
        .lines()
        .filter_map(|l| {
            let (verb, rest) = l.trim().split_once(' ')?;
            if verb != "Updating" && verb != "Downgrading" {
                return None;
            }
            let (left, to) = rest.split_once(" -> ")?;
            let (name, from) = left.split_once(' ')?;
            // A path package (a workspace member whose manifest version moved) is the lock out of
            // step with its manifests, not a release behind: the gate's `--locked` step names it.
            if from.contains(" (/") {
                return None;
            }
            Some(Move {
                name: name.to_owned(),
                from: from.trim().to_owned(),
                to: to.trim().to_owned(),
            })
        })
        .collect()
}

/// The marks of a registry cargo could not reach (lower-cased), from cargo's and curl's errors.
const UNREACHABLE: [&str; 14] = [
    "failed to download",
    "failed to fetch",
    "failed to query replaced source",
    "failed to load source for dependency",
    "failed to update registry",
    "unable to update registry",
    "download of config.json failed",
    "couldn't resolve host",
    "could not resolve host",
    "network failure",
    "spurious network error",
    "--offline was specified",
    "connection refused",
    "ssl connect error",
];

/// Why cargo's failure is an unreachable registry, quoting the line that says so; `None` when the
/// failure is something else.
#[must_use]
pub fn unreachable_reason(stderr: &str) -> Option<String> {
    stderr
        .lines()
        .find(|l| {
            let l = l.to_lowercase();
            UNREACHABLE.iter().any(|m| l.contains(m))
        })
        .map(estate_seed::clip)
}

/// Crates `cargo tree -d --prefix none --format {p}` lists in more than one compatibility class:
/// `(name, versions)`, versions ascending. Two copies of one class are not a duplicate major.
#[must_use]
pub fn duplicate_majors(tree_out: &str) -> Vec<(String, Vec<String>)> {
    use crate::deps::semver::{compat_class, parse_version};
    let mut by: std::collections::BTreeMap<String, Vec<String>> = std::collections::BTreeMap::new();
    for line in tree_out.lines() {
        let mut words = line.split_whitespace();
        let (Some(name), Some(v)) = (words.next(), words.next()) else {
            continue;
        };
        let Some(v) = v.strip_prefix('v') else {
            continue;
        };
        let held = by.entry(name.to_owned()).or_default();
        if !held.iter().any(|h| h == v) {
            held.push(v.to_owned());
        }
    }
    by.into_iter()
        .filter_map(|(name, mut vs)| {
            vs.sort_by_key(|v| parse_version(v));
            let mut classes: Vec<_> = vs
                .iter()
                .filter_map(|v| parse_version(v).map(|k| compat_class(&k)))
                .collect();
            classes.dedup();
            (classes.len() > 1).then_some((name, vs))
        })
        .collect()
}

/// What one lock's dry run came to.
#[derive(Debug, PartialEq, Eq)]
pub enum Outcome {
    /// The registry answered: the moves (none: the lock is current).
    Judged(Vec<Move>),
    /// Cargo is offline (`CARGO_NET_OFFLINE`) and judged against its CACHED index: each move is
    /// real, but a release newer than the cache is unseen, so it is no clean bill.
    Cached(Vec<Move>),
    /// Not judged, and why: offline, unreachable, timed out.
    Skipped(String),
    /// Cargo failed for a reason that is not the network: nothing can be said.
    Failed(String),
}

fn bounded_cargo(
    cargo: &str,
    dir: &Path,
    args: &[&str],
    timeout: Duration,
) -> Result<crate::bounded::Ran, String> {
    let mut cmd = Command::new(cargo);
    cmd.current_dir(dir)
        .args(args)
        .env("CARGO_TERM_COLOR", "never");
    crate::bounded::run(&mut cmd, None, timeout).map_err(|e| format!("cannot run {cargo}: {e}"))
}

/// One lock's dry run, under `timeout`.
#[must_use]
pub fn dry_run(cargo: &str, dir: &Path, timeout: Duration) -> Outcome {
    let ran = match bounded_cargo(cargo, dir, &["update", "--dry-run"], timeout) {
        Ok(r) => r,
        Err(m) => return Outcome::Failed(m),
    };
    let err = String::from_utf8_lossy(&ran.stderr);
    if ran.timed_out {
        return Outcome::Skipped(format!(
            "`cargo update --dry-run` timed out after {} s (registry index slow or unreachable)",
            timeout.as_secs()
        ));
    }
    if ran.code == Some(0) {
        let cached = std::env::var("CARGO_NET_OFFLINE").is_ok_and(|v| v == "true" || v == "1");
        let moves = parse_moves(&err);
        return if cached {
            Outcome::Cached(moves)
        } else {
            Outcome::Judged(moves)
        };
    }
    unreachable_reason(&err).map_or_else(
        || {
            let first = err
                .lines()
                .find(|l| l.trim_start().starts_with("error"))
                .or_else(|| err.lines().find(|l| !l.trim().is_empty()))
                .unwrap_or("no output");
            Outcome::Failed(format!(
                "`cargo update --dry-run` failed: {}",
                estate_seed::clip(first)
            ))
        },
        |why| Outcome::Skipped(format!("registry index unreachable: {why}")),
    )
}

/// The duplicate majors of one lock's graph, or why they could not be listed.
fn tree_duplicates(
    cargo: &str,
    dir: &Path,
    offline: bool,
    timeout: Duration,
) -> Result<Vec<(String, Vec<String>)>, String> {
    let mut args = vec![
        "tree",
        "-d",
        "--workspace",
        "--target",
        "all",
        "--locked",
        "--depth",
        "0",
        "--prefix",
        "none",
        "--format",
        "{p}",
        "-e",
        "normal,build,dev",
    ];
    if offline {
        args.push("--offline");
    }
    let ran = bounded_cargo(cargo, dir, &args, timeout)?;
    if ran.timed_out {
        return Err(format!(
            "`cargo tree -d` timed out after {} s",
            timeout.as_secs()
        ));
    }
    if ran.code != Some(0) {
        let err = String::from_utf8_lossy(&ran.stderr);
        let first = err
            .lines()
            .find(|l| !l.trim().is_empty())
            .unwrap_or("no output");
        return Err(format!(
            "`cargo tree -d` failed: {}",
            estate_seed::clip(first)
        ));
    }
    Ok(duplicate_majors(&String::from_utf8_lossy(&ran.stdout)))
}

/// One lock, judged: its repo-relative path, its outcome, its duplicate majors (or why not), and
/// its behind entries split into the change's (`red`) and the registry's (`drift`).
struct Judged {
    rel: String,
    outcome: Outcome,
    dupes: Result<Vec<(String, Vec<String>)>, String>,
    red: Vec<Move>,
    drift: Vec<Move>,
    notes: Vec<String>,
    /// Where the change started, for the drift line (`HEAD's parent 1a2b3c4d`).
    since: String,
}

impl Judged {
    const fn failed(rel: String, why: String) -> Self {
        Self {
            rel,
            outcome: Outcome::Failed(why),
            dupes: Ok(Vec::new()),
            red: Vec::new(),
            drift: Vec::new(),
            notes: Vec::new(),
            since: String::new(),
        }
    }
}

fn judge_lock(
    root: &Path,
    lock: &str,
    a: &crate::cli::CratesBehindArgs,
    offline: bool,
    rev: &crates_change::BaseRev,
) -> Judged {
    let path = if Path::new(lock).is_absolute() {
        PathBuf::from(lock)
    } else {
        root.join(lock)
    };
    let rel = path
        .strip_prefix(root)
        .unwrap_or(&path)
        .to_string_lossy()
        .into_owned();
    let dir = path
        .parent()
        .map_or_else(|| root.to_path_buf(), Path::to_path_buf);
    let timeout = Duration::from_secs(a.timeout);
    if !path.is_file() {
        return Judged::failed(rel, format!("no lock at {}", path.display()));
    }
    let outcome = if offline {
        Outcome::Skipped("offline (GOH_DEPS_OFFLINE=1): the registry was not asked".to_owned())
    } else {
        dry_run(&a.cargo, &dir, timeout)
    };
    let dupes = tree_duplicates(&a.cargo, &dir, offline, timeout);
    let (red, drift, notes) = match &outcome {
        Outcome::Judged(moves) | Outcome::Cached(moves) if !moves.is_empty() => {
            let base = if a.live {
                Base::Known(crates_change::Entries::new())
            } else {
                crates_change::base_of(root, &rel, rev)
            };
            let fetch = |name: &str| crates_change::fetch_index(&a.index, name, timeout);
            let moves = moves.iter().map(Move::clone).collect();
            let cutoff = crates_change::cutoff(root, &rel);
            crates_change::classify(moves, &base, cutoff, a.live, &fetch)
        }
        _ => (Vec::new(), Vec::new(), Vec::new()),
    };
    let since = match rev {
        _ if a.live => "--live".to_owned(),
        Ok(Some((c, label))) => format!("since {label} {}", &c[..c.len().min(8)]),
        Ok(None) => "the root commit".to_owned(),
        Err(_) => "no base".to_owned(),
    };
    Judged {
        rel,
        outcome,
        dupes,
        red,
        drift,
        notes,
        since,
    }
}

/// The whole verdict: `(exit code, stdout, stderr)`.
fn verdict(root: &Path, judged: &[Judged]) -> (i32, String, String) {
    let (mut notes, mut failed, mut red) = (
        crates_change::retired_seed(root, KIND, TAG),
        String::new(),
        String::new(),
    );
    let (mut skipped, mut reds, mut drifting) = (0usize, 0usize, 0usize);
    for j in judged {
        if matches!(j.outcome, Outcome::Cached(_)) {
            skipped += 1;
            let _ = writeln!(
                notes,
                "{WARN} {TAG} {}: judged against cargo's CACHED index (CARGO_NET_OFFLINE): a newer release is unseen",
                j.rel
            );
        }
        match &j.outcome {
            Outcome::Judged(_) | Outcome::Cached(_) => {}
            Outcome::Skipped(why) => {
                skipped += 1;
                let _ = writeln!(notes, "{WARN} {TAG} {}: NOT judged -- {why}", j.rel);
            }
            Outcome::Failed(why) => {
                let _ = writeln!(failed, "{ERR} {TAG} {}: cannot judge -- {why}", j.rel);
            }
        }
        for n in &j.notes {
            let _ = writeln!(notes, "{WARN} {TAG} {}: {n}", j.rel);
        }
        if !j.drift.is_empty() {
            drifting += j.drift.len();
            // ONE status line per lock: monitor carried 30 behind entries, and a line each would
            // bury every other step's warnings under goh_step's cap (GOH_WARN_LINES).
            let _ = writeln!(
                notes,
                "{WARN} {TAG} {}: {} behind by releases, not this change's ({}; `cargo update` clears it): {}",
                j.rel,
                crates_change::plural(j.drift.len()),
                j.since,
                crates_change::listed(&j.drift, 6)
            );
        }
        for m in &j.red {
            reds += 1;
            let _ = writeln!(red, "    {}: {} {} -> {}", j.rel, m.name, m.from, m.to);
        }
        match &j.dupes {
            Ok(d) => {
                for (name, vs) in d {
                    let _ = writeln!(
                        notes,
                        "{WARN} {TAG} {}: duplicate major {name} {} (cargo tree -d)",
                        j.rel,
                        vs.join(" + ")
                    );
                }
            }
            Err(why) => {
                let _ = writeln!(
                    notes,
                    "{WARN} {TAG} {}: duplicate majors not listed -- {why}",
                    j.rel
                );
            }
        }
    }
    if !failed.is_empty() {
        // Nothing passed: a lock that cannot be judged must not sit under a pass line.
        return (2, String::new(), format!("{notes}{failed}"));
    }
    if reds > 0 {
        let err = format!(
            "{notes}{ERR} {TAG} {} that this change locked (a newer release was out before it was committed):\n{red}{FIX}",
            crates_change::plural(reds)
        );
        return (1, String::new(), err);
    }
    let ok = if skipped > 0 {
        format!(
            "{} of {} lock(s) judged against the live registry, {skipped} not (above) -- NOT a clean bill",
            judged.len() - skipped,
            judged.len()
        )
    } else if drifting > 0 {
        format!(
            "no crate this change locked is behind; {} by later releases (above) -- NOT a clean bill",
            crates_change::plural(drifting)
        )
    } else {
        "every Cargo.lock is at its newest compatible releases".to_owned()
    };
    (0, format!("{OK} {TAG} OK — {ok}\n"), notes)
}

/// `goh crates-behind [--root R] [--lock L]... [--offline] [--timeout S]`.
#[must_use]
pub fn run_command(a: &crate::cli::CratesBehindArgs) -> i32 {
    let Some(root) = a.root.clone().or_else(crate::gitutil::repo_root) else {
        goh_tui::eoutln!("{ERR} {TAG} not a git repository");
        return 2;
    };
    let root = Path::new(&root)
        .canonicalize()
        .unwrap_or_else(|_| PathBuf::from(&root));
    let offline = a.offline || std::env::var("GOH_DEPS_OFFLINE").is_ok_and(|v| v == "1");
    // One base for every lock: the commit the change started from (crate::crates_change).
    let rev = crates_change::base_rev(&root);
    let locks = if a.locks.is_empty() {
        vec!["Cargo.lock".to_owned()]
    } else {
        a.locks.clone()
    };
    // Four at a time: media_server has 31 locks, and each dry run is mostly a network wait.
    let mut judged = Vec::with_capacity(locks.len());
    for chunk in locks.chunks(4) {
        std::thread::scope(|s| {
            let handles: Vec<_> = chunk
                .iter()
                .map(|l| s.spawn(|| judge_lock(&root, l, a, offline, &rev)))
                .collect();
            for (l, h) in chunk.iter().zip(handles) {
                // A worker that panicked judged nothing: it is a lock that cannot be judged, never
                // one that silently drops out of the verdict.
                judged.push(h.join().unwrap_or_else(|_| {
                    Judged::failed(l.clone(), "its worker panicked".to_owned())
                }));
            }
        });
    }
    estate_seed::print(verdict(&root, &judged))
}

#[cfg(test)]
#[path = "crates_behind_tests.rs"]
mod tests;
