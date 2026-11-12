//! Reading a seed file: the store's from disk, the tracked one at the run's scope (GOH.SEEDSCOPE).

use super::Kind;
use std::path::Path;

/// One entry as a file spells it.
pub(super) struct Raw {
    pub(super) root: String,
    pub(super) repo: String,
    pub(super) path: String,
    pub(super) count: usize,
}

/// The entries of the seed file at `path`. A missing file holds none; a corrupt one is an error.
pub(super) fn parse(path: &Path, kind: Kind) -> Result<Vec<Raw>, String> {
    parse_text(path, std::fs::read_to_string(path).ok(), kind)
}

/// The repository's TRACKED seed file as the run's scope sees it: at a commit, the index's copy --
/// the one the commit records -- never an unstaged edit in the worktree (GOH.SEEDSCOPE). A
/// worktree with no copy spends no git spawn: a repository with no seed file asks nothing.
pub(super) fn tracked_text(repo: &Path, kind: Kind, staged: bool) -> Option<String> {
    let on_disk = repo.join(kind.file);
    if !staged || !on_disk.is_file() {
        return std::fs::read_to_string(on_disk).ok();
    }
    let index = crate::gitutil::content_bytes(repo, kind.file, true)?;
    String::from_utf8(index).ok()
}

pub(super) fn parse_text(
    path: &Path,
    text: Option<String>,
    kind: Kind,
) -> Result<Vec<Raw>, String> {
    let Some(text) = text else {
        return Ok(Vec::new());
    };
    let bad = |why: &str| format!("{} is not a {}: {why}", path.display(), kind.name);
    let v: serde_json::Value = serde_json::from_str(&text).map_err(|e| bad(&e.to_string()))?;
    let mut out = Vec::new();
    for e in v
        .get("entries")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| bad("no `entries` array"))?
    {
        let field = |k: &str| e.get(k).and_then(serde_json::Value::as_str);
        let (Some(root), Some(repo), Some(file), Some(count)) = (
            field("root"),
            field("repo"),
            field("path"),
            e.get("count").and_then(serde_json::Value::as_u64),
        ) else {
            return Err(bad("an entry lacks root, repo, path or count"));
        };
        if field("reason").is_none_or(str::is_empty) {
            return Err(bad(&format!("{repo} {file} has no reason")));
        }
        let count = usize::try_from(count).map_err(|e| bad(&e.to_string()))?;
        out.push(Raw {
            root: root.to_string(),
            repo: repo.to_string(),
            path: file.to_string(),
            count,
        });
    }
    Ok(out)
}
