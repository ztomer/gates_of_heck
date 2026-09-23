//! Staged blobs, read ONCE per run and shared by every native scanner
//! (rust-port-plan Phase 3d).
//!
//! WHY. A staged check reads the index blob, never the worktree
//! (`gitutil::content_bytes`), and it did so with one `git show :path` per
//! file PER SCANNER. Measured on a 50-file staged fixture: 197 git spawns,
//! 193 of them `git show` — process spawns, not bytes, were the cost, and
//! they grew as scanners × files. Here every staged path is fetched through a
//! single `git cat-file --batch` before the scanners run, and
//! `content_bytes` answers from it. The bytes are the same bytes `git show`
//! returns (same object, same index), so no verdict can change; a path this
//! cannot carry falls through to `git show` exactly as before.
//!
//! What falls through, and why:
//! - a path containing a newline (the batch protocol is line-delimited);
//! - anything past [`PREFETCH_BUDGET_BYTES`] (a commit that stages a huge
//!   asset must not hold every blob in memory at once — the old path held
//!   one at a time, and so does the fallback);
//! - a path git reports `missing` (the fallback then reports it as before).

use std::collections::HashMap;
use std::fmt::Write as _;
use std::io::{BufReader, Write as _};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};

/// Most bytes held at once. Past it the rest are read one at a time, as
/// before; the number is a memory ceiling for one gate run, not a tuning.
pub const PREFETCH_BUDGET_BYTES: usize = 64 * 1024 * 1024;

/// The index-view prefix `git` resolves as "this path, stage 0".
const INDEX_SPEC_PREFIX: &str = ":";

type Key = (PathBuf, String);

fn cache() -> &'static Mutex<HashMap<Key, Vec<u8>>> {
    static CACHE: OnceLock<Mutex<HashMap<Key, Vec<u8>>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

/// The prefetched staged blob for `rel` under `root`, if this run has it.
#[must_use]
pub fn cached(root: &Path, rel: &str) -> Option<Vec<u8>> {
    let map = cache().lock().ok()?;
    map.get(&(root.to_path_buf(), rel.to_owned())).cloned()
}

/// Read the index blob of every path in `paths` through one
/// `git cat-file --batch`.
///
/// Returns how many were cached; anything not cached is simply read the old
/// way later, so a failure here costs speed, never correctness.
#[must_use]
pub fn prefetch_staged(root: &Path, paths: &[String]) -> usize {
    let wanted: Vec<&String> = paths.iter().filter(|p| !p.contains('\n')).collect();
    if wanted.is_empty() {
        return 0;
    }
    let Ok(mut child) = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["cat-file", "--batch"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
    else {
        return 0;
    };
    // Write on a thread and read here: a batch larger than the pipe buffer
    // deadlocks if one side waits for the other.
    let input = wanted.iter().fold(String::new(), |mut acc, p| {
        let _ = writeln!(acc, "{INDEX_SPEC_PREFIX}{p}");
        acc
    });
    let writer = child
        .stdin
        .take()
        .map(|mut stdin| std::thread::spawn(move || stdin.write_all(input.as_bytes())));
    let fetched = child.stdout.take().map_or(0, |stdout| {
        read_batch(root, &wanted, BufReader::new(stdout))
    });
    if let Some(handle) = writer {
        let _ = handle.join();
    }
    let _ = child.wait();
    fetched
}

/// Parse `git cat-file --batch` answers in request order: a header
/// `<oid> <type> <size>`, `size` bytes and a newline; or `<spec> missing`.
fn read_batch(root: &Path, wanted: &[&String], mut out: impl std::io::BufRead) -> usize {
    let mut held = 0usize;
    let mut fetched = 0;
    for rel in wanted {
        let mut header = String::new();
        if out.read_line(&mut header).unwrap_or(0) == 0 {
            break; // git stopped answering: the rest fall through
        }
        let size = header
            .split_whitespace()
            .nth(2)
            .and_then(|s| s.parse::<usize>().ok());
        let Some(size) = size else {
            continue; // `missing` / `ambiguous`: no body follows
        };
        let mut body = vec![0; size];
        let mut newline = [0u8; 1];
        if out.read_exact(&mut body).is_err() || out.read_exact(&mut newline).is_err() {
            break;
        }
        if held + size > PREFETCH_BUDGET_BYTES {
            continue; // read past it (the stream must advance) but hold nothing
        }
        held += size;
        if let Ok(mut map) = cache().lock() {
            map.insert((root.to_path_buf(), (*rel).clone()), body);
            fetched += 1;
        }
    }
    fetched
}

#[cfg(test)]
mod tests {
    use super::*;

    fn git(repo: &Path, args: &[&str]) {
        let status = Command::new("git")
            .args(args)
            .current_dir(repo)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        assert!(status.is_ok_and(|s| s.success()), "git {args:?}");
    }

    /// The prefetched bytes are the INDEX's, not the worktree's — the
    /// contract `content_bytes` exists for — and a missing path, a path with
    /// a newline, and one staged after the budget fall through.
    #[test]
    fn prefetch_holds_the_index_blob_and_skips_what_it_cannot_carry() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let repo = tmp.path();
        git(repo, &["init", "-q"]);
        std::fs::write(repo.join("a.txt"), "staged\n").expect("write");
        std::fs::write(repo.join("b.txt"), "also staged\n").expect("write");
        git(repo, &["add", "a.txt", "b.txt"]);
        std::fs::write(repo.join("a.txt"), "worktree edit, not staged\n").expect("write");
        let paths = vec![
            "a.txt".to_owned(),
            "nope.txt".to_owned(),
            "b.txt".to_owned(),
            "odd\nname".to_owned(),
        ];
        assert_eq!(prefetch_staged(repo, &paths), 2);
        assert_eq!(cached(repo, "a.txt").as_deref(), Some(&b"staged\n"[..]));
        assert_eq!(
            cached(repo, "b.txt").as_deref(),
            Some(&b"also staged\n"[..])
        );
        assert_eq!(cached(repo, "nope.txt"), None);
        assert_eq!(cached(repo, "odd\nname"), None);
    }
}
