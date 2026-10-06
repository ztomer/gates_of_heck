//! crates.io's latest stable version per crate name.
//!
//! Port of the retired `checks/_crates_io.py`: one answer per name per TTL in the SAME cache the
//! Python tier writes (`~/.cache/goh/crates-io/<name>.json`, `{"fetched",
//! "version"}`), the misses asked concurrently. The network is `curl`,
//! bounded; an unreachable index is `None`, which the report says out loud.

use std::path::PathBuf;
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const USER_AGENT: &str = "gates_of_heck-dep-currency";
const NET_TIMEOUT_S: u64 = 10;
const DEFAULT_TTL_S: u64 = 6 * 3600;
const WORKERS: usize = 8;

fn cache_dir() -> Option<PathBuf> {
    match std::env::var("GOH_CRATES_IO_CACHE")
        .unwrap_or_default()
        .as_str()
    {
        "off" => None,
        "" => {
            let base = std::env::var_os("XDG_CACHE_HOME")
                .map(PathBuf::from)
                .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".cache")))?;
            Some(base.join("goh").join("crates-io"))
        }
        dir => Some(PathBuf::from(dir)),
    }
}

fn ttl() -> u64 {
    std::env::var("GOH_CRATES_IO_TTL_S")
        .ok()
        .and_then(|v| v.trim().parse::<i64>().ok())
        .map_or(DEFAULT_TTL_S, |n| u64::try_from(n.max(0)).unwrap_or(0))
}

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0.0, |d| d.as_secs_f64())
}

fn safe(name: &str) -> bool {
    (1..=64).contains(&name.len())
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

fn cached(path: &std::path::Path) -> Option<String> {
    let doc: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()?;
    let fetched = doc.get("fetched")?.as_f64()?;
    let version = doc.get("version")?.as_str()?;
    // Exact for any TTL a person writes (u32 seconds is 136 years); larger is "forever".
    let ttl = u32::try_from(ttl()).map_or(f64::MAX, f64::from);
    let fresh = now() - fetched < ttl;
    (fresh && !version.is_empty()).then(|| version.to_owned())
}

fn store(dir: &std::path::Path, path: &std::path::Path, version: &str) {
    if std::fs::create_dir_all(dir).is_err() {
        return;
    }
    let tmp = dir.join(format!(
        ".tmp-{}-{}",
        std::process::id(),
        path.file_name()
            .map_or_else(String::new, |n| n.to_string_lossy().into_owned())
    ));
    let doc = serde_json::json!({ "fetched": now(), "version": version });
    if std::fs::write(&tmp, doc.to_string()).is_err() || std::fs::rename(&tmp, path).is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
}

fn fetch(name: &str) -> Option<String> {
    let mut cmd = Command::new("curl");
    cmd.args([
        "-s",
        "-f",
        "-m",
        &NET_TIMEOUT_S.to_string(),
        "-A",
        USER_AGENT,
    ])
    .arg(format!("https://crates.io/api/v1/crates/{name}"));
    let ran = crate::bounded::run(&mut cmd, None, Duration::from_secs(NET_TIMEOUT_S + 5)).ok()?;
    if ran.code != Some(0) {
        return None;
    }
    let doc: serde_json::Value = serde_json::from_slice(&ran.stdout).ok()?;
    doc.get("crate")?
        .get("max_stable_version")?
        .as_str()
        .map(str::to_owned)
}

fn latest_stable(name: &str) -> Option<String> {
    let dir = cache_dir();
    let path = dir
        .as_ref()
        .filter(|_| safe(name))
        .map(|d| d.join(format!("{name}.json")));
    if let Some(hit) = path.as_deref().and_then(cached) {
        return Some(hit);
    }
    let version = fetch(name)?;
    if let (Some(d), Some(p)) = (dir.as_deref(), path.as_deref()) {
        store(d, p, &version);
    }
    Some(version)
}

/// `latest_stable` for every name, the misses asked concurrently.
#[must_use]
pub fn latest_many(names: &[String]) -> std::collections::BTreeMap<String, Option<String>> {
    let mut unique: Vec<String> = names.to_vec();
    unique.sort();
    unique.dedup();
    let chunk = unique.len().div_ceil(WORKERS).max(1);
    std::thread::scope(|scope| {
        let mut handles = Vec::new();
        for part in unique.chunks(chunk) {
            handles.push(scope.spawn(move || {
                part.iter()
                    .map(|n| (n.clone(), latest_stable(n)))
                    .collect::<Vec<_>>()
            }));
        }
        let mut out = std::collections::BTreeMap::new();
        for h in handles {
            out.extend(h.join().unwrap_or_default());
        }
        out
    })
}
