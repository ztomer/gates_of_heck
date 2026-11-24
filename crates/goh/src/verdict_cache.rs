//! Per-file verdict cache (BACKLOG P4), rows of goh-cache.db (GOH.VERDICTDB V2).
//!
//! A checker that is a pure function of one file's bytes and its tools records the verdict under a key naming ALL
//! of its inputs, and a later run with the same key skips the work.
//!
//! The key is the caller's job and the soundness argument is its: every input
//! the verdict depends on goes into it (tool identity, config, the bytes, the
//! path). This module only stores and fetches, and it never lets a bad record
//! read as a verdict: an unreadable or malformed row is a MISS.
//!
//! THE STORE. goh-cache.db's `shell_verdicts(checker, key, record, at)` (`lib/goh_cache_schema`
//! 0003), keyed by the sha256 of the whole key (a shell-lint key is ~9 kB), read and written
//! in-process with bundled SQLite: no sqlite3 library or CLI is assumed on a runner. It was
//! `~/.cache/goh/verdicts/<checker>/<kk>/<sha>.json`, 176k files and 1.3 GB with no bound; the
//! table is bounded at 30 days and 200k rows, and the cache started cold.
//!
//! THE BINARY NEVER MIGRATES. `lib/goh_cache_db.py` owns the file's schema and versioning: a
//! file without the table is migrated by one `lib/goh_cache_db.py migrate` (the house python)
//! before the first write (Python decides whether this copy may), and a file holding a migration
//! the goh checkout does not ship (a newer goh's) is read and never written.
//!
//! EVERY ERROR IS A MISS. A file that cannot be opened, a busy lock past its timeout, a missing
//! table: every key misses (or the write is skipped) and the result is the uncached one.
//!
//! `GOH_VERDICT_CACHE=0` turns it off; `GOH_CACHE_DB` names the file (absolute only, as
//! `lib/goh_cache_db.py`; an off word or a relative path turns the cache off), else
//! `$XDG_CACHE_HOME/goh/goh-cache.db`, else `~/.cache/goh/goh-cache.db`.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use rusqlite::{Connection, OpenFlags};
use sha2::{Digest, Sha256};

/// The table every checker's rows live in.
const TABLE: &str = "shell_verdicts";
/// Keys per `IN (...)` query.
const CHUNK: usize = 500;

/// The cache for one checker, or `None` when the cache is off.
pub struct Cache {
    db: PathBuf,
    checker: String,
}

/// goh-cache.db's path from the environment: `GOH_CACHE_DB` (absolute; an off word or a relative
/// path is `None`), else `$XDG_CACHE_HOME/goh/goh-cache.db`, else `$HOME/.cache/goh/goh-cache.db`.
#[must_use]
pub fn db_path() -> Option<PathBuf> {
    let xdg = || {
        std::env::var_os("XDG_CACHE_HOME")
            .filter(|v| !v.is_empty())
            .map(|x| PathBuf::from(x).join("goh").join("goh-cache.db"))
            .or_else(|| crate::cache_root::home_cache("goh/goh-cache.db"))
    };
    crate::cache_root::root("GOH_CACHE_DB", xdg)
}

/// Whether `db` sits directly in the real home's goh state or cache directory while a suite
/// runs (`GATES_OF_HECK_SUITE_REAL_HOME`, `lib/db_core.py` `guard`): no test opens the real file.
fn real_home_file(db: &Path) -> bool {
    let Some(real) = std::env::var_os("GATES_OF_HECK_SUITE_REAL_HOME").filter(|v| !v.is_empty())
    else {
        return false;
    };
    let canon = |p: &Path| std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
    let here = db.parent().map(canon);
    let real = PathBuf::from(real);
    [real.join(".local/state/goh"), real.join(".cache/goh")]
        .iter()
        .any(|d| here.as_ref() == Some(&canon(d)))
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().fold(String::new(), |mut s, b| {
        use std::fmt::Write as _;
        let _ = write!(s, "{b:02x}");
        s
    })
}

/// The row key of a whole key: its sha256, as hex.
fn row_key(key: &str) -> String {
    hex(&Sha256::digest(key.as_bytes()))
}

/// The busy timeout: `GOH_CACHE_DB_BUSY_MS` (`lib/goh_cache_db.py`'s), else 2 s.
fn busy() -> std::time::Duration {
    std::time::Duration::from_millis(busy_ms(std::env::var("GOH_CACHE_DB_BUSY_MS").ok()))
}

fn busy_ms(raw: Option<String>) -> u64 {
    raw.and_then(|v| v.parse().ok()).unwrap_or(2_000)
}

fn has_table(con: &Connection) -> bool {
    con.query_row(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?1",
        [TABLE],
        |_| Ok(()),
    )
    .is_ok()
}

/// The migration numbers the goh checkout ships (`lib/goh_cache_schema/NNNN_name.sql`).
fn shipped() -> Option<Vec<i64>> {
    let dir = crate::goh_root().join("lib").join("goh_cache_schema");
    let names = std::fs::read_dir(dir).ok()?;
    Some(
        names
            .filter_map(Result::ok)
            .filter_map(|e| {
                let name = e.file_name().into_string().ok()?;
                let stem = name.strip_suffix(".sql")?;
                stem.get(..4)?.parse::<i64>().ok()
            })
            .collect(),
    )
}

/// Why the open file is a newer goh's (it holds a migration the checkout does not ship), or
/// `None`. A checkout whose schema directory cannot be read ships nothing to compare: the
/// table's shape is then the only guard, and a write to a reshaped table fails as a skipped write.
fn newer(con: &Connection, known: Option<&[i64]>) -> Option<String> {
    let known = known?;
    let unknown: Vec<i64> = applied(con).unwrap_or_default();
    let unknown: Vec<i64> = unknown.into_iter().filter(|n| !known.contains(n)).collect();
    (!unknown.is_empty()).then(|| {
        format!("goh-cache.db holds migration {unknown:?}, which this goh does not ship: read only")
    })
}

/// The migration numbers the open file records (`schema_version`).
fn applied(con: &Connection) -> rusqlite::Result<Vec<i64>> {
    let mut stmt = con.prepare("SELECT version FROM schema_version")?;
    let found = stmt.query_map([], |r| r.get(0))?.collect();
    found
}

impl Cache {
    /// The cache for `checker` in goh-cache.db, unless `GOH_VERDICT_CACHE` is an off word or
    /// `GOH_CACHE_DB` is an off word or relative (`db_path`).
    #[must_use]
    pub fn open(checker: &str) -> Option<Self> {
        if std::env::var("GOH_VERDICT_CACHE").is_ok_and(|v| crate::cache_root::is_off(&v)) {
            return None;
        }
        Some(Self {
            db: db_path()?,
            checker: checker.to_owned(),
        })
    }

    fn connect(&self, flags: OpenFlags) -> Option<Connection> {
        if real_home_file(&self.db) || !self.db.is_file() {
            return None;
        }
        let con =
            Connection::open_with_flags(&self.db, flags | OpenFlags::SQLITE_OPEN_NO_MUTEX).ok()?;
        con.busy_timeout(busy()).ok()?;
        Some(con)
    }

    /// The recorded verdict of every key that has one; a miss (no row, a malformed one, a
    /// file or table that cannot be read) is absent.
    #[must_use]
    pub fn get_many(&self, keys: &[&str]) -> HashMap<String, serde_json::Value> {
        let mut out = HashMap::new();
        let Some(con) = self.connect(OpenFlags::SQLITE_OPEN_READ_ONLY) else {
            return out;
        };
        if !has_table(&con) {
            return out;
        }
        let by_row: HashMap<String, &str> = keys.iter().map(|k| (row_key(k), *k)).collect();
        let rows: Vec<&String> = by_row.keys().collect();
        for part in rows.chunks(CHUNK) {
            // A query that fails is a miss for its keys, as every other error here.
            for (row, record) in self.fetch(&con, part).unwrap_or_default() {
                let verdict = serde_json::from_str::<serde_json::Value>(&record).ok();
                if let (Some(key), Some(v)) = (by_row.get(&row), verdict) {
                    out.insert((*key).to_owned(), v);
                }
            }
        }
        out
    }

    /// `(row key, record)` of this checker's rows among `part`.
    fn fetch(&self, con: &Connection, part: &[&String]) -> rusqlite::Result<Vec<(String, String)>> {
        let sql = format!(
            "SELECT key, record FROM {TABLE} WHERE checker=?1 AND key IN ({})",
            vec!["?"; part.len()].join(",")
        );
        let mut stmt = con.prepare(&sql)?;
        let params = std::iter::once(&self.checker).chain(part.iter().copied());
        let found = stmt
            .query_map(rusqlite::params_from_iter(params), |r| {
                Ok((r.get(0)?, r.get(1)?))
            })?
            .collect();
        found
    }

    /// The recorded verdict for `key`, or `None` (a miss).
    #[must_use]
    pub fn get(&self, key: &str) -> Option<serde_json::Value> {
        self.get_many(&[key]).remove(key)
    }

    /// A file holding no `shell_verdicts` yet, migrated by the seam that owns it.
    fn migrate(&self) {
        // The house interpreter (G2d): PATH's first python3 may be below the floor. None at all is
        // a skipped write, as every other failure here.
        let seam = crate::goh_root().join("lib").join("goh_cache_db.py");
        let _ = crate::house_python::path().map(|py| {
            std::process::Command::new(py)
                .arg(seam)
                .arg("migrate")
                .env("GOH_CACHE_DB", &self.db)
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status()
        });
    }

    /// Record every `(key, verdict)` in one transaction. Best effort: a cache that cannot be
    /// written only costs the next run its time.
    pub fn put_many(&self, rows: &[(&str, serde_json::Value)]) {
        if rows.is_empty() || real_home_file(&self.db) {
            return;
        }
        let has = self
            .connect(OpenFlags::SQLITE_OPEN_READ_ONLY)
            .is_some_and(|c| has_table(&c));
        if !has {
            self.migrate();
        }
        if let Some(mut con) = self.connect(OpenFlags::SQLITE_OPEN_READ_WRITE) {
            self.write(&mut con, rows, shipped().as_deref());
        }
    }

    /// `rows` into the open file, unless it has no table or holds a migration outside `known`
    /// (a newer goh's file, said once): one IMMEDIATE transaction, rolled back on any failure.
    fn write(
        &self,
        con: &mut Connection,
        rows: &[(&str, serde_json::Value)],
        known: Option<&[i64]>,
    ) {
        if !has_table(con) {
            return;
        }
        if let Some(why) = newer(con, known) {
            eprintln!("goh: {why}");
            return;
        }
        let sql = format!(
            "INSERT OR REPLACE INTO {TABLE}(checker, key, record, at) VALUES \
             (?1, ?2, ?3, (julianday('now') - 2440587.5) * 86400.0)"
        );
        let done = con
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
            .and_then(|tx| {
                {
                    let mut stmt = tx.prepare(&sql)?;
                    for (k, v) in rows {
                        stmt.execute((&self.checker, row_key(k), v.to_string()))?;
                    }
                }
                tx.commit()
            });
        drop(done); // a failed write is a skipped one: the next run judges those files again
    }

    /// Record `verdict` for `key` (`put_many` of one).
    pub fn put(&self, key: &str, verdict: &serde_json::Value) {
        self.put_many(&[(key, verdict.clone())]);
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
    hex(&Sha256::digest(bytes))
}

#[cfg(test)]
mod tests {
    use super::{busy_ms, row_key, Cache};
    use std::path::PathBuf;

    fn cache(db: PathBuf) -> Cache {
        Cache {
            db,
            checker: "t".to_owned(),
        }
    }

    fn table(db: &std::path::Path, applied: &[i64]) -> rusqlite::Connection {
        let con = rusqlite::Connection::open(db).unwrap_or_else(|e| panic!("open: {e}"));
        let ddl = "CREATE TABLE schema_version(version INTEGER PRIMARY KEY); \
                   CREATE TABLE shell_verdicts(checker TEXT, key TEXT, record TEXT, at REAL, \
                   PRIMARY KEY(checker, key));";
        assert!(con.execute_batch(ddl).is_ok(), "the test's schema");
        for n in applied {
            assert!(
                con.execute("INSERT INTO schema_version VALUES (?1)", [n])
                    .is_ok(),
                "{n}"
            );
        }
        con
    }

    fn count(con: &rusqlite::Connection) -> i64 {
        con.query_row("SELECT count(*) FROM shell_verdicts", [], |r| r.get(0))
            .unwrap_or(-1)
    }

    #[test]
    fn a_newer_goh_s_file_is_never_written_and_a_known_one_is() {
        let dir = tempfile::tempdir().unwrap_or_else(|e| panic!("tempdir: {e}"));
        let db = dir.path().join("goh-cache.db");
        let mut con = table(&db, &[1, 2, 3, 9999]);
        let c = cache(db);
        let rows = [("k", serde_json::json!([1]))];
        c.write(&mut con, &rows, Some(&[1, 2, 3]));
        assert_eq!(
            count(&con),
            0,
            "a file holding 9999 was written by a goh shipping 1-3"
        );
        c.write(&mut con, &rows, Some(&[1, 2, 3, 9999]));
        assert_eq!(count(&con), 1);
        assert_eq!(c.get("k"), Some(serde_json::json!([1])));
    }

    #[test]
    fn a_file_without_the_table_and_an_empty_batch_are_skipped_writes() {
        let dir = tempfile::tempdir().unwrap_or_else(|e| panic!("tempdir: {e}"));
        let db = dir.path().join("goh-cache.db");
        let mut con = rusqlite::Connection::open(&db).unwrap_or_else(|e| panic!("open: {e}"));
        let c = cache(db);
        c.write(&mut con, &[("k", serde_json::json!([1]))], None);
        assert_eq!(c.get("k"), None);
        let none = cache(dir.path().join("none.db"));
        none.put_many(&[]);
        assert!(
            !dir.path().join("none.db").exists(),
            "an empty batch created the file"
        );
    }

    #[test]
    fn a_file_that_is_no_database_is_every_key_a_miss_and_a_skipped_write() {
        let dir = tempfile::tempdir().unwrap_or_else(|e| panic!("tempdir: {e}"));
        let db = dir.path().join("goh-cache.db");
        assert!(
            std::fs::write(&db, b"not SQLite at all, not SQLite at all").is_ok(),
            "plant"
        );
        let c = cache(db.clone());
        c.put("k", &serde_json::json!([1]));
        assert_eq!(c.get_many(&["k", "j"]), std::collections::HashMap::new());
        let after = std::fs::read(&db).unwrap_or_else(|e| panic!("{e}"));
        assert_eq!(after, b"not SQLite at all, not SQLite at all");
    }

    #[test]
    fn a_missing_file_is_every_key_a_miss_and_is_never_created_by_a_read() {
        let dir = tempfile::tempdir().unwrap_or_else(|e| panic!("tempdir: {e}"));
        let c = cache(dir.path().join("none.db"));
        assert!(c.get("k").is_none());
        assert!(!dir.path().join("none.db").exists());
    }

    #[test]
    fn the_busy_timeout_is_its_knob_s_else_two_seconds() {
        assert_eq!(busy_ms(Some("5".to_owned())), 5);
        assert_eq!(busy_ms(Some("soon".to_owned())), 2_000);
        assert_eq!(busy_ms(None), 2_000);
    }

    #[test]
    fn a_row_key_is_the_whole_key_s_sha256() {
        assert_eq!(row_key("a").len(), 64);
        assert_ne!(row_key("a"), row_key("b"));
    }
}
