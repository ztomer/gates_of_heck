//! The umbrella queue a roadmap cites as `(queue: <ID>)`: which of its items are done.
//!
//! A SEAM, so a test reads a fixture and a machine without the umbrella degrades: `--queue PATH`,
//! else `GOH_ROADMAP_QUEUE`, else `$HOME/Projects/.loop/queue.json`. A queue that is absent or
//! unreadable is not the repo's defect, so the rule is NOT JUDGED and the gate says why -- never a
//! crash, and never a silent pass: the caller prints the reason and withholds the seed's
//! `queue-done` entries, which a blind rule would otherwise read as fixed.
//!
//! The path NAMES the queue; its store is the loop's SQLite file `loop.db` beside it when that exists
//! (LOOP.QUEUEARCHIVE: the layout goh's Python reader, `lib/queue_read.py`, documents), read with
//! the `sqlite3` CLI in read-only mode, else the JSON file itself. A store `sqlite3` cannot read,
//! or a machine without `sqlite3`, is NOT JUDGED with the reason, like an absent file.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// The SQLite store's file name, beside the queue's path.
pub const DB_NAME: &str = "loop.db";

/// The environment key that names the queue file.
pub const ENV: &str = "GOH_ROADMAP_QUEUE";

/// Where the queue is: the flag, the environment, then the umbrella's own place. `Err` says why
/// there is none.
///
/// # Errors
///
/// The flag and the environment are unset or empty and `HOME` is too.
pub fn locate(flag: Option<&str>) -> Result<PathBuf, String> {
    if let Some(p) = flag.filter(|p| !p.is_empty()) {
        return Ok(PathBuf::from(p));
    }
    match std::env::var(ENV) {
        Ok(p) if !p.is_empty() => return Ok(PathBuf::from(p)),
        Ok(_) => return Err(format!("{ENV} is set empty")),
        Err(_) => {}
    }
    std::env::var_os("HOME")
        .filter(|h| !h.is_empty())
        .map(|h| PathBuf::from(h).join("Projects/.loop/queue.json"))
        .ok_or_else(|| format!("neither {ENV} nor HOME is set"))
}

/// Every item's status by id, from the queue file's text.
///
/// # Errors
///
/// The text is not JSON, or has no `items` list of objects with a string `id` and `status`.
pub fn parse(text: &str) -> Result<BTreeMap<String, String>, String> {
    let value: serde_json::Value =
        serde_json::from_str(text).map_err(|e| format!("not JSON: {e}"))?;
    let items = value
        .get("items")
        .and_then(serde_json::Value::as_array)
        .ok_or("no \"items\" list")?;
    let mut out = BTreeMap::new();
    for item in items {
        let field = |k: &str| item.get(k).and_then(serde_json::Value::as_str);
        let (Some(id), Some(status)) = (field("id"), field("status")) else {
            return Err("an item without a string \"id\" and \"status\"".to_owned());
        };
        out.insert(id.to_owned(), status.to_owned());
    }
    Ok(out)
}

/// The queue's statuses, or why the rule cannot be judged here.
///
/// # Errors
///
/// The queue cannot be located, read or parsed; the message names the path.
pub fn load(flag: Option<&str>) -> Result<BTreeMap<String, String>, String> {
    let path = locate(flag)?;
    let db = path.with_file_name(DB_NAME);
    if db.is_file() {
        return load_db(&db);
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("cannot read the queue {}: {e}", path.display()))?;
    parse(&text).map_err(|e| format!("the queue {} is unreadable: {e}", path.display()))
}

/// Every item's status from the SQLite store DB, through `sqlite3 -readonly -json`.
///
/// # Errors
///
/// `sqlite3` cannot run, refuses the store, or prints rows that are not `{id, status}` objects.
pub fn load_db(db: &Path) -> Result<BTreeMap<String, String>, String> {
    let schema = sqlite(db, false, "PRAGMA user_version")?;
    if !KNOWN_SCHEMAS.contains(&schema.trim()) {
        return Err(format!(
            "no queue: {} is at schema {}, which this goh does not know; update goh",
            db.display(),
            schema.trim()
        ));
    }
    let rows = sqlite(
        db,
        true,
        "SELECT id, status FROM items ORDER BY archived, seq",
    )?; // archived rows are done items too
    let rows = if rows.trim().is_empty() {
        "[]"
    } else {
        rows.trim()
    }; // no rows prints nothing
    parse(&format!("{{\"items\":{rows}}}"))
        .map_err(|e| format!("the queue store {} is unreadable: {e}", db.display()))
}

/// The loop.db `PRAGMA user_version` values this reader understands.
const KNOWN_SCHEMAS: [&str; 1] = ["1"];

/// `sqlite3 -readonly [-json] DB SQL`'s output, or why it could not run.
fn sqlite(db: &Path, json: bool, sql: &str) -> Result<String, String> {
    let mut cmd = Command::new("sqlite3");
    cmd.arg("-readonly");
    if json {
        cmd.arg("-json");
    }
    let out = cmd
        .arg(db)
        .arg(sql)
        .stdin(Stdio::null())
        .output()
        .map_err(|e| {
            format!(
                "cannot run sqlite3 to read the queue store {}: {e}",
                db.display()
            )
        })?;
    if !out.status.success() {
        let why = String::from_utf8_lossy(&out.stderr);
        return Err(format!(
            "the queue store {} is unreadable: {}",
            db.display(),
            why.trim()
        ));
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}
