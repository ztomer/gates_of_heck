//! The blobs a COMMIT changed, read from the object store (never the worktree).
//!
//! What a check of a commit nobody staged needs: a rebase pick runs no pre-commit (git 2.56,
//! measured), so `post-rewrite` judges each rewritten commit after the fact. "Changed" is against
//! the first parent (`--diff-merges=first-parent`; a root commit against the empty tree), with
//! renames off so a moved file is an addition and is read whole. Gitlinks (submodules) carry no
//! blob here and are skipped.

use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Command, Stdio};

/// A blob one commit added or modified.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Changed {
    /// The commit's full object name.
    pub commit: String,
    /// Repo-relative path.
    pub path: String,
    /// The blob's object name.
    pub blob: String,
}

/// Mode of a gitlink entry: a submodule commit, not a blob in this repository.
const GITLINK_MODE: &str = "160000";

fn git(root: &Path, args: &[&str]) -> Result<Vec<u8>, String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .output()
        .map_err(|e| format!("git {}: {e}", args.first().copied().unwrap_or("")))?;
    if !out.status.success() {
        let detail = String::from_utf8_lossy(&out.stderr);
        return Err(format!(
            "git {} failed: {}",
            args.join(" "),
            detail.trim().chars().take(200).collect::<String>()
        ));
    }
    Ok(out.stdout)
}

/// Every blob `rev` added or modified against its first parent.
///
/// # Errors
///
/// A rev that names no commit, or a git that fails: a commit that cannot be read is never clean.
pub fn changed(root: &Path, rev: &str) -> Result<Vec<Changed>, String> {
    let spec = format!("{rev}^{{commit}}");
    let commit = git(
        root,
        &[
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            &spec,
        ],
    )
    .map_err(|_| format!("'{rev}' names no commit"))?;
    let commit = String::from_utf8_lossy(&commit).trim().to_owned();
    let raw = git(
        root,
        &[
            "diff-tree",
            "-r",
            "-z",
            "--no-commit-id",
            "--root",
            "--no-renames",
            "--diff-merges=first-parent",
            "--diff-filter=AMT",
            &commit,
        ],
    )?;
    Ok(parse_raw(&commit, &raw))
}

/// Parse `diff-tree -z` raw output: `:<old mode> <new mode> <old> <new> <status>` NUL `<path>` NUL.
fn parse_raw(commit: &str, raw: &[u8]) -> Vec<Changed> {
    let mut fields = raw.split(|b| *b == 0).filter(|f| !f.is_empty());
    let mut out = Vec::new();
    while let (Some(meta), Some(path)) = (fields.next(), fields.next()) {
        let meta = String::from_utf8_lossy(meta);
        let parts: Vec<&str> = meta.trim_start_matches(':').split(' ').collect();
        let (Some(mode), Some(blob)) = (parts.get(1), parts.get(3)) else {
            continue;
        };
        if *mode == GITLINK_MODE {
            continue;
        }
        out.push(Changed {
            commit: commit.to_owned(),
            path: String::from_utf8_lossy(path).into_owned(),
            blob: (*blob).to_owned(),
        });
    }
    out
}

/// Read each blob through one `git cat-file --batch`, handing `visit` one body at a time (a rebase
/// of many commits never holds them all).
///
/// # Errors
///
/// A blob git cannot produce: an unread blob is never a clean one.
pub fn for_each(
    root: &Path,
    wanted: &[Changed],
    mut visit: impl FnMut(&Changed, &[u8]),
) -> Result<(), String> {
    if wanted.is_empty() {
        return Ok(());
    }
    let mut child = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["cat-file", "--batch"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("git cat-file: {e}"))?;
    let mut input = wanted
        .iter()
        .map(|c| c.blob.as_str())
        .collect::<Vec<_>>()
        .join("\n");
    input.push('\n');
    // Write on a thread and read here: a batch larger than the pipe buffer deadlocks otherwise.
    let writer = child
        .stdin
        .take()
        .map(|mut stdin| std::thread::spawn(move || stdin.write_all(input.as_bytes())));
    let stdout = child.stdout.take().ok_or("git cat-file: no stdout")?;
    let read = read_batch(wanted, BufReader::new(stdout), &mut visit);
    if let Some(handle) = writer {
        let _ = handle.join();
    }
    let _ = child.wait();
    read
}

fn read_batch(
    wanted: &[Changed],
    mut out: impl BufRead,
    visit: &mut impl FnMut(&Changed, &[u8]),
) -> Result<(), String> {
    for item in wanted {
        let mut header = String::new();
        out.read_line(&mut header)
            .map_err(|e| format!("git cat-file: {e}"))?;
        let size = header
            .split_whitespace()
            .nth(2)
            .and_then(|s| s.parse::<usize>().ok())
            .ok_or_else(|| {
                format!(
                    "cannot read {}:{} ({})",
                    item.commit,
                    item.path,
                    header.trim()
                )
            })?;
        let mut body = vec![0; size];
        let mut newline = [0u8; 1];
        out.read_exact(&mut body)
            .and_then(|()| out.read_exact(&mut newline))
            .map_err(|e| format!("git cat-file: {e}"))?;
        visit(item, &body);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn raw_output_yields_paths_and_skips_gitlinks() {
        let raw = b":000000 100644 0000 aaaa A\0a.txt\0:100644 160000 bbbb cccc M\0sub\0\
                    :100644 100755 dddd eeee M\0dir/b.sh\0";
        let got = parse_raw("c0", raw);
        let paths: Vec<_> = got
            .iter()
            .map(|c| (c.path.as_str(), c.blob.as_str()))
            .collect();
        assert_eq!(paths, vec![("a.txt", "aaaa"), ("dir/b.sh", "eeee")]);
    }

    #[test]
    fn a_missing_blob_is_an_error_not_a_skip() {
        let item = Changed {
            commit: "c0".into(),
            path: "a.txt".into(),
            blob: "ffff".into(),
        };
        let err = read_batch(
            std::slice::from_ref(&item),
            &b"ffff missing\n"[..],
            &mut |_, _| {},
        )
        .unwrap_err();
        assert!(err.contains("c0:a.txt"), "{err}");
    }
}
