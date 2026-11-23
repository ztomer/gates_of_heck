//! Reading and comparing shellcheck's dotted version (`goh shell-lint`'s floor check).

/// `1.2.3` as numbers, for comparison; `None` when it is not a dotted version.
fn version_parts(v: &str) -> Option<Vec<u64>> {
    v.split('.').map(|p| p.parse::<u64>().ok()).collect()
}

#[must_use]
/// `have >= floor` as dotted versions (`0.11` is `0.11.0`); an unreadable one is not.
pub fn newer_or_equal(have: &str, floor: &str) -> bool {
    let (Some(mut h), Some(mut f)) = (version_parts(have), version_parts(floor)) else {
        return false;
    };
    let n = h.len().max(f.len());
    h.resize(n, 0);
    f.resize(n, 0);
    h >= f
}

#[must_use]
/// The `version: X.Y.Z` line of `shellcheck --version`.
pub fn reported_version(text: &str) -> Option<&str> {
    text.lines()
        .find_map(|l| l.strip_prefix("version:"))
        .map(str::trim)
}

/// What `shellcheck --version` says, asked once per binary identity (GOH.SCLINTKEY).
///
/// The verdict store keeps the answer under the binary's path, size and mtime -- the identity
/// every shell verdict is already keyed on (`shell_lint::tools_key`) -- so a run over a known
/// binary execs no `--version` (one per shell-lint run was ~309 execs per goh suite run). With
/// the store off, or no binary on PATH to name, it asks every time.
///
/// # Errors
///
/// The `--version` run failed to start or timed out.
pub fn reported() -> Result<String, String> {
    let cache = crate::verdict_cache::Cache::open("shellcheck-version");
    let key =
        crate::shell_lint::which("shellcheck").map(|p| crate::verdict_cache::file_identity(&p));
    let kept = cache.as_ref().zip(key.as_ref()).and_then(|(c, k)| c.get(k));
    if let Some(text) = kept.as_ref().and_then(serde_json::Value::as_str) {
        return Ok(text.to_owned());
    }
    let ran = crate::shell_lint::bounded(std::process::Command::new("shellcheck").arg("--version"))
        .map_err(|e| format!("shellcheck --version: {e}"))?;
    let text = String::from_utf8_lossy(&ran.stdout).into_owned();
    if let (Some(c), Some(k), Some(0)) = (cache.as_ref(), key.as_ref(), ran.code) {
        c.put(k, &serde_json::Value::String(text.clone()));
    }
    Ok(text)
}
