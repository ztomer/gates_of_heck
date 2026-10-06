//! `goh proven`: the proven-step cache's hot path in-process -- `gates/_proven.sh`'s key, lookup
//! and record (roadmap 4C.2).
//!
//! A key was `git status` + `write-tree` + `shasum` (Perl) + `cut` in bash, and a lookup one more
//! git call, per step, per group, per gate. This computes the SAME bytes, so the two are
//! interchangeable -- a record either side writes, either side finds (`tests/test_proven_native.py`):
//!
//! * the identity's run-constant half is the bash memo, `GOH_PROVEN_IDENTITY` (`_proven.sh` hands
//!   work here only when that memo is its own), and the live half -- the environment and the ignored
//!   files a build reads, builtins in bash -- arrives on stdin;
//! * the record format is the bash one: `epoch=`, `label=`, `tree=`, `step=` lines, under the COMMON
//!   git dir's `goh-proven/`, pruned of expired records on every write.

use std::fmt::Write as _;
use std::io::{Read, Write as _};
use std::path::PathBuf;
use std::process::{Command, Stdio};

use sha2::{Digest, Sha256};

fn git(args: &[&str]) -> Option<String> {
    let out = Command::new("git")
        .args(args)
        .stderr(Stdio::null())
        .output()
        .ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).into_owned())
}

/// `proven_tree`: the index's tree when the working tree holds exactly its bytes, else `None`.
fn tree() -> Option<String> {
    let status = git(&[
        "--no-optional-locks",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ])?;
    for line in status.lines().filter(|l| !l.is_empty()) {
        let b = line.as_bytes();
        // staged only (`X `) is fine: the working tree holds the index's bytes; anything else --
        // untracked, an unstaged edit, an unmerged path -- has no tree git can name.
        if line.starts_with("??") || b.get(1) != Some(&b' ') {
            return None;
        }
    }
    let t = git(&["write-tree"])?;
    let t = t.trim();
    (!t.is_empty()).then(|| t.to_owned())
}

/// The identity bytes `proven_identity` prints: the memo, then the live half from stdin.
fn identity() -> Option<Vec<u8>> {
    std::env::var_os("GOH_PROVEN_IDENTITY_FOR")?;
    let memo = std::env::var("GOH_PROVEN_IDENTITY").unwrap_or_default();
    let mut out = memo.into_bytes();
    out.push(b'\n');
    let mut live = Vec::new();
    std::io::stdin().read_to_end(&mut live).ok()?;
    out.extend_from_slice(&live);
    Some(out)
}

fn hex(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut s = String::with_capacity(64);
    for b in digest {
        let _ = write!(s, "{b:02x}");
    }
    s
}

/// `goh proven key STEP [ENTRY...]`: `KEY TREE`, the tree key -- or, with entries, the scoped key
/// (`proven_scoped_key`). Exit 1: no key (a dirty tree, no repo); 3: no identity memo.
#[must_use]
pub fn key(step: &str, entries: &[String]) -> i32 {
    let Some(identity) = identity() else {
        return 3;
    };
    let Some(tree) = tree() else {
        return 1;
    };
    let mut bytes = Vec::new();
    if entries.is_empty() {
        bytes.extend_from_slice(format!("proven v1\ntree {tree}\nstep {step}\n").as_bytes());
    } else {
        bytes.extend_from_slice(format!("proven v1 scoped\nstep {step}\n").as_bytes());
        for e in entries {
            bytes.extend_from_slice(format!("scope {e}\n").as_bytes());
        }
        let Some(objs) = objects(&tree, entries) else {
            return 1;
        };
        bytes.extend_from_slice(objs.as_bytes());
        bytes.push(b'\n');
    }
    bytes.extend_from_slice(&identity);
    println!("{} {tree}", hex(&bytes));
    0
}

/// One `cat-file --batch-check` for every entry, as the bash: a missing entry is `missing` (cat-file
/// echoes the `<tree>:<path>`, which would key it on the whole tree), trailing newlines trimmed as a
/// command substitution trims them.
fn objects(tree: &str, entries: &[String]) -> Option<String> {
    let mut child = Command::new("git")
        .args(["cat-file", "--batch-check=%(objectname) %(objecttype)"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let mut input = String::new();
    for e in entries {
        if e == "." {
            input.push_str(tree);
        } else {
            let _ = write!(input, "{tree}:{e}");
        }
        input.push('\n');
    }
    child.stdin.take()?.write_all(input.as_bytes()).ok()?;
    let out = child.wait_with_output().ok()?;
    let text = String::from_utf8_lossy(&out.stdout);
    let lines: Vec<&str> = text
        .lines()
        .map(|l| {
            if l.ends_with(" missing") {
                "missing"
            } else {
                l
            }
        })
        .collect();
    Some(lines.join("\n").trim_end_matches('\n').to_owned())
}

fn dir() -> Option<PathBuf> {
    let common = git(&["rev-parse", "--path-format=absolute", "--git-common-dir"])?;
    Some(PathBuf::from(common.trim()).join("goh-proven"))
}

fn now() -> u64 {
    std::env::var("GOH_PROVEN_NOW")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or_else(|| {
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |d| d.as_secs())
        })
}

fn age(s: u64) -> String {
    if s < 60 {
        format!("{s}s")
    } else if s < 3600 {
        format!("{}m", s / 60)
    } else {
        format!("{}h{:02}m", s / 3600, (s % 3600) / 60)
    }
}

/// `goh proven lookup KEY STEP TTL`: `AGE LABEL` and exit 0 for a record of this key made for this
/// exact step and younger than TTL seconds; exit 1 otherwise.
#[must_use]
pub fn lookup(key: &str, step: &str, ttl: u64) -> i32 {
    let Some(text) = dir().and_then(|d| std::fs::read_to_string(d.join(key)).ok()) else {
        return 1;
    };
    let (mut epoch, mut by, mut recorded) = (None, String::new(), None);
    for line in text.lines() {
        if let Some(v) = line.strip_prefix("epoch=") {
            epoch = v
                .parse::<u64>()
                .ok()
                .filter(|_| v.bytes().all(|b| b.is_ascii_digit()));
        } else if let Some(v) = line.strip_prefix("label=") {
            v.clone_into(&mut by);
        } else if let Some(v) = line.strip_prefix("step=") {
            recorded = Some(v.to_owned());
        }
    }
    let Some(epoch) = epoch else {
        return 1;
    };
    if recorded.as_deref() != Some(step) {
        return 1;
    }
    let Some(seconds) = now().checked_sub(epoch).filter(|s| *s < ttl) else {
        return 1;
    };
    println!(
        "{} {}",
        age(seconds),
        if by.is_empty() { "unknown" } else { &by }
    );
    0
}

/// `goh proven record KEY TREE STEP LABEL TTL`: the record, written atomically, then every expired
/// or unreadable record pruned.
#[must_use]
pub fn record(key: &str, tree: &str, step: &str, label: &str, ttl: u64) -> i32 {
    let Some(dir) = dir() else {
        return 1;
    };
    if std::fs::create_dir_all(&dir).is_err() {
        return 1;
    }
    let now = now();
    let tmp = dir.join(format!(".tmp.{}", std::process::id()));
    let body = format!("epoch={now}\nlabel={label}\ntree={tree}\nstep={step}\n");
    if std::fs::write(&tmp, body).is_err() || std::fs::rename(&tmp, dir.join(key)).is_err() {
        let _ = std::fs::remove_file(&tmp);
        return 1;
    }
    for entry in std::fs::read_dir(&dir).into_iter().flatten().flatten() {
        let path = entry.path();
        // Dotfiles are other writers' in-flight `.tmp.*`, as the bash glob `"$dir"/*` skips them.
        if !path.is_file() || entry.file_name().to_string_lossy().starts_with('.') {
            continue;
        }
        let first = std::fs::read_to_string(&path).unwrap_or_default();
        let epoch = first
            .lines()
            .next()
            .and_then(|l| l.strip_prefix("epoch="))
            .and_then(|v| v.parse::<u64>().ok());
        if epoch.is_none_or(|e| now.saturating_sub(e) >= ttl) {
            let _ = std::fs::remove_file(&path);
        }
    }
    0
}
