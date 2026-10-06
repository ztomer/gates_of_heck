//! Per-file verdict cache (BACKLOG P4).
//!
//! A checker that is a pure function of one file's bytes and its tools records the verdict under a key naming ALL
//! of its inputs, and a later run with the same key skips the work.
//!
//! The key is the caller's job and the soundness argument is its: every input
//! the verdict depends on goes into it (tool identity, config, the bytes, the
//! path). This module only stores and fetches, and it never lets a bad record
//! read as a verdict: an unreadable or malformed record is a MISS, and a
//! record is written to a temp name and renamed into place whole.
//!
//! `GOH_VERDICT_CACHE=0` turns it off; `GOH_VERDICT_DIR` moves it (default
//! `~/.cache/goh/verdicts`).

use std::path::PathBuf;

use sha2::{Digest, Sha256};

/// The cache for one checker, or `None` when the cache is off.
pub struct Cache {
    dir: PathBuf,
}

impl Cache {
    /// The cache under `<root>/<checker>/`, unless `GOH_VERDICT_CACHE=0`.
    #[must_use]
    pub fn open(checker: &str) -> Option<Self> {
        if std::env::var("GOH_VERDICT_CACHE").is_ok_and(|v| v.trim() == "0") {
            return None;
        }
        let root = std::env::var_os("GOH_VERDICT_DIR")
            .map(PathBuf::from)
            .or_else(|| {
                std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".cache/goh/verdicts"))
            })?;
        Some(Self {
            dir: root.join(checker),
        })
    }

    fn path(&self, key: &str) -> PathBuf {
        let digest = Sha256::digest(key.as_bytes());
        let hex: String = digest.iter().fold(String::new(), |mut s, b| {
            use std::fmt::Write as _;
            let _ = write!(s, "{b:02x}");
            s
        });
        self.dir.join(&hex[..2]).join(format!("{hex}.json"))
    }

    /// The recorded verdict for `key`, or `None` (a miss, an unreadable or a
    /// malformed record alike).
    #[must_use]
    pub fn get(&self, key: &str) -> Option<serde_json::Value> {
        let text = std::fs::read_to_string(self.path(key)).ok()?;
        let record: serde_json::Value = serde_json::from_str(&text).ok()?;
        // The record carries its own key: a hash collision, or a file renamed into the wrong
        // slot, is a miss rather than someone else's verdict.
        (record.get("key").and_then(serde_json::Value::as_str) == Some(key))
            .then(|| record.get("verdict").cloned())
            .flatten()
    }

    /// Record `verdict` for `key`. Best effort: a cache that cannot be
    /// written only costs the next run its time.
    pub fn put(&self, key: &str, verdict: &serde_json::Value) {
        let path = self.path(key);
        let Some(dir) = path.parent() else {
            return;
        };
        if std::fs::create_dir_all(dir).is_err() {
            return;
        }
        let record = serde_json::json!({ "key": key, "verdict": verdict });
        let tmp = dir.join(format!(
            ".{}.{}",
            std::process::id(),
            path.file_name()
                .map_or_else(String::new, |n| n.to_string_lossy().into_owned())
        ));
        if std::fs::write(&tmp, record.to_string()).is_err()
            || std::fs::rename(&tmp, &path).is_err()
        {
            let _ = std::fs::remove_file(&tmp);
        }
    }
}

/// A file's identity for a key: its path, size and modification time (a
/// tool binary, a config file). `-` when it does not exist.
#[must_use]
pub fn file_identity(path: &std::path::Path) -> String {
    std::fs::metadata(path).map_or_else(
        |_| format!("{}:-", path.display()),
        |m| {
            let mtime = m
                .modified()
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map_or(0, |d| d.as_nanos());
            format!("{}:{}:{mtime}", path.display(), m.len())
        },
    )
}

/// The sha256 of some bytes, as hex: what a key says about a file's content.
#[must_use]
pub fn content_hash(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .fold(String::new(), |mut s, b| {
            use std::fmt::Write as _;
            let _ = write!(s, "{b:02x}");
            s
        })
}
