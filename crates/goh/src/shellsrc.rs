//! Which tracked files are shell SOURCE, and their text -- one definition for every shell scanner.
//!
//! `*.sh`, `*.sh.tmpl` (the template an installer is regenerated from: leaving it out let a
//! release put a fixed site straight back), `*.bash`, `*.zsh`, and any file whose shebang names
//! sh/bash/zsh/ksh/dash. Shared by `goh early-exit-pipe` and `goh dead-after-exec`, so a scope fix
//! reaches both: two copies of "what is a shell script" is how one scanner went blind to `.tmpl`.

use std::path::Path;

const SHELLS: [&str; 5] = ["sh", "bash", "zsh", "ksh", "dash"];

/// Is `rel` (with these leading bytes) a shell script?
#[must_use]
pub fn is_shell(rel: &str, head: &[u8]) -> bool {
    let name = rel.rsplit('/').next().unwrap_or(rel);
    if [".sh", ".sh.tmpl", ".bash", ".zsh"]
        .iter()
        .any(|x| name.ends_with(x))
    {
        return true;
    }
    let Some(line) = head.strip_prefix(b"#!") else {
        return false;
    };
    let line = line.split(|b| *b == b'\n').next().unwrap_or(line);
    let line = String::from_utf8_lossy(line);
    let mut words = line.split_whitespace();
    let interp = words.next().unwrap_or("").rsplit('/').next().unwrap_or("");
    let interp = if interp == "env" {
        words.find(|w| !w.starts_with('-')).unwrap_or("")
    } else {
        interp
    };
    SHELLS.contains(&interp)
}

/// The first bytes of a worktree file, enough for its shebang.
fn head_bytes(path: &Path) -> Vec<u8> {
    use std::io::Read as _;
    let mut buf = vec![0u8; 256];
    let n = std::fs::File::open(path)
        .and_then(|mut f| f.read(&mut buf))
        .unwrap_or(0);
    buf.truncate(n);
    buf
}

/// `(path, text)` of every in-scope UTF-8 shell source among `files` (the index's blobs when
/// `staged`), skipping what `exclude` matches.
#[must_use]
pub fn sources(
    root: &Path,
    files: &[String],
    exclude: Option<&crate::pathfilter::PathFilter>,
    staged: bool,
) -> Vec<(String, String)> {
    let mut out = Vec::new();
    for rel in files {
        if exclude.is_some_and(|x| x.is_match(rel)) {
            continue;
        }
        let head = if staged {
            crate::gitutil::content_bytes(root, rel, true).unwrap_or_default()
        } else {
            head_bytes(&root.join(rel))
        };
        if !is_shell(rel, &head) {
            continue;
        }
        let Some(blob) = crate::gitutil::content_bytes(root, rel, staged) else {
            continue;
        };
        if let Ok(text) = String::from_utf8(blob) {
            out.push((rel.clone(), text));
        }
    }
    out
}
