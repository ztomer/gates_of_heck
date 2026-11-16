//! A store entry for a repository whose CI runs goh (GOH.STORECI): see the parent's WHERE AN ENTRY
//! LIVES. A runner cannot read the private store, so the entry is refused, naming the repository.

use std::path::{Path, PathBuf};

use super::{retire_store, store_dir, Kind, Seed};
use std::collections::BTreeMap;

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

/// GOH.STORECI, judged AFTER the findings (GOH.STORECISTALE): `seed`'s store entries about the
/// repository at `root` (root commit `repo`) when its CI runs goh, which a runner cannot read.
///
/// A full run (`full`) that finds an entry's file clean RETIRES it -- deleted from the store under
/// its lock (`retire_store`, the one retirement path GOH.SEEDPRUNE writes through), taken out of
/// `seed`, and said in the returned lines. Judged at load, before any finding was read, the
/// refusal fired on entries whose sites were already fixed (token-pool, 2026-11-16) and the lane
/// hand-deleted them. Any entry left is refused; a commit's run, which reads only what it changes,
/// names the full run that retires a fixed site.
///
/// # Errors
///
/// The refusal, naming the repository, its workflow and the store file, then any retired lines.
pub(super) fn judge(
    root: &Path,
    repo: &str,
    full: bool,
    counts: &BTreeMap<String, usize>,
    seed: &mut Seed,
    kind: Kind,
) -> Result<String, String> {
    if repo.is_empty() {
        return Ok(String::new());
    }
    let mine: Vec<String> = seed
        .range((repo.to_string(), String::new())..)
        .take_while(|((r, _), _)| r == repo)
        .filter(|(_, held)| held.stored)
        .map(|((_, path), _)| path.clone())
        .collect();
    if mine.is_empty() {
        return Ok(String::new());
    }
    // A directory read: the repository's identity is already known.
    let Some(workflow) = goh_workflow(root) else {
        return Ok(String::new());
    };
    let name = root
        .file_name()
        .map_or_else(|| repo.to_string(), |n| n.to_string_lossy().into_owned());
    let (stale, live): (Vec<String>, Vec<String>) = mine
        .into_iter()
        .partition(|p| full && counts.get(p).copied().unwrap_or(0) == 0);
    for p in &stale {
        seed.remove(&(repo.to_string(), p.clone()));
    }
    // Nothing to retire writes nothing and says nothing (`retire_store`).
    let gone: BTreeMap<String, usize> = stale.into_iter().map(|p| (p, 0)).collect();
    let said = retire_store(repo, kind, &gone, kind.name, |path, file| {
        format!(
            "{} {} {path}: no {} left, its entry retired from {} -- {name} runs goh in GitHub CI \
             (.github/workflows/{workflow}), which cannot read the store (GOH.STORECI, \
             GOH.STORECISTALE)",
            goh_tui::START,
            kind.name,
            kind.unit,
            file.display()
        )
    });
    let Some(first) = live.first() else {
        return Ok(said);
    };
    let file = store_dir().map_or_else(
        || kind.file.to_string(),
        |d| d.join(kind.file).display().to_string(),
    );
    let msg = format!(
        "{name} runs goh in GitHub CI (.github/workflows/{workflow}), which cannot read {file}: its \
         {} there ({first}{}) would pass here and go red on its next push -- move {} into {name}'s \
         own tracked {}{} (GOH.STORECI)",
        goh_tui::counted_as(live.len(), "entry", "entries"),
        if live.len() > 1 { ", ..." } else { "" },
        if live.len() > 1 { "them" } else { "it" },
        kind.file,
        if full {
            ""
        } else {
            "; an entry whose site is already fixed is retired by a full run, `goh structural \
             --full`"
        },
    );
    Err(format!("{msg}\n{said}").trim_end().to_string())
}
