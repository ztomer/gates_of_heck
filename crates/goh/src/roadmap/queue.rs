//! The umbrella queue a roadmap cites as `(queue: <ID>)`: which of its items are done.
//!
//! A SEAM, so a test reads a fixture and a machine without the umbrella degrades: `--queue PATH`,
//! else `GOH_ROADMAP_QUEUE`, else `$HOME/Projects/.loop/queue.json`. A queue that is absent or
//! unreadable is not the repo's defect, so the rule is NOT JUDGED and the gate says why -- never a
//! crash, and never a silent pass: the caller prints the reason and withholds the seed's
//! `queue-done` entries, which a blind rule would otherwise read as fixed.

use std::collections::BTreeMap;
use std::path::PathBuf;

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
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("cannot read the queue {}: {e}", path.display()))?;
    parse(&text).map_err(|e| format!("the queue {} is unreadable: {e}", path.display()))
}
