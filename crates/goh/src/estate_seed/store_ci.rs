//! A store entry for a repository whose CI runs goh (GOH.STORECI): see the parent's WHERE AN ENTRY
//! LIVES. A runner cannot read the private store, so the entry is refused, naming the repository.

use std::path::{Path, PathBuf};

use super::parse::Raw;

/// The day a store entry for a repository whose CI runs goh turns from a warning into a refusal:
/// 2026-11-16T00:00:00Z, a week after GOH.STORECI landed (seconds since the epoch).
pub const STORE_CI_REFUSED_FROM: u64 = 1_794_787_200;

/// Matches a workflow line that brings goh into a run: `repository: o/gates_of_heck` (a checkout),
/// `github.com/o/gates_of_heck` (a clone), `uses: o/gates_of_heck/...` (its actions).
const CI_RUNS_GOH: &str = r"(?:github\.com[/:]|repository:\s*|uses:\s*)[\w.-]+/gates_of_heck\b";

/// The workflow under `repo/.github/workflows` that runs goh, if any (its file name).
#[must_use]
pub fn goh_workflow(repo: &Path) -> Option<String> {
    let re = regex::Regex::new(CI_RUNS_GOH).ok()?;
    let mut files: Vec<PathBuf> = std::fs::read_dir(repo.join(".github").join("workflows"))
        .ok()?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|x| x == "yml" || x == "yaml"))
        .collect();
    files.sort();
    files.into_iter().find_map(|f| {
        let text = std::fs::read_to_string(&f).ok()?;
        text.lines()
            .filter(|l| !l.trim_start().starts_with('#'))
            .any(|l| re.is_match(l.split(" #").next().unwrap_or(l)))
            .then(|| f.file_name().map(|n| n.to_string_lossy().into_owned()))
            .flatten()
    })
}

/// Refuse (from `STORE_CI_REFUSED_FROM`; warn before) the store entries `stored` that are about the
/// repository at `repo` when its CI runs goh: a runner cannot read the store.
pub(super) fn refuse(repo: &Path, file: &Path, stored: &[Raw], now: u64) -> Result<(), String> {
    // The workflow first: a directory read, where the root costs a git spawn.
    let Some(workflow) = goh_workflow(repo) else {
        return Ok(());
    };
    let Some(own) = crate::gitutil::root_commit(repo) else {
        return Ok(());
    };
    let mine: Vec<&Raw> = stored.iter().filter(|e| e.root == own).collect();
    let Some(first) = mine.first() else {
        return Ok(());
    };
    let name = repo
        .file_name()
        .map_or_else(|| first.repo.clone(), |n| n.to_string_lossy().into_owned());
    let msg = format!(
        "{name} runs goh in GitHub CI (.github/workflows/{workflow}), which cannot read {}: its {} \
         there ({}{}) would pass here and go red on its next push -- move {} into {name}'s own \
         tracked {} (GOH.STORECI)",
        file.display(),
        goh_tui::counted_as(mine.len(), "entry", "entries"),
        first.path,
        if mine.len() > 1 { ", ..." } else { "" },
        if mine.len() > 1 { "them" } else { "it" },
        file.file_name().map_or_else(String::new, |n| n.to_string_lossy().into_owned()),
    );
    if now >= STORE_CI_REFUSED_FROM {
        return Err(msg);
    }
    goh_tui::eoutln!(
        "{} {msg}; a warning until 2026-11-16, refused from then",
        goh_tui::WARN
    );
    Ok(())
}
